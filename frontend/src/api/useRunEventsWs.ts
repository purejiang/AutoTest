// 运行事件 WebSocket（契约 §6.1）：state 首帧 + since_seq 补发 + 实时事件。
// 增量按 seq 去重；断线 2s 起指数退避重连；20s ping 保活；run 终态后不再重连。
import { onScopeDispose, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'

import type { RunEvent, RunStatus, WsEventMsg, WsStateMsg } from '@/types/api'

export interface RunEventsWsOptions {
  enabled?: MaybeRefOrGetter<boolean>
  /** 最多保留在内存的事件条数（默认 3000） */
  maxEvents?: number
}

const TERMINAL_STATUSES = new Set<string>(['succeeded', 'failed', 'aborted', 'stopped', 'interrupted'])
const PING_INTERVAL_MS = 20_000
const BASE_RETRY_MS = 2_000
const MAX_RETRY_MS = 30_000

function buildWsUrl(runId: number, sinceSeq: number): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/runs/${encodeURIComponent(String(runId))}?since_seq=${sinceSeq}`
}

export function useRunEventsWs(
  runId: MaybeRefOrGetter<number | null | undefined>,
  options: RunEventsWsOptions = {},
) {
  const events = ref<RunEvent[]>([])
  const connected = ref(false)
  const status = ref<RunStatus | null>(null)
  const lastSeq = ref(0)
  const reconnectAttempt = ref(0)
  const error = ref<string | null>(null)
  const maxEvents = options.maxEvents ?? 3000

  let cleanupCurrent: (() => void) | null = null
  let clearBuffer: () => void = () => undefined

  function resetState(): void {
    events.value = []
    connected.value = false
    status.value = null
    lastSeq.value = 0
    reconnectAttempt.value = 0
    error.value = null
  }

  function connectFor(id: number): void {
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
        if (typeof stateMessage.status === 'string') {
          const nextStatus = stateMessage.status as RunStatus
          status.value = nextStatus
          if (TERMINAL_STATUSES.has(nextStatus)) terminal = true
        }
        return
      }

      if (kind === 'event') {
        const eventMessage = message as WsEventMsg
        if (typeof eventMessage.seq !== 'number' || seen.has(eventMessage.seq)) return
        seen.add(eventMessage.seq)
        const event: RunEvent = {
          seq: eventMessage.seq,
          type: eventMessage.type,
          payload: eventMessage.payload ?? {},
          created_at: eventMessage.ts ?? new Date().toISOString(),
        }
        latestSeq = Math.max(latestSeq, event.seq)
        lastSeq.value = latestSeq
        const next = events.value.concat(event)
        events.value = next.length > maxEvents ? next.slice(next.length - maxEvents) : next
        if (event.type === 'run_state' && typeof event.payload.status === 'string') {
          const nextStatus = event.payload.status as RunStatus
          status.value = nextStatus
          if (TERMINAL_STATUSES.has(nextStatus)) terminal = true
        }
        if (event.type === 'run_finished' && typeof event.payload.status === 'string') {
          status.value = event.payload.status as RunStatus
          terminal = true
        }
        return
      }

      if (kind === 'pong') return

      if (kind === 'error') {
        const messageText = (message as { message?: unknown }).message
        error.value = typeof messageText === 'string' ? messageText : 'WS 错误'
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
    () => [toValue(runId), toValue(options.enabled ?? true)] as const,
    ([id, enabled]) => {
      cleanupCurrent?.()
      cleanupCurrent = null
      if (!enabled || typeof id !== 'number' || !Number.isFinite(id)) {
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
    lastSeq,
    reconnectAttempt,
    error,
    clear: () => clearBuffer(),
  }
}
