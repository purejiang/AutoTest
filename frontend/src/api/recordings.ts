// 录制 hooks（契约 §7.4）
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, toValue, type MaybeRefOrGetter } from 'vue'

import type { BulkDeleteResult, Recording, RecordingEvent, RecordingStatus, Script } from '@/types/api'

import { del, get, itemsOf, post, totalOf, type ItemsLike } from './client'

export interface RecordingListResult {
  items: Recording[]
  total: number
}

export interface RecordingListParams {
  status?: RecordingStatus
  mode?: 'web' | 'physical'
  limit?: number
  offset?: number
}

export interface RecordingEventsResult {
  items: RecordingEvent[]
  total: number
}

export interface CreateRecordingPayload {
  serial: string
  mode?: 'web' | 'physical'
  name?: string
}

export interface ConvertRecordingPayload {
  name?: string
  include_kinds?: string[]
  exclude_seqs?: number[]
  target_mode?: 'point' | 'element'
}

export interface ConvertRecordingResult {
  script_id?: string
  script?: Script
  id?: string
  name?: string
  warnings?: Array<string | { seq?: number; reason?: string }>
  [key: string]: unknown
}

export const recordingKeys = {
  all: ['recordings'] as const,
  lists: () => [...recordingKeys.all, 'list'] as const,
  list: (params: RecordingListParams) => [...recordingKeys.lists(), params] as const,
  detail: (id: string) => [...recordingKeys.all, 'detail', id] as const,
  events: (id: string, limit: number) => [...recordingKeys.all, 'events', id, limit] as const,
}

const base = (id: string): string => `/recordings/${encodeURIComponent(id)}`

function requireId(id: string | undefined): string {
  if (!id) throw new Error('recording id 未就绪')
  return id
}

// ---------------------------------------------------------------- 查询

export function useRecordings(
  params: MaybeRefOrGetter<RecordingListParams> = {},
  options: { refetchInterval?: number | false; enabled?: MaybeRefOrGetter<boolean> } = {},
) {
  const resolved = computed(() => toValue(params))
  return useQuery({
    queryKey: computed(() => recordingKeys.list(resolved.value)),
    queryFn: async ({ signal }) => {
      const raw = await get<ItemsLike<Recording>>('/recordings', {
        signal,
        query: {
          status: resolved.value.status,
          mode: resolved.value.mode,
          limit: resolved.value.limit ?? 100,
          offset: resolved.value.offset ?? 0,
        },
      })
      return { items: itemsOf(raw), total: totalOf(raw) }
    },
    enabled: computed(() => toValue(options.enabled ?? true)),
    refetchInterval: options.refetchInterval ?? 5000,
    staleTime: 2000,
  })
}

export function useRecording(id: MaybeRefOrGetter<string | undefined>) {
  const resolved = computed(() => toValue(id))
  return useQuery({
    queryKey: computed(() => recordingKeys.detail(resolved.value ?? '')),
    queryFn: ({ signal }) => get<Recording>(base(resolved.value as string), { signal }),
    enabled: computed(() => Boolean(resolved.value)),
    refetchInterval: (query) => {
      const data = query.state.data
      return data && data.status !== 'stopped' ? 4000 : false
    },
    staleTime: 1000,
  })
}

export function useRecordingEvents(
  id: MaybeRefOrGetter<string | undefined>,
  options: {
    limit?: number
    sinceSeq?: number
    enabled?: MaybeRefOrGetter<boolean>
    refetchInterval?: MaybeRefOrGetter<number | false>
  } = {},
) {
  const resolved = computed(() => toValue(id))
  const limit = options.limit ?? 1000
  return useQuery({
    queryKey: computed(() => recordingKeys.events(resolved.value ?? '', limit)),
    queryFn: async ({ signal }) => {
      const raw = await get<ItemsLike<RecordingEvent>>(`${base(resolved.value as string)}/events`, {
        signal,
        query: { since_seq: options.sinceSeq ?? 0, limit },
      })
      return { items: itemsOf(raw), total: totalOf(raw) }
    },
    enabled: computed(() => Boolean(resolved.value) && toValue(options.enabled ?? true)),
    refetchInterval: computed(() => toValue(options.refetchInterval ?? 4000)),
    staleTime: 1000,
  })
}

// ---------------------------------------------------------------- 变更

function invalidateRecording(
  queryClient: ReturnType<typeof useQueryClient>,
  id: MaybeRefOrGetter<string | undefined>,
): void {
  void queryClient.invalidateQueries({ queryKey: recordingKeys.all })
  const value = toValue(id)
  if (value) void queryClient.invalidateQueries({ queryKey: recordingKeys.detail(value) })
}

export function useCreateRecording() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: CreateRecordingPayload) => post<Recording>('/recordings', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: recordingKeys.all })
    },
  })
}

export function usePauseRecording(id: MaybeRefOrGetter<string | undefined>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => post<unknown>(`${base(requireId(toValue(id)))}/pause`),
    onSuccess: () => invalidateRecording(queryClient, id),
  })
}

export function useResumeRecording(id: MaybeRefOrGetter<string | undefined>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => post<unknown>(`${base(requireId(toValue(id)))}/resume`),
    onSuccess: () => invalidateRecording(queryClient, id),
  })
}

export function useStopRecording(id: MaybeRefOrGetter<string | undefined>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => post<unknown>(`${base(requireId(toValue(id)))}/stop`),
    onSuccess: () => invalidateRecording(queryClient, id),
  })
}

export function useDeleteRecordingEvent(id: MaybeRefOrGetter<string | undefined>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (seq: number) => del<unknown>(`${base(requireId(toValue(id)))}/events/${seq}`),
    onSuccess: () => invalidateRecording(queryClient, id),
  })
}

export function useConvertRecording(id: MaybeRefOrGetter<string | undefined>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: ConvertRecordingPayload) =>
      post<ConvertRecordingResult>(`${base(requireId(toValue(id)))}/to_script`, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: recordingKeys.all })
      void queryClient.invalidateQueries({ queryKey: ['scripts'] })
    },
  })
}

export function useDeleteRecording() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => del<unknown>(base(id)),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: recordingKeys.all })
    },
  })
}

export function useBulkDeleteRecordings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (ids: string[]) => post<BulkDeleteResult<string>>('/recordings/bulk-delete', { ids }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: recordingKeys.all })
    },
  })
}

export function useBulkDeleteRecordingEvents(id: MaybeRefOrGetter<string | undefined>) {
  const queryClient = useQueryClient()
  const resolved = computed(() => toValue(id))
  return useMutation({
    mutationFn: (seqs: number[]) =>
      post<BulkDeleteResult<number>>(
        `/recordings/${encodeURIComponent(requireId(resolved.value))}/events/bulk-delete`,
        { seqs },
      ),
    onSuccess: () => invalidateRecording(queryClient, id),
  })
}
