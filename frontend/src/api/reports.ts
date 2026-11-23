// 报告 hooks（契约 §7.5）
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, toValue, type MaybeRefOrGetter } from 'vue'

import type { Report } from '@/types/api'

import { del, downloadFile, get, itemsOf, post, type ItemsLike } from './client'

export interface GenerateReportPayload {
  run_id: number
  include_video?: boolean
}

export const reportKeys = {
  all: ['reports'] as const,
  list: (runId?: number) => [...reportKeys.all, 'list', runId ?? 'all'] as const,
}

export function useReports(runId: MaybeRefOrGetter<number | undefined>) {
  const resolved = computed(() => toValue(runId))
  return useQuery({
    queryKey: computed(() => reportKeys.list(resolved.value)),
    queryFn: async ({ signal }) =>
      itemsOf(
        await get<ItemsLike<Report>>('/reports', {
          signal,
          query: { run_id: resolved.value },
        }),
      ),
    refetchInterval: 5000,
    staleTime: 2000,
  })
}

export function useGenerateReport() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ run_id, include_video }: GenerateReportPayload) =>
      post<Report>(`/runs/${run_id}/report`, include_video === undefined ? {} : { include_video }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: reportKeys.all })
    },
  })
}

export function useDeleteReport() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => del<unknown>(`/reports/${encodeURIComponent(id)}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: reportKeys.all })
    },
  })
}

export function downloadReport(id: string, filename?: string): void {
  downloadFile(`/reports/${encodeURIComponent(id)}/download`, filename)
}
