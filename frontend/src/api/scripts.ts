// 脚本 CRUD / 校验 / 导入导出 hooks（契约 §7.2）
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type { Script, ScriptData, ScriptValidation } from '@/types/api'

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

export function useScripts(params: ScriptListParams = {}, options: { enabled?: boolean } = {}) {
  return useQuery<ScriptListResult>({
    queryKey: scriptKeys.list(params),
    queryFn: async ({ signal }) => {
      const raw = await get<ItemsLike<Script>>('/scripts', {
        signal,
        query: { query: params.query, limit: params.limit, offset: params.offset },
      })
      return { items: itemsOf(raw), total: totalOf(raw) }
    },
    enabled: options.enabled ?? true,
    staleTime: 5000,
  })
}

export function useScript(id: string | undefined, options: { enabled?: boolean } = {}) {
  return useQuery<Script>({
    queryKey: scriptKeys.detail(id ?? ''),
    queryFn: ({ signal }) => get<Script>(`/scripts/${encodeURIComponent(id as string)}`, { signal }),
    enabled: Boolean(id) && (options.enabled ?? true),
    staleTime: 10000,
  })
}

// ---------------------------------------------------------------- 变更

export function useCreateScript() {
  const queryClient = useQueryClient()
  return useMutation<Script, Error, CreateScriptPayload>({
    mutationFn: (payload) => post<Script>('/scripts', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scriptKeys.all })
    },
  })
}

export function useUpdateScript(id: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation<Script, Error, UpdateScriptPayload>({
    mutationFn: (payload) => put<Script>(`/scripts/${encodeURIComponent(id as string)}`, payload),
    onSuccess: (script) => {
      if (id) queryClient.setQueryData(scriptKeys.detail(id), script)
      void queryClient.invalidateQueries({ queryKey: scriptKeys.all })
    },
  })
}

export function useDeleteScript() {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, string>({
    mutationFn: (id) => del<unknown>(`/scripts/${encodeURIComponent(id)}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scriptKeys.all })
    },
  })
}

export function useDuplicateScript() {
  const queryClient = useQueryClient()
  return useMutation<Script, Error, { id: string; name?: string }>({
    mutationFn: ({ id, name }) =>
      post<Script>(`/scripts/${encodeURIComponent(id)}/duplicate`, name ? { name } : {}),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scriptKeys.all })
    },
  })
}

export function useValidateScriptData() {
  return useMutation<ScriptValidation, Error, { data: unknown }>({
    mutationFn: (payload) => post<ScriptValidation>('/scripts/validate', payload),
  })
}

export function useImportScript() {
  const queryClient = useQueryClient()
  return useMutation<Script, Error, ImportScriptPayload>({
    mutationFn: (payload) => post<Script>('/scripts/import', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scriptKeys.all })
    },
  })
}

/** 导出 json 附件（浏览器原生下载，经 vite proxy/后端静态）。 */
export function exportScript(id: string, name?: string): void {
  downloadFile(`/scripts/${encodeURIComponent(id)}/export`, name ? `${name}.json` : undefined)
}
