// 运行 / 步骤 / 事件 / 控制 hooks（契约 §7.3）
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type { Run, RunDetail, RunEvent } from '@/types/api'

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

export function useRuns(params: RunListParams = {}, options: { refetchInterval?: number | false } = {}) {
  return useQuery<RunListResult>({
    queryKey: runKeys.list(params),
    queryFn: async ({ signal }) => {
      const raw = await get<ItemsLike<Run>>('/runs', {
        signal,
        query: {
          status: params.status,
          script_id: params.script_id,
          limit: params.limit,
          offset: params.offset,
        },
      })
      return { items: itemsOf(raw), total: totalOf(raw) }
    },
    refetchInterval: options.refetchInterval ?? 5000,
    staleTime: 2000,
  })
}

export function useRun(id: number | undefined) {
  const enabled = typeof id === 'number' && Number.isFinite(id)
  return useQuery<RunDetail>({
    queryKey: runKeys.detail(id ?? 0),
    queryFn: ({ signal }) => get<RunDetail>(`/runs/${id}`, { signal }),
    enabled,
    refetchInterval: (query) => {
      const data = query.state.data
      return data && isActiveRun(data.status) ? 3000 : false
    },
    staleTime: 1000,
  })
}

export function useRunEvents(id: number | undefined, params: RunEventsParams = {}) {
  const sinceSeq = params.sinceSeq ?? 0
  const limit = params.limit ?? 500
  return useQuery<RunEventsResult>({
    queryKey: runKeys.events(id ?? 0, { sinceSeq, limit }),
    queryFn: async ({ signal }) => {
      const raw = await get<ItemsLike<RunEvent>>(`/runs/${id}/events`, {
        signal,
        query: { since_seq: sinceSeq, limit },
      })
      return { items: itemsOf(raw), total: totalOf(raw) }
    },
    enabled: typeof id === 'number' && Number.isFinite(id) && (params.enabled ?? true),
    staleTime: 0,
  })
}

// ---------------------------------------------------------------- 失效

function invalidateRun(queryClient: ReturnType<typeof useQueryClient>, runId: number | undefined): void {
  void queryClient.invalidateQueries({ queryKey: runKeys.all })
  if (typeof runId === 'number') {
    void queryClient.invalidateQueries({ queryKey: runKeys.detail(runId) })
  }
}

function requireRunId(id: number | undefined): number {
  if (typeof id !== 'number' || !Number.isFinite(id)) throw new Error('run id 未就绪')
  return id
}

// ---------------------------------------------------------------- 变更

export function useStartRun() {
  const queryClient = useQueryClient()
  return useMutation<Run, Error, StartRunPayload>({
    mutationFn: (payload) => post<Run>('/runs', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: runKeys.all })
    },
  })
}

export function usePauseRun(runId: number | undefined) {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, void>({
    mutationFn: () => post<unknown>(`/runs/${requireRunId(runId)}/pause`),
    onSuccess: () => invalidateRun(queryClient, runId),
  })
}

export function useResumeCheck(runId: number | undefined) {
  return useMutation<ResumeCheckResult, Error, void>({
    mutationFn: () => post<ResumeCheckResult>(`/runs/${requireRunId(runId)}/resume-check`),
  })
}

export function useResumeRun(runId: number | undefined) {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, ResumePayload>({
    mutationFn: (payload) =>
      post<unknown>(`/runs/${requireRunId(runId)}/resume`, {
        ack: payload.ack ?? true,
        resume_kind: 'hot',
      }),
    onSuccess: () => invalidateRun(queryClient, runId),
  })
}

export function useStopRun(runId: number | undefined) {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, void>({
    mutationFn: () => post<unknown>(`/runs/${requireRunId(runId)}/stop`),
    onSuccess: () => invalidateRun(queryClient, runId),
  })
}

export function useDebugRun(runId: number | undefined) {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, DebugPayload>({
    mutationFn: (payload) => post<unknown>(`/runs/${requireRunId(runId)}/debug`, payload),
    onSuccess: () => invalidateRun(queryClient, runId),
  })
}

export function useDeleteRun() {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, number>({
    mutationFn: (id) => del<unknown>(`/runs/${id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: runKeys.all })
    },
  })
}
