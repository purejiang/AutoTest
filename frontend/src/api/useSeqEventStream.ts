// 通用「seq 事件流」WebSocket：state 首帧 + since_seq 补发 + 实时事件。
// 运行事件（§6.1）与录制事件（§6.3）协议骨架一致，差异只留在各自的适配器：
// - URL / id 校验 / 事件字段映射；
// - 首帧附加字段（录制有 event_count）；
// - run 终态（run_state/run_finished）对 status 的额外更新。
// 公共部分（断线退避、20s ping、seq 去重、内存上限、终态停止、清理）只维护这一份。
import { onScopeDispose, ref, toValue, watch, type MaybeRefOrGetter, type Ref } from 'vue'

import type { WsEventMsg, WsStateMsg } from '@/types/api'

const PING_INTERVAL_MS = 20_000
const BASE_RETRY_MS = 2_000
const MAX_RETRY_MS = 30_000

export interface SeqEventStreamContext<TStatus extends string> {
  setStatus: (status: TStatus) => void
  /** 终态后不再重连（已经走完的事件流）。 */
  stopReconnect: () => void
  setEventCount: (count: number) => void
}

export interface SeqEventStreamOptions<TId extends string | number, TEvent, TStatus extends string> {
  enabled?: MaybeRefOrGetter<boolean>
  /** 最多保留在内存的事件条数 */
  maxEvents?: number
  terminalStatuses: ReadonlySet<string>
  isValidId: (value: unknown) => value is TId
  buildUrl: (id: TId, sinceSeq: number) => string
  createEvent: (message: WsEventMsg) => TEvent
  /** 首帧 state 的附加字段（status 已由核心处理）。 */
  onState?: (message: WsStateMsg, context: SeqEventStreamContext<TStatus>) => void
  /** 事件的领域语义（如 run_state/run_finished）。 */
  onEvent?: (event: TEvent, context: SeqEventStreamContext<TStatus>) => void
}

export interface SeqEventStream<TEvent, TStatus extends string> {
  events: Ref<TEvent[]>
  connected: Ref<boolean>
  status: Ref<TStatus | null>
  /** 首帧事件总数（只有录制协议提供，run 恒为 0）。 */
  eventCount: Ref<number>
  lastSeq: Ref<number>
  reconnectAttempt: Ref<number>
  error: Ref<string | null>
  clear: () => void
}

export function useSeqEventStream<TId extends string | number, TEvent, TStatus extends string>(
  idSource: MaybeRefOrGetter<TId | null | undefined>,
  options: SeqEventStreamOptions<TId, TEvent, TStatus>,
): SeqEventStream<TEvent, TStatus> {
  const events = ref([]) as Ref<TEvent[]>
  const connected = ref(false)
  const status = ref(null) as Ref<TStatus | null>
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

  function connectFor(id: TId): void {
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

    const context: SeqEventStreamContext<TStatus> = {
      setStatus: (next) => {
        status.value = next
        if (options.terminalStatuses.has(next)) terminal = true
      },
      stopReconnect: () => {
        terminal = true
      },
      setEventCount: (count) => {
        eventCount.value = count
      },
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
          context.setStatus(stateMessage.status as TStatus)
        }
        options.onState?.(stateMessage, context)
        return
      }

      if (kind === 'event') {
        const eventMessage = message as WsEventMsg
        if (typeof eventMessage.seq !== 'number' || seen.has(eventMessage.seq)) return
        seen.add(eventMessage.seq)
        const event = options.createEvent(eventMessage)
        latestSeq = Math.max(latestSeq, eventMessage.seq)
        lastSeq.value = latestSeq
        const next = events.value.concat(event)
        events.value = next.length > maxEvents ? next.slice(next.length - maxEvents) : next
        options.onEvent?.(event, context)
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
        socket = new WebSocket(options.buildUrl(id, latestSeq))
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
    () => [toValue(idSource), toValue(options.enabled ?? true)] as const,
    ([id, enabled]) => {
      cleanupCurrent?.()
      cleanupCurrent = null
      if (!enabled || !options.isValidId(id)) {
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
