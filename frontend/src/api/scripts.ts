// 脚本 CRUD / 校验 / 导入导出 hooks（契约 §7.2）
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, toValue, type MaybeRefOrGetter } from 'vue'

import type { BulkDeleteResult, Script, ScriptData, ScriptValidation } from '@/types/api'

import { del, downloadFile, get, itemsOf, post, put, totalOf, type ItemsLike } from './client'

export interface ScriptListParams {
  query?: string
  limit?: number
  offset?: number
}

export interface CreateScriptPayload {
  name: string
  notes?: string
  data?: ScriptData | Record<string, unknown>
}

export interface UpdateScriptPayload {
  name?: string
  notes?: string
  data?: ScriptData
}

export interface DuplicateScriptPayload {
  name?: string
}

export interface ImportScriptPayload {
  data: unknown
}

export interface ScriptListResult {
  items: Script[]
  total: number
}

export const scriptKeys = {
  all: ['scripts'] as const,
  lists: () => [...scriptKeys.all, 'list'] as const,
  list: (params: ScriptListParams) => [...scriptKeys.lists(), params] as const,
  detail: (id: string) => [...scriptKeys.all, 'detail', id] as const,
}

// ---------------------------------------------------------------- 查询

export function useScripts(
  params: MaybeRefOrGetter<ScriptListParams> = {},
  options: { enabled?: MaybeRefOrGetter<boolean> } = {},
) {
  const resolved = computed(() => toValue(params))
  return useQuery({
    queryKey: computed(() => scriptKeys.list(resolved.value)),
    queryFn: async ({ signal }) => {
      const raw = await get<ItemsLike<Script>>('/scripts', {
        signal,
        query: {
          query: resolved.value.query,
          limit: resolved.value.limit,
          offset: resolved.value.offset,
        },
      })
      return { items: itemsOf(raw), total: totalOf(raw) }
    },
    enabled: computed(() => toValue(options.enabled ?? true)),
    staleTime: 5000,
  })
}

export function useScript(id: MaybeRefOrGetter<string | undefined>, options: { enabled?: boolean } = {}) {
  const resolved = computed(() => toValue(id))
  return useQuery({
    queryKey: computed(() => scriptKeys.detail(resolved.value ?? '')),
    queryFn: ({ signal }) => get<Script>(`/scripts/${encodeURIComponent(resolved.value as string)}`, { signal }),
    enabled: computed(() => Boolean(resolved.value) && (options.enabled ?? true)),
    staleTime: 10000,
  })
}

// ---------------------------------------------------------------- 变更

export function useCreateScript() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: CreateScriptPayload) => post<Script>('/scripts', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scriptKeys.all })
    },
  })
}

export function useUpdateScript(id: MaybeRefOrGetter<string | undefined>) {
  const queryClient = useQueryClient()
  const resolved = computed(() => toValue(id))
  return useMutation({
    mutationFn: (payload: UpdateScriptPayload) =>
      put<Script>(`/scripts/${encodeURIComponent(resolved.value as string)}`, payload),
    onSuccess: (script) => {
      if (resolved.value) queryClient.setQueryData(scriptKeys.detail(resolved.value), script)
      void queryClient.invalidateQueries({ queryKey: scriptKeys.all })
    },
  })
}

export function useDeleteScript() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => del<unknown>(`/scripts/${encodeURIComponent(id)}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scriptKeys.all })
    },
  })
}

export function useBulkDeleteScripts() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (ids: string[]) => post<BulkDeleteResult<string>>('/scripts/bulk-delete', { ids }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scriptKeys.all })
    },
  })
}

export function useDuplicateScript() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, name }: { id: string; name?: string }) =>
      post<Script>(`/scripts/${encodeURIComponent(id)}/duplicate`, name ? { name } : {}),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scriptKeys.all })
    },
  })
}

export function useValidateScriptData() {
  return useMutation({
    mutationFn: (payload: { data: unknown }) => post<ScriptValidation>('/scripts/validate', payload),
  })
}

export function useImportScript() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: ImportScriptPayload) => post<Script>('/scripts/import', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scriptKeys.all })
    },
  })
}

/** 导出 json 附件（浏览器原生下载，经 vite proxy/后端静态）。 */
export function exportScript(id: string, name?: string): void {
  downloadFile(`/scripts/${encodeURIComponent(id)}/export`, name ? `${name}.json` : undefined)
}
