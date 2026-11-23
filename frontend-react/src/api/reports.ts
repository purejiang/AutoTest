// 报告 hooks（契约 §7.5）
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

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

export function useReports(runId?: number) {
  return useQuery<Report[]>({
    queryKey: reportKeys.list(runId),
    queryFn: async ({ signal }) =>
      itemsOf(
        await get<ItemsLike<Report>>('/reports', {
          signal,
          query: { run_id: runId },
        }),
      ),
    refetchInterval: 5000,
    staleTime: 2000,
  })
}

export function useGenerateReport() {
  const queryClient = useQueryClient()
  return useMutation<Report, Error, GenerateReportPayload>({
    mutationFn: ({ run_id, include_video }) =>
      post<Report>(`/runs/${run_id}/report`, include_video === undefined ? {} : { include_video }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: reportKeys.all })
    },
  })
}

export function useDeleteReport() {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, string>({
    mutationFn: (id) => del<unknown>(`/reports/${encodeURIComponent(id)}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: reportKeys.all })
    },
  })
}

export function downloadReport(id: string, filename?: string): void {
  downloadFile(`/reports/${encodeURIComponent(id)}/download`, filename)
}
