// WebCodecs H.264 解码封装（契约 §6.2 / §10）。
//
// 两条配置路径（兼容性关键）：
//   1) Annex B 模式：{ codec, optimizeForLatency: true }，不带 description。
//      Chrome/Edge 在 description 缺省时按 Annex B（00 00 01 起始码）解析，直接喂 scrcpy 帧。
//   2) avcC 模式：Annex B 模式被判定不支持/configure 失败时，用 buildAvcC(SPS,PPS) 作为
//      description 重新 configure；此时码流必须转成 AVCC（4 字节长度前缀），由 decode() 内部完成。
//
// 行为约定：
//   - 解码输出的是 VideoFrame；onFrame 的消费方必须负责 frame.close()（渲染层只画最新帧并关闭旧帧）。
//   - flush()：清空待解码队列 -> 丢弃重建 VideoDecoder -> 用缓存的 SPS/PPS 重新 configure，
//     调用方随后应把状态置为“等待关键帧”，直到下一个 IDR 到达。
//   - 解码错误：decoder 会被 UA 关闭；本类上报错误并丢弃内部实例，下次 configure() 会重建。
//     上层收到 onError 后应 requestKeyframe()，等新的 IDR 触发重配。

import {
  buildAvcC,
  codecString,
  isKeyframeFrame,
  nalsToAvcc,
  parseSps,
  splitNals,
  stripStartCode,
  type SpsInfo,
} from './h264'

export type MirrorDecoderMode = 'annexb' | 'avcc'

export interface MirrorDecoderConfig {
  /** 裸 SPS NAL（可带起始码，内部会剥离） */
  sps: Uint8Array
  /** 裸 PPS NAL；Annex B 模式可缺省，avcC 回退路径必需 */
  pps?: Uint8Array
}

export type MirrorDecoderErrorCode =
  | 'UNSUPPORTED'
  | 'CONFIGURE_FAILED'
  | 'DECODE_ERROR'
  | 'FLUSH_FAILED'
  | 'CLOSED'

export class MirrorDecoderError extends Error {
  readonly code: MirrorDecoderErrorCode
  override readonly cause?: unknown

  constructor(code: MirrorDecoderErrorCode, message: string, cause?: unknown) {
    super(message)
    this.name = 'MirrorDecoderError'
    this.code = code
    this.cause = cause
  }
}

export interface MirrorDecoderOptions {
  /** 每解出一帧回调（消费方必须 close 该 VideoFrame） */
  onFrame: (frame: VideoFrame) => void
  onError?: (err: MirrorDecoderError) => void
  /** 解码队列积压阈值：超过且当前帧不是关键帧时直接丢弃（默认 8） */
  maxQueueSize?: number
}

export interface DecodeOptions {
  /** 是否关键帧；不传则内部扫描 NAL 判断 */
  keyframe?: boolean
  /** 已经切好的 NAL 列表（上层已 split 时传，避免重复扫描） */
  nals?: Uint8Array[]
}

export type DecodeResult = 'decoded' | 'dropped' | 'not-configured'

export interface MirrorDecoderStats {
  decoded: number
  dropped: number
  queueSize: number
}

export class MirrorDecoder {
  /** WebCodecs 能力检测：'VideoDecoder' in window */
  static isSupported(): boolean {
    try {
      return typeof VideoDecoder !== 'undefined' && typeof VideoDecoder === 'function'
    } catch {
      return false
    }
  }

  private readonly options: MirrorDecoderOptions
  private readonly maxQueueSize: number
  private decoder: VideoDecoder | null = null
  private modeValue: MirrorDecoderMode | null = null
  private configValue: MirrorDecoderConfig | null = null
  private spsInfoValue: SpsInfo | null = null
  private generation = 0
  private closed = false
  private lastTimestamp = -1
  private decodedCount = 0
  private droppedCount = 0
  private lastConfigureError: unknown = null

  constructor(options: MirrorDecoderOptions) {
    this.options = options
    this.maxQueueSize = options.maxQueueSize ?? 8
  }

  get mode(): MirrorDecoderMode | null {
    return this.modeValue
  }

  get isConfigured(): boolean {
    return !this.closed && this.decoder !== null && this.decoder.state === 'configured'
  }

  get spsInfo(): SpsInfo | null {
    return this.spsInfoValue
  }

  get stats(): MirrorDecoderStats {
    return {
      decoded: this.decodedCount,
      dropped: this.droppedCount,
      queueSize: this.decoder?.decodeQueueSize ?? 0,
    }
  }

  /**
   * 尝试配置解码器：Annex B 优先，失败回退 avcC description。
   * 返回最终生效的模式；两条路径都失败时抛 MirrorDecoderError（code=CONFIGURE_FAILED）。
   */
  async configure(config: MirrorDecoderConfig): Promise<MirrorDecoderMode> {
    if (this.closed) throw new MirrorDecoderError('CLOSED', '解码器已关闭')
    if (!MirrorDecoder.isSupported()) {
      throw new MirrorDecoderError('UNSUPPORTED', '当前浏览器不支持 WebCodecs VideoDecoder')
    }

    const sps = stripStartCode(config.sps)
    const pps = config.pps ? stripStartCode(config.pps) : undefined
    const spsInfo = parseSps(sps) // 顺带做合法性校验
    const codec = codecString(spsInfo)

    const gen = ++this.generation
    this.configValue = { sps, pps }
    this.spsInfoValue = spsInfo
    this.lastConfigureError = null

    const candidates: Array<{ mode: MirrorDecoderMode; config: VideoDecoderConfig }> = [
      { mode: 'annexb', config: { codec, optimizeForLatency: true } },
    ]
    if (pps) {
      candidates.push({
        mode: 'avcc',
        config: { codec, description: buildAvcC(sps, pps), optimizeForLatency: true },
      })
    }

    let lastError: unknown = null
    for (const candidate of candidates) {
      if (this.closed || gen !== this.generation) {
        throw new MirrorDecoderError('CLOSED', '解码器配置被新的配置请求取代')
      }
      let supported = true
      try {
        const support = await VideoDecoder.isConfigSupported(candidate.config)
        supported = support.supported !== false
      } catch (err) {
        supported = false
        lastError = err
      }
      if (this.closed || gen !== this.generation) {
        throw new MirrorDecoderError('CLOSED', '解码器配置被新的配置请求取代')
      }
      if (!supported) continue
      if (this.tryConfigure(candidate.config)) {
        this.modeValue = candidate.mode
        this.lastTimestamp = -1
        return candidate.mode
      }
      lastError = this.lastConfigureError
    }

    this.modeValue = null
    this.disposeDecoder()
    const suffix = pps ? '' : '（无 PPS，无法尝试 avcC 回退；等码流里出现 PPS 后会重试）'
    const err = new MirrorDecoderError(
      'CONFIGURE_FAILED',
      `H.264 解码器配置失败（codec=${codec}）${suffix}${lastError instanceof Error ? `：${lastError.message}` : ''}`,
      lastError,
    )
    this.emitError(err)
    throw err
  }

  private tryConfigure(config: VideoDecoderConfig): boolean {
    this.disposeDecoder()
    let next: VideoDecoder
    try {
      next = new VideoDecoder({
        output: (frame) => this.handleOutput(frame),
        error: (error) => this.handleDecoderError(error),
      })
    } catch (err) {
      this.lastConfigureError = err
      return false
    }
    try {
      next.configure(config)
    } catch (err) {
      this.lastConfigureError = err
      try {
        next.close()
      } catch {
        /* noop */
      }
      return false
    }
    this.decoder = next
    return true
  }

  /**
   * 喂一帧（Annex B 数据）。
   * avcC 模式下内部自动转成长度前缀格式；丢弃队列积压时的非关键帧以保低延迟。
   */
  decode(payload: Uint8Array, ptsUs: number, options: DecodeOptions = {}): DecodeResult {
    if (this.closed) return 'not-configured'
    const decoder = this.decoder
    if (!decoder || decoder.state !== 'configured') return 'not-configured'

    const nals = options.nals ?? splitNals(payload)
    if (nals.length === 0) return 'dropped'
    const keyframe = options.keyframe ?? isKeyframeFrame(nals)

    if (!keyframe && decoder.decodeQueueSize > this.maxQueueSize) {
      this.droppedCount += 1
      return 'dropped'
    }

    const data = this.modeValue === 'avcc' ? nalsToAvcc(nals) : payload
    if (data.byteLength === 0) {
      this.droppedCount += 1
      return 'dropped'
    }

    let timestamp = Math.round(ptsUs)
    if (!Number.isFinite(timestamp) || timestamp < 0) timestamp = 0
    // WebCodecs 要求时间戳单调不降；服务端重启/兜底估计可能回退，这里做一次纠正。
    if (timestamp <= this.lastTimestamp) timestamp = this.lastTimestamp + 1

    try {
      decoder.decode(
        new EncodedVideoChunk({
          type: keyframe ? 'key' : 'delta',
          timestamp,
          data,
        }),
      )
      this.lastTimestamp = timestamp
      return 'decoded'
    } catch (err) {
      this.droppedCount += 1
      this.emitError(
        new MirrorDecoderError(
          'DECODE_ERROR',
          `提交解码失败：${err instanceof Error ? err.message : String(err)}`,
          err,
        ),
      )
      return 'dropped'
    }
  }

  /**
   * flush：清空待解码队列、重建解码器并重新 configure，等待下一个 IDR。
   * 对应控制消息 {"kind":"flush","reason":"keyframe_drop"}。
   */
  async flush(reason?: string): Promise<void> {
    if (this.closed || !this.configValue) return
    const config = this.configValue
    this.generation += 1
    this.disposeDecoder()
    this.modeValue = null
    try {
      await this.configure(config)
    } catch (err) {
      const wrapped =
        err instanceof MirrorDecoderError
          ? err
          : new MirrorDecoderError('FLUSH_FAILED', `flush 重新配置失败：${String(reason ?? '')}`, err)
      if (wrapped.code !== 'CONFIGURE_FAILED') this.emitError(wrapped)
    }
  }

  /** 关闭并释放解码器（幂等）。 */
  close(): void {
    if (this.closed) return
    this.closed = true
    this.generation += 1
    this.disposeDecoder()
    this.modeValue = null
    this.configValue = null
  }

  private disposeDecoder(): void {
    const decoder = this.decoder
    this.decoder = null
    if (!decoder) return
    try {
      if (decoder.state !== 'closed') decoder.close()
    } catch {
      /* noop */
    }
  }

  private handleOutput(frame: VideoFrame): void {
    if (this.closed) {
      try {
        frame.close()
      } catch {
        /* noop */
      }
      return
    }
    this.decodedCount += 1
    try {
      this.options.onFrame(frame)
    } catch (err) {
      try {
        frame.close()
      } catch {
        /* noop */
      }
      this.emitError(
        new MirrorDecoderError(
          'DECODE_ERROR',
          `渲染回调抛错：${err instanceof Error ? err.message : String(err)}`,
          err,
        ),
      )
    }
  }

  private handleDecoderError(error: DOMException): void {
    // UA 在解码错误后会把 VideoDecoder 置为 closed；丢弃内部实例，等上层重配。
    this.decoder = null
    this.modeValue = null
    this.emitError(
      new MirrorDecoderError(
        'DECODE_ERROR',
        `视频解码错误：${error?.message ?? 'unknown'}`,
        error,
      ),
    )
  }

  private emitError(err: MirrorDecoderError): void {
    if (this.closed) return
    try {
      this.options.onError?.(err)
    } catch {
      /* 错误回调本身不再向外抛 */
    }
  }
}
