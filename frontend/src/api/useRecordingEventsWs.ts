// 录制事件 WebSocket（契约 §6.3）：state 首帧 + since_seq 补发 + 实时事件。
// 增量按 seq 去重；断线 2s 起指数退避重连；20s ping 保活；录制终态后不再重连。
import { onScopeDispose, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'

import type { RecordingEvent, RecordingStatus, WsEventMsg, WsStateMsg } from '@/types/api'

export interface RecordingEventsWsOptions {
  enabled?: MaybeRefOrGetter<boolean>
  /** 最多保留在内存的事件条数（默认 1000） */
  maxEvents?: number
}

const TERMINAL_STATUSES = new Set<string>(['stopped'])
const PING_INTERVAL_MS = 20_000
const BASE_RETRY_MS = 2_000
const MAX_RETRY_MS = 30_000

function buildWsUrl(recordingId: string, sinceSeq: number): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/recordings/${encodeURIComponent(recordingId)}?since_seq=${sinceSeq}`
}

function normalizeConfidence(value: string | undefined): RecordingEvent['confidence'] {
  return value === 'low' || value === 'medium' || value === 'high' ? value : 'high'
}

export function useRecordingEventsWs(
  recordingId: MaybeRefOrGetter<string | null | undefined>,
  options: RecordingEventsWsOptions = {},
) {
  const events = ref<RecordingEvent[]>([])
  const connected = ref(false)
  const status = ref<RecordingStatus | null>(null)
  /** 首帧 state 携带的事件总数；用于展示/对照 REST 的 event_count。 */
  const eventCount = ref(0)
  const lastSeq = ref(0)
  const reconnectAttempt = ref(0)
  const error = ref<string | null>(null)
  const maxEvents = options.maxEvents ?? 1000

  let cleanupCurrent: (() => void) | null = null
  let clearBuffer: () => void = () => undefined

  function resetState(): void {
    events.value = []
    connected.value = false
    status.value = null
    eventCount.value = 0
    lastSeq.value = 0
    reconnectAttempt.value = 0
    error.value = null
  }

  function connectFor(id: string): void {
    let closed = false
    let terminal = false
    let socket: WebSocket | null = null
    let retryTimer: number | undefined
    let pingTimer: number | undefined
    let attempt = 0
    let latestSeq = 0
    const seen = new Set<number>()

    const clearPing = (): void => {
      if (pingTimer !== undefined) {
        window.clearInterval(pingTimer)
        pingTimer = undefined
      }
    }

    const scheduleRetry = (): void => {
      if (closed || terminal) return
      attempt += 1
      reconnectAttempt.value = attempt
      const delay = Math.min(BASE_RETRY_MS * 2 ** Math.max(0, attempt - 1), MAX_RETRY_MS)
      retryTimer = window.setTimeout(() => {
        retryTimer = undefined
        open()
      }, delay)
    }

    const handleMessage = (raw: string): void => {
      let message: unknown
      try {
        message = JSON.parse(raw) as unknown
      } catch {
        return
      }
      if (!message || typeof message !== 'object') return
      const kind = (message as { kind?: unknown }).kind

      if (kind === 'state') {
        const stateMessage = message as WsStateMsg
        if (typeof stateMessage.last_seq === 'number') {
          latestSeq = Math.max(latestSeq, stateMessage.last_seq)
          lastSeq.value = latestSeq
        }
        if (typeof stateMessage.event_count === 'number') {
          eventCount.value = stateMessage.event_count
        }
        if (typeof stateMessage.status === 'string') {
          const nextStatus = stateMessage.status as RecordingStatus
          status.value = nextStatus
          if (TERMINAL_STATUSES.has(nextStatus)) terminal = true
        }
        return
      }

      if (kind === 'event') {
        const eventMessage = message as WsEventMsg
        if (typeof eventMessage.seq !== 'number' || seen.has(eventMessage.seq)) return
        seen.add(eventMessage.seq)
        const event: RecordingEvent = {
          seq: eventMessage.seq,
          // WS 的 type 就是录制 kind（tap/swipe/input/key/app ...），payload 可直接使用。
          kind: eventMessage.type,
          payload: eventMessage.payload ?? {},
          confidence: normalizeConfidence(eventMessage.confidence),
          created_at: eventMessage.ts ?? new Date().toISOString(),
        }
        latestSeq = Math.max(latestSeq, event.seq)
        lastSeq.value = latestSeq
        const next = events.value.concat(event)
        events.value = next.length > maxEvents ? next.slice(next.length - maxEvents) : next
        return
      }

      if (kind === 'pong') return

      if (kind === 'error') {
        const messageText = (message as { message?: unknown }).message
        error.value = typeof messageText === 'string' ? messageText : '实时连接错误'
      }
    }

    function open(): void {
      if (closed) return
      connected.value = false
      try {
        socket = new WebSocket(buildWsUrl(id, latestSeq))
      } catch (connectError) {
        error.value = connectError instanceof Error ? connectError.message : String(connectError)
        scheduleRetry()
        return
      }

      socket.onopen = () => {
        if (closed) return
        attempt = 0
        reconnectAttempt.value = 0
        connected.value = true
        error.value = null
        clearPing()
        pingTimer = window.setInterval(() => {
          if (socket && socket.readyState === WebSocket.OPEN) {
            try {
              socket.send(JSON.stringify({ kind: 'ping' }))
            } catch {
              // 连接正在关闭，交给 onclose 重连
            }
          }
        }, PING_INTERVAL_MS)
      }

      socket.onmessage = (incoming: MessageEvent) => {
        if (closed) return
        if (typeof incoming.data === 'string') handleMessage(incoming.data)
      }

      socket.onerror = () => {
        if (closed) return
        error.value = '实时连接错误，正在重连…'
      }

      socket.onclose = () => {
        clearPing()
        socket = null
        if (closed) return
        connected.value = false
        scheduleRetry()
      }
    }

    clearBuffer = () => {
      events.value = []
      seen.clear()
    }

    resetState()
    open()

    cleanupCurrent = () => {
      closed = true
      clearPing()
      if (retryTimer !== undefined) window.clearTimeout(retryTimer)
      if (socket) {
        socket.onopen = null
        socket.onmessage = null
        socket.onerror = null
        socket.onclose = null
        try {
          socket.close()
        } catch {
          // ignore
        }
        socket = null
      }
      clearBuffer = () => undefined
    }
  }

  watch(
    () => [toValue(recordingId), toValue(options.enabled ?? true)] as const,
    ([id, enabled]) => {
      cleanupCurrent?.()
      cleanupCurrent = null
      if (!enabled || typeof id !== 'string' || id.length === 0) {
        resetState()
        return
      }
      connectFor(id)
    },
    { immediate: true },
  )

  onScopeDispose(() => {
    cleanupCurrent?.()
    cleanupCurrent = null
  })

  return {
    events,
    connected,
    status,
    eventCount,
    lastSeq,
    reconnectAttempt,
    error,
    clear: () => clearBuffer(),
  }
}
