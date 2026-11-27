import { effectScope, nextTick, ref, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useRecordingEventsWs } from './useRecordingEventsWs'
import { useRunEventsWs } from './useRunEventsWs'
import { useSeqEventStream } from './useSeqEventStream'

interface TestEvent {
  seq: number
  type: string
  payload: Record<string, unknown>
}

class FakeWebSocket {
  static OPEN = 1
  static CLOSED = 3
  static instances: FakeWebSocket[] = []

  url: string
  readyState = FakeWebSocket.OPEN
  sent: string[] = []
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  open(): void {
    this.onopen?.(new Event('open'))
  }

  emit(message: unknown): void {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent)
  }

  send(data: string): void {
    this.sent.push(data)
  }

  serveClose(): void {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({} as CloseEvent)
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({} as CloseEvent)
  }
}

function setup<T>(factory: () => T): { result: T; scope: EffectScope } {
  const scope = effectScope()
  const result = scope.run(factory)
  if (result === undefined) throw new Error('effect scope returned undefined')
  return { result, scope }
}

function lastSocket(): FakeWebSocket {
  return FakeWebSocket.instances[FakeWebSocket.instances.length - 1] as FakeWebSocket
}

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useSeqEventStream', () => {
  it('connects, maps state/events, de-duplicates and caps the buffer', () => {
    const id = ref<number | null>(1)
    const { result, scope } = setup(() =>
      useSeqEventStream<number, TestEvent, string>(id, {
        maxEvents: 2,
        terminalStatuses: new Set(['done']),
        isValidId: (value): value is number => typeof value === 'number',
        buildUrl: (runId, sinceSeq) => `/ws/test/${runId}?since_seq=${sinceSeq}`,
        createEvent: (message) => ({
          seq: message.seq,
          type: message.type,
          payload: message.payload,
        }),
        onState: (message, context) => {
          if (typeof message.event_count === 'number') context.setEventCount(message.event_count)
        },
      }),
    )

    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(lastSocket().url).toBe('/ws/test/1?since_seq=0')

    lastSocket().open()
    expect(result.connected.value).toBe(true)

    lastSocket().emit({
      kind: 'state',
      status: 'running',
      last_seq: 5,
      event_count: 9,
    })
    expect(result.status.value).toBe('running')
    expect(result.lastSeq.value).toBe(5)
    expect(result.eventCount.value).toBe(9)

    lastSocket().emit({ kind: 'event', seq: 1, type: 'a', payload: { n: 1 } })
    lastSocket().emit({ kind: 'event', seq: 2, type: 'b', payload: {} })
    lastSocket().emit({ kind: 'event', seq: 2, type: 'dup', payload: {} })
    lastSocket().emit({ kind: 'event', seq: 3, type: 'c', payload: {} })

    expect(result.events.value.map((event) => event.seq)).toEqual([2, 3])
    expect(result.lastSeq.value).toBe(5)
    scope.stop()
  })

  it('reconnects with backoff and resumes from last_seq', () => {
    vi.useFakeTimers()
    const id = ref<number | null>(7)
    const { result, scope } = setup(() =>
      useSeqEventStream<number, TestEvent, string>(id, {
        terminalStatuses: new Set<string>(),
        isValidId: (value): value is number => typeof value === 'number',
        buildUrl: (runId, sinceSeq) => `/ws/test/${runId}?since_seq=${sinceSeq}`,
        createEvent: (message) => ({ seq: message.seq, type: message.type, payload: message.payload }),
      }),
    )

    lastSocket().open()
    lastSocket().emit({ kind: 'state', status: 'running', last_seq: 4 })
    lastSocket().serveClose()
    expect(result.connected.value).toBe(false)

    vi.advanceTimersByTime(1999)
    expect(FakeWebSocket.instances).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(FakeWebSocket.instances).toHaveLength(2)
    expect(lastSocket().url).toBe('/ws/test/7?since_seq=4')
    expect(result.reconnectAttempt.value).toBe(1)
    scope.stop()
  })

  it('stops reconnecting after a terminal status', () => {
    vi.useFakeTimers()
    const id = ref<number | null>(1)
    const { scope } = setup(() =>
      useSeqEventStream<number, TestEvent, string>(id, {
        terminalStatuses: new Set(['done']),
        isValidId: (value): value is number => typeof value === 'number',
        buildUrl: (runId) => `/ws/test/${runId}`,
        createEvent: (message) => ({ seq: message.seq, type: message.type, payload: message.payload }),
      }),
    )

    lastSocket().open()
    lastSocket().emit({ kind: 'state', status: 'done', last_seq: 1 })
    lastSocket().serveClose()
    vi.advanceTimersByTime(60_000)
    expect(FakeWebSocket.instances).toHaveLength(1)
    scope.stop()
  })

  it('pings every 20s while open', () => {
    vi.useFakeTimers()
    const id = ref<number | null>(1)
    const { scope } = setup(() =>
      useSeqEventStream<number, TestEvent, string>(id, {
        terminalStatuses: new Set<string>(),
        isValidId: (value): value is number => typeof value === 'number',
        buildUrl: (runId) => `/ws/test/${runId}`,
        createEvent: (message) => ({ seq: message.seq, type: message.type, payload: message.payload }),
      }),
    )

    lastSocket().open()
    vi.advanceTimersByTime(20_000)
    expect(lastSocket().sent).toContain(JSON.stringify({ kind: 'ping' }))
    scope.stop()
  })

  it('clears events and resets de-duplication', () => {
    const id = ref<number | null>(1)
    const { result, scope } = setup(() =>
      useSeqEventStream<number, TestEvent, string>(id, {
        terminalStatuses: new Set<string>(),
        isValidId: (value): value is number => typeof value === 'number',
        buildUrl: (runId) => `/ws/test/${runId}`,
        createEvent: (message) => ({ seq: message.seq, type: message.type, payload: message.payload }),
      }),
    )
    lastSocket().open()
    lastSocket().emit({ kind: 'event', seq: 1, type: 'a', payload: {} })
    expect(result.events.value).toHaveLength(1)
    result.clear()
    expect(result.events.value).toHaveLength(0)
    lastSocket().emit({ kind: 'event', seq: 1, type: 'a', payload: {} })
    expect(result.events.value).toHaveLength(1)
    scope.stop()
  })

  it('does not connect when disabled and tears down on scope dispose', async () => {
    const id = ref<number | null>(1)
    const enabled = ref(false)
    const { result, scope } = setup(() =>
      useSeqEventStream<number, TestEvent, string>(id, {
        enabled,
        terminalStatuses: new Set<string>(),
        isValidId: (value): value is number => typeof value === 'number',
        buildUrl: (runId) => `/ws/test/${runId}`,
        createEvent: (message) => ({ seq: message.seq, type: message.type, payload: message.payload }),
      }),
    )
    expect(FakeWebSocket.instances).toHaveLength(0)

    enabled.value = true
    await nextTick()
    expect(FakeWebSocket.instances).toHaveLength(1)
    const socket = lastSocket()
    socket.open()
    expect(result.connected.value).toBe(true)

    scope.stop()
    expect(socket.readyState).toBe(FakeWebSocket.CLOSED)
    expect(result.events.value).toEqual([])
  })
})

describe('run / recording adapters', () => {
  it('run adapter maps run_state/run_finished to status', () => {
    const runId = ref<number | null>(1)
    const { result, scope } = setup(() => useRunEventsWs(runId, { maxEvents: 10 }))
    lastSocket().open()
    lastSocket().emit({
      kind: 'event',
      seq: 1,
      type: 'run_state',
      ts: '2026-01-01T00:00:00Z',
      payload: { status: 'running' },
    })
    expect(result.status.value).toBe('running')
    expect(result.events.value[0]).toMatchObject({ seq: 1, created_at: '2026-01-01T00:00:00Z' })
    lastSocket().emit({
      kind: 'event',
      seq: 2,
      type: 'run_finished',
      ts: '2026-01-01T00:00:01Z',
      payload: { status: 'succeeded' },
    })
    expect(result.status.value).toBe('succeeded')
    scope.stop()
  })

  it('recording adapter maps kind/confidence and event_count', () => {
    const recordingId = ref<string | null>('rec-1')
    const { result, scope } = setup(() => useRecordingEventsWs(recordingId, { maxEvents: 10 }))
    expect(lastSocket().url).toContain('/ws/recordings/rec-1?since_seq=0')
    lastSocket().open()
    lastSocket().emit({ kind: 'state', status: 'recording', last_seq: 0, event_count: 3 })
    expect(result.eventCount.value).toBe(3)
    lastSocket().emit({ kind: 'event', seq: 1, type: 'tap', ts: '2026-01-01T00:00:00Z', payload: {} })
    lastSocket().emit({
      kind: 'event',
      seq: 2,
      type: 'swipe',
      confidence: 'low',
      ts: '2026-01-01T00:00:01Z',
      payload: {},
    })
    expect(result.events.value[0]).toMatchObject({ seq: 1, kind: 'tap', confidence: 'high' })
    expect(result.events.value[1]).toMatchObject({ seq: 2, kind: 'swipe', confidence: 'low' })
    scope.stop()
  })
})
