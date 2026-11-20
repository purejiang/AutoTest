// 录制 hooks（契约 §7.4）
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type { Recording, RecordingEvent, RecordingStatus, Script } from '@/types/api'

import { del, get, itemsOf, post, totalOf, type ItemsLike } from './client'

export interface RecordingListResult {
  items: Recording[]
  total: number
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
}

/** to_script 响应在契约里未细化：兼容 {script_id}/{id}/{script} 三种形态。 */
export interface ConvertRecordingResult {
  script_id?: string
  script?: Script
  id?: string
  warnings?: string[]
  [key: string]: unknown
}

export const recordingKeys = {
  all: ['recordings'] as const,
  lists: () => [...recordingKeys.all, 'list'] as const,
  list: (params: { status?: RecordingStatus; limit?: number }) => [...recordingKeys.lists(), params] as const,
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
  params: { status?: RecordingStatus; limit?: number } = {},
  options: { refetchInterval?: number | false; enabled?: boolean } = {},
) {
  return useQuery<RecordingListResult>({
    queryKey: recordingKeys.list(params),
    queryFn: async ({ signal }) => {
      const raw = await get<ItemsLike<Recording>>('/recordings', {
        signal,
        query: { status: params.status, limit: params.limit ?? 100 },
      })
      return { items: itemsOf(raw), total: totalOf(raw) }
    },
    enabled: options.enabled ?? true,
    refetchInterval: options.refetchInterval ?? 5000,
    staleTime: 2000,
  })
}

export function useRecording(id: string | undefined) {
  return useQuery<Recording>({
    queryKey: recordingKeys.detail(id ?? ''),
    queryFn: ({ signal }) => get<Recording>(base(id as string), { signal }),
    enabled: Boolean(id),
    refetchInterval: (query) => {
      const data = query.state.data
      return data && data.status !== 'stopped' ? 4000 : false
    },
    staleTime: 1000,
  })
}

export function useRecordingEvents(
  id: string | undefined,
  options: { limit?: number; sinceSeq?: number; enabled?: boolean; refetchInterval?: number | false } = {},
) {
  const limit = options.limit ?? 1000
  return useQuery<RecordingEventsResult>({
    queryKey: recordingKeys.events(id ?? '', limit),
    queryFn: async ({ signal }) => {
      const raw = await get<ItemsLike<RecordingEvent>>(`${base(id as string)}/events`, {
        signal,
        query: { since_seq: options.sinceSeq ?? 0, limit },
      })
      return { items: itemsOf(raw), total: totalOf(raw) }
    },
    enabled: Boolean(id) && (options.enabled ?? true),
    refetchInterval: options.refetchInterval ?? 4000,
    staleTime: 1000,
  })
}

// ---------------------------------------------------------------- 变更

function invalidateRecording(queryClient: ReturnType<typeof useQueryClient>, id: string | undefined): void {
  void queryClient.invalidateQueries({ queryKey: recordingKeys.all })
  if (id) void queryClient.invalidateQueries({ queryKey: recordingKeys.detail(id) })
}

export function useCreateRecording() {
  const queryClient = useQueryClient()
  return useMutation<Recording, Error, CreateRecordingPayload>({
    mutationFn: (payload) => post<Recording>('/recordings', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: recordingKeys.all })
    },
  })
}

export function usePauseRecording(id: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, void>({
    mutationFn: () => post<unknown>(`${base(requireId(id))}/pause`),
    onSuccess: () => invalidateRecording(queryClient, id),
  })
}

export function useResumeRecording(id: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, void>({
    mutationFn: () => post<unknown>(`${base(requireId(id))}/resume`),
    onSuccess: () => invalidateRecording(queryClient, id),
  })
}

export function useStopRecording(id: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, void>({
    mutationFn: () => post<unknown>(`${base(requireId(id))}/stop`),
    onSuccess: () => invalidateRecording(queryClient, id),
  })
}

export function useDeleteRecordingEvent(id: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, number>({
    mutationFn: (seq) => del<unknown>(`${base(requireId(id))}/events/${seq}`),
    onSuccess: () => invalidateRecording(queryClient, id),
  })
}

export function useConvertRecording(id: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation<ConvertRecordingResult, Error, ConvertRecordingPayload>({
    mutationFn: (payload) => post<ConvertRecordingResult>(`${base(requireId(id))}/to_script`, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: recordingKeys.all })
      void queryClient.invalidateQueries({ queryKey: ['scripts'] })
    },
  })
}

export function useDeleteRecording() {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, string>({
    mutationFn: (id) => del<unknown>(base(id)),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: recordingKeys.all })
    },
  })
}
