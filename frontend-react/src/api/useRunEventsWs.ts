// 运行事件 WebSocket（契约 §6.1）：state 首帧 + since_seq 补发 + 实时事件。
// 增量按 seq 去重；断线 2s 起指数退避重连；20s ping 保活；run 终态后不再重连。
import { useEffect, useRef, useState } from 'react'

import type { RunEvent, RunStatus, WsEventMsg, WsStateMsg } from '@/types/api'

export interface RunEventsWsOptions {
  enabled?: boolean
  /** 最多保留在内存的事件条数（默认 3000） */
  maxEvents?: number
}

export interface RunEventsWsResult {
  events: RunEvent[]
  connected: boolean
  status: RunStatus | null
  lastSeq: number
  reconnectAttempt: number
  error: string | null
  clear: () => void
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
  runId: number | null | undefined,
  options: RunEventsWsOptions = {},
): RunEventsWsResult {
  const enabled = options.enabled ?? true
  const maxEvents = options.maxEvents ?? 3000

  const [events, setEvents] = useState<RunEvent[]>([])
  const [connected, setConnected] = useState(false)
  const [status, setStatus] = useState<RunStatus | null>(null)
  const [lastSeq, setLastSeq] = useState(0)
  const [reconnectAttempt, setReconnectAttempt] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const clearRef = useRef<() => void>(() => undefined)

  useEffect(() => {
    if (!enabled || typeof runId !== 'number' || !Number.isFinite(runId)) return

    let closed = false
    let terminal = false
    let socket: WebSocket | null = null
    let retryTimer: number | undefined
    let pingTimer: number | undefined
    let attempt = 0
    let latestSeq = 0
    const seen = new Set<number>()

    setEvents([])
    setStatus(null)
    setLastSeq(0)
    setReconnectAttempt(0)
    setError(null)

    const clearPing = (): void => {
      if (pingTimer !== undefined) {
        window.clearInterval(pingTimer)
        pingTimer = undefined
      }
    }

    const scheduleRetry = (): void => {
      if (closed || terminal) return
      attempt += 1
      setReconnectAttempt(attempt)
      const delay = Math.min(BASE_RETRY_MS * 2 ** Math.max(0, attempt - 1), MAX_RETRY_MS)
      retryTimer = window.setTimeout(() => {
        retryTimer = undefined
        connect()
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
          setLastSeq(latestSeq)
        }
        if (typeof stateMessage.status === 'string') {
          const nextStatus = stateMessage.status as RunStatus
          setStatus(nextStatus)
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
        setLastSeq(latestSeq)
        setEvents((previous) => {
          const next = previous.concat(event)
          return next.length > maxEvents ? next.slice(next.length - maxEvents) : next
        })
        if (event.type === 'run_state' && typeof event.payload.status === 'string') {
          const nextStatus = event.payload.status as RunStatus
          setStatus(nextStatus)
          if (TERMINAL_STATUSES.has(nextStatus)) terminal = true
        }
        if (event.type === 'run_finished' && typeof event.payload.status === 'string') {
          const nextStatus = event.payload.status as RunStatus
          setStatus(nextStatus)
          terminal = true
        }
        return
      }

      if (kind === 'pong') return

      if (kind === 'error') {
        const messageText = (message as { message?: unknown }).message
        setError(typeof messageText === 'string' ? messageText : 'WS 错误')
      }
    }

    const connect = (): void => {
      if (closed) return
      setConnected(false)
      try {
        socket = new WebSocket(buildWsUrl(runId, latestSeq))
      } catch (connectError) {
        setError(connectError instanceof Error ? connectError.message : String(connectError))
        scheduleRetry()
        return
      }

      socket.onopen = () => {
        if (closed) return
        attempt = 0
        setReconnectAttempt(0)
        setConnected(true)
        setError(null)
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
        setError('实时连接错误，正在重连…')
      }

      socket.onclose = () => {
        clearPing()
        socket = null
        if (closed) return
        setConnected(false)
        scheduleRetry()
      }
    }

    clearRef.current = () => {
      setEvents([])
      seen.clear()
      latestSeq = 0
      setLastSeq(0)
    }

    connect()

    return () => {
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
      clearRef.current = () => undefined
    }
  }, [enabled, runId, maxEvents])

  return {
    events,
    connected,
    status,
    lastSeq,
    reconnectAttempt,
    error,
    clear: () => clearRef.current(),
  }
}
