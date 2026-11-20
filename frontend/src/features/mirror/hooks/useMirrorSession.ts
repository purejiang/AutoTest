// 镜像会话 hook：WS 生命周期 + SPS/PPS 捕获 + VideoDecoder 配置/解码 + fps 统计。
//
// 数据流：
//   mirrorWs(二进制帧) -> splitNals(缓存 SPS/PPS、识别 IDR) -> 串行化队列
//   -> MirrorDecoder.configure(Annex B 优先/avcC 回退) -> decode -> VideoFrame
//   -> CanvasRenderer.present(只画最新一帧)
//
// 串行化：所有帧处理都排进同一条 Promise 链，保证 configure/flush 与 decode 的先后顺序；
// 重连/停止时重置链并丢弃旧代（generation）任务，避免旧帧喂进新解码器。

import { useCallback, useEffect, useRef, useState } from 'react'
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
  serial: string
  token?: string
  canvasRef: React.RefObject<HTMLCanvasElement>
}

export interface MirrorSession {
  status: MirrorUiStatus
  hello: MirrorHelloInfo | null
  stats: MirrorStats
  error: string | null
  decoderMode: MirrorDecoderMode | null
  isSupported: boolean
  startMirror: () => Promise<void>
  stopMirror: () => Promise<void>
  reconnect: () => void
  requestKeyframe: () => void
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
  const { serial, token, canvasRef } = options
  const isSupported = MirrorDecoder.isSupported()

  const [status, setStatus] = useState<MirrorUiStatus>('idle')
  const [hello, setHello] = useState<MirrorHelloInfo | null>(null)
  const [stats, setStats] = useState<MirrorStats>(INITIAL_STATS)
  const [error, setError] = useState<string | null>(null)
  const [decoderMode, setDecoderMode] = useState<MirrorDecoderMode | null>(null)

  const mountedRef = useRef(true)
  const socketRef = useRef<MirrorSocket | null>(null)
  const decoderRef = useRef<MirrorDecoder | null>(null)
  const rendererRef = useRef<CanvasRenderer | null>(null)
  const startedRef = useRef(false)
  const presentedRef = useRef(0)
  const serverStatsRef = useRef<{ fps?: number; dropped?: number }>({})
  const controlsRef = useRef<{
    connect: () => void
    teardown: (resetToIdle: boolean) => void
  } | null>(null)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  // ---- fps / 统计（跨连接，每秒刷新；服务端 stats 消息覆盖 dropped/serverFps） ----
  useEffect(() => {
    let lastTick = typeof performance !== 'undefined' ? performance.now() : Date.now()
    let lastPresented = 0
    const timer = setInterval(() => {
      const now = typeof performance !== 'undefined' ? performance.now() : Date.now()
      const elapsed = Math.max(1, now - lastTick)
      const presented = presentedRef.current
      const fps = Math.round(((presented - lastPresented) * 1000) / elapsed)
      lastPresented = presented
      lastTick = now
      const decoderStats = decoderRef.current?.stats
      setStats({
        fps,
        dropped: (serverStatsRef.current.dropped ?? 0) + (decoderStats?.dropped ?? 0),
        decoded: decoderStats?.decoded ?? 0,
        serverFps: serverStatsRef.current.fps,
        queueSize: decoderStats?.queueSize ?? 0,
      })
    }, 1000)
    return () => clearInterval(timer)
  }, [])

  // ---- 画布渲染器生命周期 ----
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !isSupported) return
    const renderer = new CanvasRenderer({
      onFrameDrawn: () => {
        presentedRef.current += 1
        setStatus((prev) => (prev === 'streaming' ? prev : 'streaming'))
      },
    })
    renderer.attach(canvas, canvas.parentElement)
    rendererRef.current = renderer
    return () => {
      renderer.destroy()
      rendererRef.current = null
    }
  }, [canvasRef, isSupported])

  // ---- 会话主体：状态查询 -> 建连 -> 帧管线 ----
  useEffect(() => {
    if (!isSupported) {
      setStatus('error')
      setError('当前浏览器不支持 WebCodecs（VideoDecoder）。请使用新版 Chrome/Edge；或改用“物理录制”模式在 /recorder 页面操作。')
      return
    }
    if (!serial) {
      setStatus('idle')
      return
    }

    let disposed = false
    let socket: MirrorSocket | null = null
    let decoder: MirrorDecoder | null = null
    let sps: Uint8Array | null = null
    let pps: Uint8Array | null = null
    let needsConfigure = true
    let awaitingKeyframe = true
    let generation = 0
    let chain: Promise<void> = Promise.resolve()
    let lastConfigureErrorAt = 0
    let lastConfigureErrorShown = false

    const isAlive = (): boolean => !disposed && mountedRef.current
    const setStatusSafe = (next: MirrorUiStatus): void => {
      if (isAlive()) setStatus(next)
    }
    const reportError = (message: string): void => {
      if (isAlive()) setError(message)
    }
    const enqueue = (task: () => void | Promise<void>): void => {
      chain = chain.then(task).catch((err) => {
        if (isAlive()) console.warn('[mirror] 帧管线异常', err)
      })
    }

    const ensureConfigured = async (): Promise<boolean> => {
      if (!decoder || !sps) return false
      if (
        lastConfigureErrorShown &&
        Date.now() - lastConfigureErrorAt < CONFIGURE_RETRY_GAP_MS
      ) {
        return false
      }
      try {
        const mode = await decoder.configure({ sps, pps: pps ?? undefined })
        if (!isAlive()) return false
        needsConfigure = false
        awaitingKeyframe = true
        lastConfigureErrorShown = false
        setDecoderMode(mode)
        return true
      } catch (err) {
        if (!isAlive()) return false
        lastConfigureErrorAt = Date.now()
        if (!pps) {
          // Annex B 模式可能暂时不可用；PPS 还在码流里，等它到达后自动重试，先不打扰用户
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
      if (!isAlive() || gen !== generation || !decoder) return
      if (!decoder.isConfigured || needsConfigure) {
        const ok = await ensureConfigured()
        if (!ok || !isAlive() || gen !== generation || !decoder) return
      }
      if (!decoder.isConfigured) return
      if (awaitingKeyframe && !keyframe) return
      if (keyframe) awaitingKeyframe = false
      decoder.decode(payload, ptsUs, { keyframe, nals })
    }

    const handleFrame = (payload: Uint8Array, ptsUs: number): void => {
      if (!isAlive()) return
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

    /** 消费 hello/config 里的 SPS/PPS（后端向后兼容扩展了 pps_b64，老版本没有则等码流） */
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
      if (!isAlive()) return
      setHello(toHelloInfo(msg, serial))
      applyParameterSets(msg.sps_b64, msg.pps_b64)
      setError(null)
      awaitingKeyframe = true
      setStatusSafe('waiting-keyframe')
      socket?.requestKeyframe()
    }

    const handleControl = (msg: MirrorControlMessage): void => {
      if (!isAlive()) return
      switch (msg.kind) {
        case 'config': {
          setHello((prev) => {
            if (!prev) return prev
            const next: MirrorHelloInfo = { ...prev }
            if (typeof msg.width === 'number') next.width = msg.width
            if (typeof msg.height === 'number') next.height = msg.height
            if (typeof msg.rotation === 'number') next.rotation = msg.rotation
            if (typeof msg.codec === 'string') next.codec = msg.codec
            if (typeof msg.max_size === 'number') next.maxSize = msg.max_size
            if (typeof msg.max_fps === 'number') next.maxFps = msg.max_fps
            return next
          })
          applyParameterSets(msg.sps_b64, msg.pps_b64)
          awaitingKeyframe = true
          setStatusSafe('waiting-keyframe')
          socket?.requestKeyframe()
          break
        }
        case 'flush': {
          awaitingKeyframe = true
          setStatusSafe('waiting-keyframe')
          const gen = generation
          enqueue(async () => {
            if (!isAlive() || gen !== generation || !decoder) return
            await decoder.flush(typeof msg.reason === 'string' ? msg.reason : undefined)
          })
          socket?.requestKeyframe()
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
          serverStatsRef.current = {
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
      if (!isAlive()) return
      setStatusSafe('connecting')
      awaitingKeyframe = true
      socket?.requestKeyframe()
    }

    const handleClose = (info: MirrorCloseInfo): void => {
      if (!isAlive() || info.manual) return
      awaitingKeyframe = true
      if (info.willReconnect) {
        setStatusSafe('reconnecting')
      } else {
        reportError('连接已断开（页面隐藏时暂停自动重连），回到页面后会自动恢复。')
        setStatusSafe('error')
      }
    }

    const connect = (): void => {
      if (!isAlive()) return
      try {
        socket?.close()
      } catch {
        /* noop */
      }
      socket = null
      socketRef.current = null
      try {
        decoder?.close()
      } catch {
        /* noop */
      }
      decoder = null
      decoderRef.current = null
      sps = null
      pps = null
      needsConfigure = true
      awaitingKeyframe = true
      generation += 1
      chain = Promise.resolve()
      lastConfigureErrorShown = false
      setDecoderMode(null)
      setStatusSafe('connecting')
      setError(null)
      rendererRef.current?.clear()

      try {
        decoder = new MirrorDecoder({
          onFrame: (frame) => {
            const renderer = rendererRef.current
            if (renderer) renderer.present(frame)
            else closeFrame(frame)
          },
          onError: (err) => {
            if (!isAlive()) return
            awaitingKeyframe = true
            setStatus((prev) => (prev === 'streaming' ? 'waiting-keyframe' : prev))
            reportError(`解码错误：${err.message}`)
            socket?.requestKeyframe()
          },
        })
        decoderRef.current = decoder
      } catch (err) {
        reportError(`创建解码器失败：${errorMessage(err)}`)
        setStatusSafe('error')
        return
      }

      try {
        socket = createMirrorSocket({
          serial,
          token,
          onOpen: handleOpen,
          onHello: handleHello,
          onFrame: handleFrame,
          onControl: handleControl,
          onClose: handleClose,
          // 连接级错误随后一定有 close 事件；这里不重复报错，避免干扰状态机
          onError: () => undefined,
        })
        socketRef.current = socket
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
      socketRef.current = null
      try {
        decoder?.close()
      } catch {
        /* noop */
      }
      decoder = null
      decoderRef.current = null
      rendererRef.current?.clear()
      if (resetToIdle && isAlive()) {
        setDecoderMode(null)
        setHello(null)
        setStats(INITIAL_STATS)
        setStatus('idle')
      }
    }

    const boot = async (): Promise<void> => {
      setHello(null)
      setStats(INITIAL_STATS)
      setDecoderMode(null)
      setError(null)
      setStatus('checking')
      try {
        const info = await get<MirrorStatusInfo>(
          `/devices/${encodeURIComponent(serial)}/mirror/status`,
        )
        if (!isAlive()) return
        if (info && info.active === true) {
          startedRef.current = true
          const width = Number(info.width)
          const height = Number(info.height)
          if (width > 0 && height > 0) {
            setHello({
              serial,
              sessionId: typeof info.session_id === 'string' ? info.session_id : '',
              width,
              height,
              rotation: Number(info.rotation) || 0,
              codec: typeof info.codec === 'string' ? info.codec : '',
              maxSize: typeof info.max_size === 'number' ? info.max_size : undefined,
              maxFps: typeof info.max_fps === 'number' ? info.max_fps : undefined,
            })
          }
          connect()
        } else {
          startedRef.current = false
          setStatus('idle')
        }
      } catch (err) {
        if (!isAlive()) return
        startedRef.current = false
        setStatus('idle')
        setError(
          `获取镜像状态失败：${errorMessage(err)}。后端可能未启动，可点击“启动镜像”重试。`,
        )
      }
    }

    controlsRef.current = { connect, teardown }
    void boot()

    return () => {
      disposed = true
      teardown(false)
      if (controlsRef.current && controlsRef.current.connect === connect) {
        controlsRef.current = null
      }
    }
  }, [serial, token, isSupported])

  const startMirror = useCallback(async (): Promise<void> => {
    if (!serial || !isSupported) return
    setStatus('starting')
    setError(null)
    try {
      await post<void>(`/devices/${encodeURIComponent(serial)}/mirror/start`)
      startedRef.current = true
      if (mountedRef.current) controlsRef.current?.connect()
    } catch (err) {
      if (!mountedRef.current) return
      setStatus('error')
      setError(`启动镜像失败：${errorMessage(err)}`)
    }
  }, [serial, isSupported])

  const stopMirror = useCallback(async (): Promise<void> => {
    if (!serial) return
    try {
      await post<void>(`/devices/${encodeURIComponent(serial)}/mirror/stop`)
    } catch (err) {
      if (mountedRef.current) setError(`停止镜像失败：${errorMessage(err)}`)
    }
    startedRef.current = false
    controlsRef.current?.teardown(true)
  }, [serial])

  const reconnect = useCallback((): void => {
    if (!isSupported) return
    setError(null)
    if (startedRef.current) {
      controlsRef.current?.connect()
    } else {
      void startMirror()
    }
  }, [isSupported, startMirror])

  const requestKeyframe = useCallback((): void => {
    socketRef.current?.requestKeyframe()
  }, [])

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
  }
}
