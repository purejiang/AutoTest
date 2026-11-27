// 镜像页内部类型（对外只暴露 MirrorPanelProps，见 ../index.tsx）。

import type { MirrorDecoderMode } from './lib/webcodecs'

/** hello / config 消息归一化后的展示信息 */
export interface MirrorHelloInfo {
  serial: string
  sessionId: string
  width: number
  height: number
  rotation: number
  codec: string
  maxSize?: number
  scalePct?: number
  configuredMaxSize?: number
  maxFps?: number
  videoBitRate?: number
}

/** GET /api/devices/{serial}/mirror/status 的响应（契约 §7.1 / §14.3，向后兼容只读关键字段） */
export interface MirrorStatusInfo {
  active: boolean
  width?: number
  height?: number
  rotation?: number
  codec?: string
  max_size?: number
  scale_pct?: number
  configured_max_size?: number
  max_fps?: number
  video_bit_rate?: number
  session_id?: string
  [key: string]: unknown
}

/** 面板状态机 */
export type MirrorUiStatus =
  | 'idle' // 镜像服务未启动
  | 'checking' // 首次查询 mirror/status
  | 'starting' // POST mirror/start 进行中
  | 'connecting' // WS 建连中
  | 'waiting-keyframe' // 已收到 hello，等第一个 IDR 解出
  | 'streaming' // 持续出帧
  | 'reconnecting' // WS 断线，退避重连中
  | 'error'

export interface MirrorStats {
  /** 本地画布实际呈现的 fps */
  fps: number
  /** 服务端上报丢帧 + 解码队列主动丢帧 */
  dropped: number
  /** 已解码帧数 */
  decoded: number
  /** 服务端 stats 消息里的 fps（可能为空） */
  serverFps?: number
  /** WebCodecs decodeQueueSize（>2 说明解码排队，越大越滞后） */
  queueSize: number
  /** 单帧绘制耗时（EMA，ms）：判断瓶颈在“画”这一层 */
  drawMs?: number
  /** 正在等关键帧（断链恢复中），此时 P 帧被丢是预期行为 */
  awaitingKeyframe?: boolean
}

export interface MirrorSessionState {
  status: MirrorUiStatus
  hello: MirrorHelloInfo | null
  stats: MirrorStats
  error: string | null
  decoderMode: MirrorDecoderMode | null
}
