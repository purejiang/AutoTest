// 镜像会话 composable：WS 生命周期 + SPS/PPS 捕获 + VideoDecoder 配置/解码 + fps 统计。
//
// 数据流：
//   mirrorWs(二进制帧) -> splitNals(缓存 SPS/PPS、识别 IDR) -> 串行化队列
//   -> MirrorDecoder.configure(Annex B 优先/avcC 回退) -> decode -> VideoFrame
//   -> CanvasRenderer.present(只画最新一帧)
//
// 串行化：所有帧处理都排进同一条 Promise 链，保证 configure/flush 与 decode 的先后顺序；
// 重连/停止时重置链并丢弃旧代（generation）任务，避免旧帧喂进新解码器。

import { computed, onBeforeUnmount, onMounted, ref, toValue, watch, type MaybeRefOrGetter, type Ref } from 'vue'

import { base64ToBytes, bytesEqual, nalType, splitNals } from '@/lib/h264'
import {
  createMirrorSocket,
  type MirrorCloseInfo,
  type MirrorControlMessage,
  type MirrorHello,
  type MirrorSocket,
} from '@/lib/mirrorWs'
import { MirrorDecoder, type MirrorDecoderMode } from '@/lib/webcodecs'
import { CanvasRenderer } from '../render/canvasRenderer'
import { errorMessage, get, post } from '../lib/api'
import type { MirrorHelloInfo, MirrorStats, MirrorStatusInfo, MirrorUiStatus } from '../types'

const INITIAL_STATS: MirrorStats = { fps: 0, dropped: 0, decoded: 0, queueSize: 0 }
const CONFIGURE_RETRY_GAP_MS = 2000

export interface UseMirrorSessionOptions {
  serial: MaybeRefOrGetter<string>
  token?: MaybeRefOrGetter<string | undefined>
  canvasRef: Ref<HTMLCanvasElement | null>
}

export interface MirrorSession {
  status: Ref<MirrorUiStatus>
  hello: Ref<MirrorHelloInfo | null>
  stats: Ref<MirrorStats>
  error: Ref<string | null>
  decoderMode: Ref<MirrorDecoderMode | null>
  isSupported: boolean
  startMirror: () => Promise<void>
  stopMirror: () => Promise<void>
  reconnect: () => void
  requestKeyframe: () => void
  sendTouch: (action: 'down' | 'move' | 'up', nx: number, ny: number) => boolean
}

function toHelloInfo(msg: MirrorHello, fallbackSerial: string): MirrorHelloInfo {
  return {
    serial: typeof msg.serial === 'string' ? msg.serial : fallbackSerial,
    sessionId: typeof msg.session_id === 'string' ? msg.session_id : '',
    width: Number(msg.width) || 0,
    height: Number(msg.height) || 0,
    rotation: Number(msg.rotation) || 0,
    codec: typeof msg.codec === 'string' ? msg.codec : '',
    maxSize: typeof msg.max_size === 'number' ? msg.max_size : undefined,
    maxFps: typeof msg.max_fps === 'number' ? msg.max_fps : undefined,
    videoBitRate: typeof msg.video_bit_rate === 'number' ? msg.video_bit_rate : undefined,
  }
}

function closeFrame(frame: VideoFrame): void {
  try {
    frame.close()
  } catch {
    /* noop */
  }
}

export function useMirrorSession(options: UseMirrorSessionOptions): MirrorSession {
  const serial = computed(() => toValue(options.serial))
  const token = computed(() => toValue(options.token))
  const isSupported = MirrorDecoder.isSupported()

  const status = ref<MirrorUiStatus>('idle')
  const hello = ref<MirrorHelloInfo | null>(null)
  const stats = ref<MirrorStats>({ ...INITIAL_STATS })
  const error = ref<string | null>(null)
  const decoderMode = ref<MirrorDecoderMode | null>(null)

  let disposed = false
  let socketRef: MirrorSocket | null = null
  let decoderRef: MirrorDecoder | null = null
  let rendererRef: CanvasRenderer | null = null
  let startedRef = false
  let presentedRef = 0
  let serverStatsRef: { fps?: number; dropped?: number } = {}
  let controlsRef: { connect: () => void; teardown: (resetToIdle: boolean) => void } | null = null
  let cleanupSession: (() => void) | null = null
  let stopSessionWatch: (() => void) | null = null
  let statsTimer: number | undefined

  const isAlive = (): boolean => !disposed

  function setupStatsTimer(): void {
    let lastTick = typeof performance !== 'undefined' ? performance.now() : Date.now()
    let lastPresented = 0
    statsTimer = window.setInterval(() => {
      if (!isAlive()) return
      const now = typeof performance !== 'undefined' ? performance.now() : Date.now()
      const elapsed = Math.max(1, now - lastTick)
      const presented = presentedRef
      const fps = Math.round(((presented - lastPresented) * 1000) / elapsed)
      lastPresented = presented
      lastTick = now
      const decoderStats = decoderRef?.stats
      stats.value = {
        fps,
        dropped: (serverStatsRef.dropped ?? 0) + (decoderStats?.dropped ?? 0),
        decoded: decoderStats?.decoded ?? 0,
        serverFps: serverStatsRef.fps,
        queueSize: decoderStats?.queueSize ?? 0,
      }
    }, 1000)
  }

  function attachRenderer(): void {
    const canvas = options.canvasRef.value
    if (!canvas || !isSupported) return
    if (rendererRef) return
    const renderer = new CanvasRenderer({
      onFrameDrawn: () => {
        presentedRef += 1
        if (status.value !== 'streaming') status.value = 'streaming'
      },
    })
    renderer.attach(canvas, canvas.parentElement)
    rendererRef = renderer
  }

  function setupSession(): void {
    const sessionSerial = serial.value
    if (!isSupported) {
      status.value = 'error'
      error.value =
        '当前浏览器不支持 WebCodecs（VideoDecoder）。请使用新版 Chrome/Edge；或改用「真机录制」模式在 /recorder 页面操作。'
      return
    }
    if (!sessionSerial) {
      status.value = 'idle'
      return
    }

    let sessionDisposed = false
    let socket: MirrorSocket | null = null
    let decoder: MirrorDecoder | null = null
    let sps: Uint8Array | null = null
    let pps: Uint8Array | null = null
    let needsConfigure = true
    let awaitingKeyframe = true
    let lastKeyframeRequestAt = 0
    const KEYFRAME_REQUEST_MIN_GAP_MS = 1200
    let generation = 0
    let chain: Promise<void> = Promise.resolve()
    let lastConfigureErrorAt = 0
    let lastConfigureErrorShown = false

    const isSessionAlive = (): boolean => !sessionDisposed && isAlive()
    const setStatusSafe = (next: MirrorUiStatus): void => {
      if (isSessionAlive()) status.value = next
    }
    const reportError = (message: string): void => {
      if (isSessionAlive()) error.value = message
    }
    const requestKeyframeThrottled = (): void => {
      const now = Date.now()
      if (now - lastKeyframeRequestAt < KEYFRAME_REQUEST_MIN_GAP_MS) return
      lastKeyframeRequestAt = now
      socket?.requestKeyframe()
    }
    const enqueue = (task: () => void | Promise<void>): void => {
      chain = chain.then(task).catch((err) => {
        if (isSessionAlive()) console.warn('[mirror] 帧管线异常', err)
      })
    }

    const ensureConfigured = async (): Promise<boolean> => {
      if (!decoder || !sps) return false
      if (lastConfigureErrorShown && Date.now() - lastConfigureErrorAt < CONFIGURE_RETRY_GAP_MS) {
        return false
      }
      try {
        const mode = await decoder.configure({ sps, pps: pps ?? undefined })
        if (!isSessionAlive()) return false
        needsConfigure = false
        awaitingKeyframe = true
        lastConfigureErrorShown = false
        decoderMode.value = mode
        return true
      } catch (err) {
        if (!isSessionAlive()) return false
        lastConfigureErrorAt = Date.now()
        if (!pps) {
          // Annex B 模式可能暂时不可用；PPS 还在码流里，等它到达后自动重试。
          return false
        }
        if (!lastConfigureErrorShown) {
          lastConfigureErrorShown = true
          reportError(`解码器配置失败：${errorMessage(err)}`)
        }
        socket?.requestKeyframe()
        return false
      }
    }

    const processFrame = async (
      nals: Uint8Array[],
      keyframe: boolean,
      ptsUs: number,
      payload: Uint8Array,
      gen: number,
    ): Promise<void> => {
      if (!isSessionAlive() || gen !== generation || !decoder) return
      if (!decoder.isConfigured || needsConfigure) {
        const ok = await ensureConfigured()
        if (!ok || !isSessionAlive() || gen !== generation || !decoder) return
      }
      if (!decoder.isConfigured) return
      if (awaitingKeyframe && !keyframe) return
      if (keyframe) awaitingKeyframe = false
      decoder.decode(payload, ptsUs, { keyframe, nals })
    }

    const handleFrame = (payload: Uint8Array, ptsUs: number): void => {
      if (!isSessionAlive()) return
      const nals = splitNals(payload)
      if (nals.length === 0) return
      let keyframe = false
      for (const nal of nals) {
        const type = nalType(nal)
        if (type === 7) {
          if (!bytesEqual(nal, sps)) {
            sps = nal.slice()
            needsConfigure = true
          }
        } else if (type === 8) {
          if (!bytesEqual(nal, pps)) {
            pps = nal.slice()
            if (!decoder?.isConfigured) needsConfigure = true
          }
        } else if (type === 5) {
          keyframe = true
        }
      }
      const gen = generation
      enqueue(() => processFrame(nals, keyframe, ptsUs, payload, gen))
    }

    const applyParameterSets = (spsB64: unknown, ppsB64: unknown): void => {
      if (typeof spsB64 === 'string' && spsB64) {
        try {
          const nextSps = base64ToBytes(spsB64)
          if (nextSps.length > 0 && !bytesEqual(nextSps, sps)) {
            sps = nextSps.slice()
            needsConfigure = true
          }
        } catch {
          /* hello 里的 SPS 解析失败不致命：码流里通常还会带 SPS/PPS */
        }
      }
      if (typeof ppsB64 === 'string' && ppsB64) {
        try {
          const nextPps = base64ToBytes(ppsB64)
          if (nextPps.length > 0 && !bytesEqual(nextPps, pps)) {
            pps = nextPps.slice()
            if (!decoder?.isConfigured) needsConfigure = true
          }
        } catch {
          /* 忽略坏 PPS */
        }
      }
    }

    const handleHello = (msg: MirrorHello): void => {
      if (!isSessionAlive()) return
      hello.value = toHelloInfo(msg, sessionSerial)
      applyParameterSets(msg.sps_b64, msg.pps_b64)
      error.value = null
      awaitingKeyframe = true
      setStatusSafe('waiting-keyframe')
      socket?.requestKeyframe()
    }

    const handleControl = (msg: MirrorControlMessage): void => {
      if (!isSessionAlive()) return
      switch (msg.kind) {
        case 'config': {
          const prev = hello.value
          if (prev) {
            const next: MirrorHelloInfo = { ...prev }
            if (typeof msg.width === 'number') next.width = msg.width
            if (typeof msg.height === 'number') next.height = msg.height
            if (typeof msg.rotation === 'number') next.rotation = msg.rotation
            if (typeof msg.codec === 'string') next.codec = msg.codec
            if (typeof msg.max_size === 'number') next.maxSize = msg.max_size
            if (typeof msg.max_fps === 'number') next.maxFps = msg.max_fps
            if (typeof msg.video_bit_rate === 'number') next.videoBitRate = msg.video_bit_rate
            hello.value = next
          }
          applyParameterSets(msg.sps_b64, msg.pps_b64)
          awaitingKeyframe = true
          setStatusSafe('waiting-keyframe')
          // 不要在 config 时请求关键帧：后端编码器已重置，请求会造成重置循环。
          break
        }
        case 'flush': {
          awaitingKeyframe = true
          setStatusSafe('waiting-keyframe')
          const gen = generation
          enqueue(async () => {
            if (!isSessionAlive() || gen !== generation || !decoder) return
            await decoder.flush(typeof msg.reason === 'string' ? msg.reason : undefined)
          })
          break
        }
        case 'error': {
          const code = typeof msg.code === 'string' ? msg.code : 'MIRROR_ERROR'
          const message = typeof msg.message === 'string' ? msg.message : '镜像服务错误'
          reportError(`${code}：${message}`)
          setStatusSafe('error')
          break
        }
        case 'stats': {
          serverStatsRef = {
            fps: typeof msg.fps === 'number' ? msg.fps : undefined,
            dropped: typeof msg.dropped === 'number' ? msg.dropped : undefined,
          }
          break
        }
        default:
          break
      }
    }

    const handleOpen = (): void => {
      if (!isSessionAlive()) return
      setStatusSafe('connecting')
      awaitingKeyframe = true
      socket?.requestKeyframe()
    }

    const handleClose = (info: MirrorCloseInfo): void => {
      if (!isSessionAlive() || info.manual) return
      awaitingKeyframe = true
      if (info.willReconnect) {
        setStatusSafe('reconnecting')
      } else {
        reportError('连接已断开（页面隐藏时暂停自动重连），回到页面后会自动恢复。')
        setStatusSafe('error')
      }
    }

    const connect = (): void => {
      if (!isSessionAlive()) return
      try {
        socket?.close()
      } catch {
        /* noop */
      }
      socket = null
      socketRef = null
      try {
        decoder?.close()
      } catch {
        /* noop */
      }
      decoder = null
      decoderRef = null
      sps = null
      pps = null
      needsConfigure = true
      awaitingKeyframe = true
      generation += 1
      chain = Promise.resolve()
      lastConfigureErrorShown = false
      decoderMode.value = null
      setStatusSafe('connecting')
      error.value = null
      rendererRef?.clear()

      try {
        decoder = new MirrorDecoder({
          onFrame: (frame) => {
            const renderer = rendererRef
            if (renderer) renderer.present(frame)
            else closeFrame(frame)
          },
          onError: (err) => {
            if (!isSessionAlive()) return
            awaitingKeyframe = true
            if (status.value === 'streaming') status.value = 'waiting-keyframe'
            reportError(`解码错误：${err.message}`)
            requestKeyframeThrottled()
          },
        })
        decoderRef = decoder
      } catch (err) {
        reportError(`创建解码器失败：${errorMessage(err)}`)
        setStatusSafe('error')
        return
      }

      try {
        socket = createMirrorSocket({
          serial: sessionSerial,
          token: token.value,
          onOpen: handleOpen,
          onHello: handleHello,
          onFrame: handleFrame,
          onControl: handleControl,
          onClose: handleClose,
          onError: () => undefined,
        })
        socketRef = socket
      } catch (err) {
        reportError(`建立镜像连接失败：${errorMessage(err)}`)
        setStatusSafe('error')
      }
    }

    const teardown = (resetToIdle: boolean): void => {
      generation += 1
      chain = Promise.resolve()
      awaitingKeyframe = true
      try {
        socket?.close()
      } catch {
        /* noop */
      }
      socket = null
      socketRef = null
      try {
        decoder?.close()
      } catch {
        /* noop */
      }
      decoder = null
      decoderRef = null
      rendererRef?.clear()
      if (resetToIdle && isSessionAlive()) {
        decoderMode.value = null
        hello.value = null
        stats.value = { ...INITIAL_STATS }
        status.value = 'idle'
      }
    }

    const boot = async (): Promise<void> => {
      hello.value = null
      stats.value = { ...INITIAL_STATS }
      decoderMode.value = null
      error.value = null
      status.value = 'checking'
      try {
        const info = await get<MirrorStatusInfo>(`/devices/${encodeURIComponent(sessionSerial)}/mirror/status`)
        if (!isSessionAlive()) return
        if (info && info.active === true) {
          startedRef = true
          const width = Number(info.width)
          const height = Number(info.height)
          if (width > 0 && height > 0) {
            hello.value = {
              serial: sessionSerial,
              sessionId: typeof info.session_id === 'string' ? info.session_id : '',
              width,
              height,
              rotation: Number(info.rotation) || 0,
              codec: typeof info.codec === 'string' ? info.codec : '',
              maxSize: typeof info.max_size === 'number' ? info.max_size : undefined,
              maxFps: typeof info.max_fps === 'number' ? info.max_fps : undefined,
              videoBitRate: typeof info.video_bit_rate === 'number' ? info.video_bit_rate : undefined,
            }
          }
          connect()
        } else {
          startedRef = false
          status.value = 'idle'
        }
      } catch (err) {
        if (!isSessionAlive()) return
        startedRef = false
        status.value = 'idle'
        error.value = `获取镜像状态失败：${errorMessage(err)}。后端可能未启动，可点击“启动镜像”重试。`
      }
    }

    controlsRef = { connect, teardown }
    void boot()

    cleanupSession = () => {
      sessionDisposed = true
      teardown(false)
      if (controlsRef && controlsRef.connect === connect) controlsRef = null
    }
  }

  onMounted(() => {
    attachRenderer()
    setupStatsTimer()
    stopSessionWatch = watch(
      () => [serial.value, token.value] as const,
      () => {
        cleanupSession?.()
        cleanupSession = null
        setupSession()
      },
      { immediate: true },
    )
  })

  onBeforeUnmount(() => {
    disposed = true
    stopSessionWatch?.()
    stopSessionWatch = null
    cleanupSession?.()
    cleanupSession = null
    if (statsTimer !== undefined) window.clearInterval(statsTimer)
    rendererRef?.destroy()
    rendererRef = null
  })

  async function startMirror(): Promise<void> {
    const sessionSerial = serial.value
    if (!sessionSerial || !isSupported) return
    status.value = 'starting'
    error.value = null
    try {
      await post<void>(`/devices/${encodeURIComponent(sessionSerial)}/mirror/start`)
      startedRef = true
      if (isAlive()) controlsRef?.connect()
    } catch (err) {
      if (!isAlive()) return
      status.value = 'error'
      error.value = `启动镜像失败：${errorMessage(err)}`
    }
  }

  async function stopMirror(): Promise<void> {
    const sessionSerial = serial.value
    if (!sessionSerial) return
    try {
      await post<void>(`/devices/${encodeURIComponent(sessionSerial)}/mirror/stop`)
    } catch (err) {
      if (isAlive()) error.value = `停止镜像失败：${errorMessage(err)}`
    }
    startedRef = false
    controlsRef?.teardown(true)
  }

  function reconnect(): void {
    if (!isSupported) return
    error.value = null
    if (startedRef) {
      controlsRef?.connect()
    } else {
      void startMirror()
    }
  }

  function requestKeyframe(): void {
    socketRef?.requestKeyframe()
  }

  function sendTouch(action: 'down' | 'move' | 'up', nx: number, ny: number): boolean {
    return socketRef?.sendTouch(action, nx, ny) ?? false
  }

  return {
    status,
    hello,
    stats,
    error,
    decoderMode,
    isSupported,
    startMirror,
    stopMirror,
    reconnect,
    requestKeyframe,
    sendTouch,
  }
}
