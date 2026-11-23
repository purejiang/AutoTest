// 运行 / 步骤 / 事件 / 控制 hooks（契约 §7.3）
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, toValue, type MaybeRefOrGetter } from 'vue'

import type { BulkDeleteResult, Run, RunDetail, RunEvent } from '@/types/api'

import { del, get, itemsOf, post, totalOf, type ItemsLike } from './client'

export interface RunListParams {
  status?: string
  script_id?: string
  limit?: number
  offset?: number
}

export interface StartRunPayload {
  script_id: string
  serial?: string
  variables?: Record<string, string>
  debug?: { step_mode?: boolean; breakpoints?: string[] }
}

export interface RunEventsParams {
  sinceSeq?: number
  limit?: number
  enabled?: boolean
}

export interface DebugPayload {
  action: 'step' | 'continue' | 'run_from'
  step_id?: string
}

export interface ResumePayload {
  ack?: boolean
}

export type ResumeCheckResult = Record<string, unknown>

export interface RunListResult {
  items: Run[]
  total: number
}

export interface RunEventsResult {
  items: RunEvent[]
  total: number
}

const ACTIVE_RUN_STATUSES = new Set(['queued', 'running', 'paused', 'stopping'])

export function isActiveRun(status?: string | null): boolean {
  return status ? ACTIVE_RUN_STATUSES.has(status) : false
}

export const runKeys = {
  all: ['runs'] as const,
  lists: () => [...runKeys.all, 'list'] as const,
  list: (params: RunListParams) => [...runKeys.lists(), params] as const,
  detail: (id: number) => [...runKeys.all, 'detail', id] as const,
  events: (id: number, params: RunEventsParams) => [...runKeys.all, 'events', id, params] as const,
}

// ---------------------------------------------------------------- 查询

export function useRuns(
  params: MaybeRefOrGetter<RunListParams> = {},
  options: { refetchInterval?: number | false } = {},
) {
  const resolved = computed(() => toValue(params))
  return useQuery({
    queryKey: computed(() => runKeys.list(resolved.value)),
    queryFn: async ({ signal }) => {
      const raw = await get<ItemsLike<Run>>('/runs', {
        signal,
        query: {
          status: resolved.value.status,
          script_id: resolved.value.script_id,
          limit: resolved.value.limit,
          offset: resolved.value.offset,
        },
      })
      return { items: itemsOf(raw), total: totalOf(raw) }
    },
    refetchInterval: options.refetchInterval ?? 5000,
    staleTime: 2000,
  })
}

export function useRun(id: MaybeRefOrGetter<number | undefined>) {
  const resolved = computed(() => toValue(id))
  const enabled = computed(() => typeof resolved.value === 'number' && Number.isFinite(resolved.value))
  return useQuery({
    queryKey: computed(() => runKeys.detail(resolved.value ?? 0)),
    queryFn: ({ signal }) => get<RunDetail>(`/runs/${resolved.value}`, { signal }),
    enabled,
    refetchInterval: (query) => {
      const data = query.state.data
      return data && isActiveRun(data.status) ? 3000 : false
    },
    staleTime: 1000,
  })
}

export function useRunEvents(id: MaybeRefOrGetter<number | undefined>, params: RunEventsParams = {}) {
  const resolved = computed(() => toValue(id))
  const sinceSeq = params.sinceSeq ?? 0
  const limit = params.limit ?? 500
  return useQuery({
    queryKey: computed(() => runKeys.events(resolved.value ?? 0, { sinceSeq, limit })),
    queryFn: async ({ signal }) => {
      const raw = await get<ItemsLike<RunEvent>>(`/runs/${resolved.value}/events`, {
        signal,
        query: { since_seq: sinceSeq, limit },
      })
      return { items: itemsOf(raw), total: totalOf(raw) }
    },
    enabled: computed(
      () =>
        typeof resolved.value === 'number' && Number.isFinite(resolved.value) && (params.enabled ?? true),
    ),
    staleTime: 0,
  })
}

// ---------------------------------------------------------------- 失效

function invalidateRun(queryClient: ReturnType<typeof useQueryClient>, runId: MaybeRefOrGetter<number | undefined>): void {
  void queryClient.invalidateQueries({ queryKey: runKeys.all })
  const id = toValue(runId)
  if (typeof id === 'number') {
    void queryClient.invalidateQueries({ queryKey: runKeys.detail(id) })
  }
}

function requireRunId(id: number | undefined): number {
  if (typeof id !== 'number' || !Number.isFinite(id)) throw new Error('run id 未就绪')
  return id
}

// ---------------------------------------------------------------- 变更

export function useStartRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: StartRunPayload) => post<Run>('/runs', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: runKeys.all })
    },
  })
}

export function usePauseRun(runId: MaybeRefOrGetter<number | undefined>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => post<unknown>(`/runs/${requireRunId(toValue(runId))}/pause`),
    onSuccess: () => invalidateRun(queryClient, runId),
  })
}

export function useResumeCheck(runId: MaybeRefOrGetter<number | undefined>) {
  return useMutation({
    mutationFn: () => post<ResumeCheckResult>(`/runs/${requireRunId(toValue(runId))}/resume-check`),
  })
}

export function useResumeRun(runId: MaybeRefOrGetter<number | undefined>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: ResumePayload) =>
      post<unknown>(`/runs/${requireRunId(toValue(runId))}/resume`, {
        ack: payload.ack ?? true,
        resume_kind: 'hot',
      }),
    onSuccess: () => invalidateRun(queryClient, runId),
  })
}

export function useStopRun(runId: MaybeRefOrGetter<number | undefined>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => post<unknown>(`/runs/${requireRunId(toValue(runId))}/stop`),
    onSuccess: () => invalidateRun(queryClient, runId),
  })
}

export function useDebugRun(runId: MaybeRefOrGetter<number | undefined>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: DebugPayload) => post<unknown>(`/runs/${requireRunId(toValue(runId))}/debug`, payload),
    onSuccess: () => invalidateRun(queryClient, runId),
  })
}

export function useDeleteRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => del<unknown>(`/runs/${id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: runKeys.all })
    },
  })
}

export function useBulkDeleteRuns() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (ids: number[]) => post<BulkDeleteResult<number>>('/runs/bulk-delete', { ids }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: runKeys.all })
    },
  })
}
