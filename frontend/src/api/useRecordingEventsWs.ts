// 录制事件 WebSocket（契约 §6.3）：公共连接/重连/去重/心跳在 useSeqEventStream。
// 这里只保留录制 URL、事件字段映射和首帧 event_count。
import type { MaybeRefOrGetter } from 'vue'

import type { RecordingEvent, RecordingStatus, WsEventMsg } from '@/types/api'

import { useSeqEventStream } from './useSeqEventStream'

export interface RecordingEventsWsOptions {
  enabled?: MaybeRefOrGetter<boolean>
  /** 最多保留在内存的事件条数（默认 1000） */
  maxEvents?: number
}

const TERMINAL_STATUSES = new Set<string>(['stopped'])

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
  const stream = useSeqEventStream<string, RecordingEvent, RecordingStatus>(recordingId, {
    enabled: options.enabled,
    maxEvents: options.maxEvents ?? 1000,
    terminalStatuses: TERMINAL_STATUSES,
    isValidId: (value): value is string => typeof value === 'string' && value.length > 0,
    buildUrl: buildWsUrl,
    createEvent: (message: WsEventMsg) => ({
      seq: message.seq,
      // WS 的 type 就是录制 kind（tap/swipe/input/key/app ...），payload 可直接使用。
      kind: message.type,
      payload: message.payload ?? {},
      confidence: normalizeConfidence(message.confidence),
      created_at: message.ts ?? new Date().toISOString(),
    }),
    onState: (message, context) => {
      if (typeof message.event_count === 'number') context.setEventCount(message.event_count)
    },
  })

  return {
    events: stream.events,
    connected: stream.connected,
    status: stream.status,
    eventCount: stream.eventCount,
    lastSeq: stream.lastSeq,
    reconnectAttempt: stream.reconnectAttempt,
    error: stream.error,
    clear: stream.clear,
  }
}
