// 运行事件 WebSocket（契约 §6.1）：公共连接/重连/去重/心跳在 useSeqEventStream。
// 这里只保留 run 的 URL、事件字段映射和 run_state/run_finished 的终态语义。
import type { MaybeRefOrGetter } from 'vue'

import type { RunEvent, RunStatus } from '@/types/api'

import { useSeqEventStream } from './useSeqEventStream'

export interface RunEventsWsOptions {
  enabled?: MaybeRefOrGetter<boolean>
  /** 最多保留在内存的事件条数（默认 3000） */
  maxEvents?: number
}

const TERMINAL_STATUSES = new Set<string>(['succeeded', 'failed', 'aborted', 'stopped', 'interrupted'])

function buildWsUrl(runId: number, sinceSeq: number): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/runs/${encodeURIComponent(String(runId))}?since_seq=${sinceSeq}`
}

export function useRunEventsWs(
  runId: MaybeRefOrGetter<number | null | undefined>,
  options: RunEventsWsOptions = {},
) {
  const stream = useSeqEventStream<number, RunEvent, RunStatus>(runId, {
    enabled: options.enabled,
    maxEvents: options.maxEvents ?? 3000,
    terminalStatuses: TERMINAL_STATUSES,
    isValidId: (value): value is number => typeof value === 'number' && Number.isFinite(value),
    buildUrl: buildWsUrl,
    createEvent: (message) => ({
      seq: message.seq,
      type: message.type,
      payload: message.payload ?? {},
      created_at: message.ts ?? new Date().toISOString(),
    }),
    onEvent: (event, context) => {
      if (event.type === 'run_state' && typeof event.payload.status === 'string') {
        context.setStatus(event.payload.status as RunStatus)
      }
      if (event.type === 'run_finished' && typeof event.payload.status === 'string') {
        context.setStatus(event.payload.status as RunStatus)
        context.stopReconnect()
      }
    },
  })

  return {
    events: stream.events,
    connected: stream.connected,
    status: stream.status,
    lastSeq: stream.lastSeq,
    reconnectAttempt: stream.reconnectAttempt,
    error: stream.error,
    clear: stream.clear,
  }
}
