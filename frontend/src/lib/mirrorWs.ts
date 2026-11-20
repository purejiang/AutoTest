// 镜像 WebSocket 客户端（契约 §6.2）。
//
// 帧格式（server -> client binary，12 字节大端头）：
//   [u64 BE pts_us][u32 BE payload_len][Annex B payload]
// 容错：某些代理/中间件会把 12 字节头吃掉只留 payload；若 header 长度字段与剩余长度不匹配，
// 则把整条二进制消息当作 payload 兜底（pts 用 performance.now()*1000 估计）。
//
// text 帧：
//   hello  {"kind":"hello","serial","session_id","width","height","rotation","codec","sps_b64","max_size","max_fps"}
//   control {"kind":"config"|"flush"|"error"|"stats"|"pong", ...}
// client -> server：{"kind":"request_keyframe"} / {"kind":"ping"}
//
// 断线自动重连：2s 起步指数退避（2/4/8/10/10…，上限 10s）；document.hidden 时不重连，
// 页面恢复可见后立即补一次连接。手动 close() 后不再重连。

import type { WsControlMsg } from '@/types/api'

export interface MirrorHello {
  kind: 'hello'
  serial: string
  session_id: string
  width: number
  height: number
  rotation: number
  codec: string
  /** SPS 的 base64（裸 NAL，不含起始码） */
  sps_b64: string
  /** PPS 的 base64（契约冻结后向后兼容扩展；老后端没有该字段，此时从码流捕获） */
  pps_b64?: string
  max_size?: number
  max_fps?: number
  [key: string]: unknown
}

export interface MirrorConfigControl {
  kind: 'config'
  width?: number
  height?: number
  rotation?: number
  codec?: string
  sps_b64?: string
  pps_b64?: string
  max_size?: number
  max_fps?: number
  [key: string]: unknown
}

export interface MirrorFlushControl {
  kind: 'flush'
  reason?: string
  [key: string]: unknown
}

export interface MirrorErrorControl {
  kind: 'error'
  code?: string
  message?: string
  [key: string]: unknown
}

export interface MirrorStatsControl {
  kind: 'stats'
  fps?: number
  dropped?: number
  [key: string]: unknown
}

export interface MirrorPongControl {
  kind: 'pong'
  [key: string]: unknown
}

export type MirrorControlMessage =
  | MirrorConfigControl
  | MirrorFlushControl
  | MirrorErrorControl
  | MirrorStatsControl
  | MirrorPongControl

export interface MirrorCloseInfo {
  code: number
  reason: string
  wasClean: boolean
  /** 是否由调用方主动 close() */
  manual: boolean
  /** WebSocket 是否会按退避策略自动重连 */
  willReconnect: boolean
}

export interface MirrorFrame {
  ptsUs: number
  payload: Uint8Array
}

export interface MirrorSocketOptions {
  serial: string
  /** 契约 §7.1 的鉴权 token（配置了 security.token 时必需）；也可由 url 覆盖 */
  token?: string
  /** 覆盖 WS 地址（测试/自定义代理用）；默认按当前页面 origin 推导 */
  url?: string
  onHello?: (hello: MirrorHello) => void
  onFrame?: (payload: Uint8Array, ptsUs: number) => void
  onControl?: (msg: MirrorControlMessage) => void
  onError?: (err: Error) => void
  onOpen?: () => void
  onClose?: (info: MirrorCloseInfo) => void
  /** 测试注入；默认 new WebSocket(url) */
  wsFactory?: (url: string) => WebSocket
}

export type MirrorSocketState = 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface MirrorSocket {
  readonly state: MirrorSocketState
  readonly url: string
  close(): void
  requestKeyframe(): void
  ping(): void
  /**
   * 流式触摸（首选手动控制通道）：走同一条 WS，无 HTTP 开销且严格有序。
   * 返回 false 表示连接不可用（调用方应回落到 REST /touch）。
   */
  sendTouch(action: 'down' | 'move' | 'up', nx: number, ny: number): boolean
}

const INITIAL_BACKOFF_MS = 2000
const MAX_BACKOFF_MS = 10000
const HEARTBEAT_MS = 15000

function defaultUrl(serial: string, token?: string): string {
  if (typeof window === 'undefined' || typeof window.location === 'undefined') {
    throw new Error('mirrorWs: 当前环境没有 window.location，无法推导 WS 地址')
  }
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const qs = token ? `?token=${encodeURIComponent(token)}` : ''
  return `${proto}//${window.location.host}/ws/mirror/${encodeURIComponent(serial)}${qs}`
}

function documentHidden(): boolean {
  return typeof document !== 'undefined' && document.hidden === true
}

/**
 * 解析一帧二进制消息（导出便于单测）。行为见文件头注释。
 */
export function parseBinaryFrame(buffer: ArrayBuffer, nowUs?: number): MirrorFrame {
  const fallbackPts = nowUs ?? (typeof performance !== 'undefined' ? performance.now() * 1000 : Date.now() * 1000)
  if (buffer.byteLength >= 12) {
    const view = new DataView(buffer)
    const ptsUs = Number(view.getBigUint64(0, false))
    const payloadLen = view.getUint32(8, false)
    if (payloadLen === buffer.byteLength - 12) {
      return { ptsUs, payload: new Uint8Array(buffer, 12) }
    }
  }
  // 只收到 payload 或头与长度不匹配：整条兜底
  return { ptsUs: fallbackPts, payload: new Uint8Array(buffer) }
}

export function createMirrorSocket(options: MirrorSocketOptions): MirrorSocket {
  const {
    serial,
    token,
    onHello,
    onFrame,
    onControl,
    onError,
    onOpen,
    onClose,
    wsFactory,
  } = options

  const url = options.url ?? defaultUrl(serial, token)
  const make = wsFactory ?? ((u: string) => new WebSocket(u))

  let ws: WebSocket | null = null
  let state: MirrorSocketState = 'connecting'
  let manualClosed = false
  let disposed = false
  let attempt = 0
  let pendingKeyframe = false
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  let awaitingVisible = false

  function clearReconnectTimer(): void {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  function stopHeartbeat(): void {
    if (heartbeatTimer !== null) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  function startHeartbeat(): void {
    stopHeartbeat()
    heartbeatTimer = setInterval(() => {
      if (documentHidden()) return
      sendJson({ kind: 'ping' })
    }, HEARTBEAT_MS)
  }

  function sendTouch(action: 'down' | 'move' | 'up', nx: number, ny: number): boolean {
    return sendJson({ kind: 'touch', action, nx, ny })
  }

  function sendJson(payload: Record<string, unknown>): boolean {
    if (!ws || ws.readyState !== WebSocket.OPEN) return false
    try {
      ws.send(JSON.stringify(payload))
      return true
    } catch {
      return false
    }
  }

  function handleText(text: string): void {
    let parsed: unknown
    try {
      parsed = JSON.parse(text)
    } catch {
      onError?.(new Error('mirrorWs: 收到非法 JSON text 帧'))
      return
    }
    if (!parsed || typeof parsed !== 'object') {
      onError?.(new Error('mirrorWs: 控制消息不是对象'))
      return
    }
    const msg = parsed as { kind?: unknown }
    if (msg.kind === 'hello') {
      onHello?.(parsed as MirrorHello)
      return
    }
    onControl?.(parsed as MirrorControlMessage)
  }

  function handleBinary(data: ArrayBuffer): void {
    try {
      const frame = parseBinaryFrame(data)
      if (frame.payload.byteLength === 0) return
      onFrame?.(frame.payload, frame.ptsUs)
    } catch (err) {
      onError?.(err instanceof Error ? err : new Error(String(err)))
    }
  }

  function scheduleReconnect(): void {
    if (manualClosed || disposed) return
    if (documentHidden()) {
      // 页面隐藏时不重连；等 visibilitychange 恢复后再补
      awaitingVisible = true
      state = 'reconnecting'
      return
    }
    const delay = Math.min(MAX_BACKOFF_MS, INITIAL_BACKOFF_MS * 2 ** attempt)
    attempt += 1
    state = 'reconnecting'
    clearReconnectTimer()
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, delay)
  }

  function connect(): void {
    if (manualClosed || disposed) return
    clearReconnectTimer()
    let next: WebSocket
    try {
      next = make(url)
    } catch (err) {
      onError?.(err instanceof Error ? err : new Error(String(err)))
      scheduleReconnect()
      return
    }
    next.binaryType = 'arraybuffer'
    ws = next
    state = attempt === 0 ? 'connecting' : 'reconnecting'

    next.onopen = () => {
      if (disposed || manualClosed) {
        try {
          next.close()
        } catch {
          /* noop */
        }
        return
      }
      attempt = 0
      state = 'open'
      awaitingVisible = false
      startHeartbeat()
      onOpen?.()
      if (pendingKeyframe) {
        pendingKeyframe = false
        sendJson({ kind: 'request_keyframe' })
      }
    }

    next.onmessage = (ev: MessageEvent) => {
      if (disposed) return
      const data = ev.data
      if (typeof data === 'string') {
        handleText(data)
      } else if (data instanceof ArrayBuffer) {
        handleBinary(data)
      } else if (typeof Blob !== 'undefined' && data instanceof Blob) {
        // binaryType=arraybuffer 时不应该出现，兜底转一下
        void data.arrayBuffer().then((buf) => {
          if (!disposed) handleBinary(buf)
        }).catch((err: unknown) => {
          onError?.(err instanceof Error ? err : new Error(String(err)))
        })
      } else {
        onError?.(new Error('mirrorWs: 收到未知类型的 WS 消息'))
      }
    }

    next.onerror = () => {
      if (disposed) return
      // 浏览器不暴露错误细节；close 事件随后到达，由 close 处理重连
      onError?.(new Error('mirrorWs: WebSocket 连接出错'))
    }

    next.onclose = (ev: CloseEvent) => {
      if (disposed) return
      ws = null
      stopHeartbeat()
      const willReconnect = !manualClosed
      if (!manualClosed) {
        scheduleReconnect()
      } else {
        state = 'closed'
      }
      onClose?.({
        code: ev.code,
        reason: ev.reason,
        wasClean: ev.wasClean,
        manual: manualClosed,
        willReconnect,
      })
    }
  }

  function onVisibilityChange(): void {
    if (disposed || manualClosed) return
    if (!documentHidden() && awaitingVisible && (!ws || ws.readyState === WebSocket.CLOSED)) {
      awaitingVisible = false
      attempt = 0
      connect()
    }
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibilityChange)
  }

  connect()

  return {
    get state() {
      return state
    },
    get url() {
      return url
    },
    close() {
      manualClosed = true
      disposed = true
      state = 'closed'
      clearReconnectTimer()
      stopHeartbeat()
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVisibilityChange)
      }
      if (ws) {
        const current = ws
        ws = null
        try {
          current.close(1000, 'client closed')
        } catch {
          /* noop */
        }
      }
    },
    requestKeyframe() {
      if (!sendJson({ kind: 'request_keyframe' })) pendingKeyframe = true
    },
    ping() {
      sendJson({ kind: 'ping' })
    },
    sendTouch(action: 'down' | 'move' | 'up', nx: number, ny: number) {
      return sendJson({ kind: 'touch', action, nx, ny })
    },
  }
}
