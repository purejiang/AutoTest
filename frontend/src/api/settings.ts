// 设置 / 健康 / 维护 hooks（契约 §7.6 + §7.5 maintenance）
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, toValue, type MaybeRefOrGetter } from 'vue'

import type { HealthResponse, MaintenanceStats, SettingEntry, SettingsResponse } from '@/types/api'

import { del, get, post, put } from './client'

export interface CleanupPayload {
  retention_days?: number
  dry_run?: boolean
}

export type CleanupResult = Record<string, unknown>

export type SettingSource = 'default' | 'file' | 'env'

/** GET /api/settings/scrcpy/options 的单项：设置 schema + 当前生效值/来源。 */
export interface ScrcpyOptionEntry extends SettingEntry {
  value: unknown
  source: SettingSource
}

/** 镜像参数白名单 + scrcpy-server 版本/内置 jar 信息（后端为唯一事实来源）。 */
export interface ScrcpyOptionsResponse {
  server_version: string
  codec: string
  jar: string
  jar_present: boolean
  items: ScrcpyOptionEntry[]
}

export const settingsKeys = {
  all: ['settings'] as const,
  detail: () => [...settingsKeys.all, 'detail'] as const,
  scrcpyOptions: () => [...settingsKeys.all, 'scrcpy-options'] as const,
  health: ['health'] as const,
  maintenance: ['maintenance'] as const,
}

// ---------------------------------------------------------------- 设置

export function useSettings() {
  return useQuery({
    queryKey: settingsKeys.detail(),
    queryFn: ({ signal }) => get<SettingsResponse>('/settings', { signal }),
    staleTime: 5000,
  })
}

/** scrcpy-server 版本/内置 jar 由后端返回，避免前端再写一份版本号。 */
export function useScrcpyOptions() {
  return useQuery({
    queryKey: settingsKeys.scrcpyOptions(),
    queryFn: ({ signal }) => get<ScrcpyOptionsResponse>('/settings/scrcpy/options', { signal }),
    staleTime: 60_000,
  })
}

export function useUpdateSettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (values: Record<string, unknown>) => put<SettingsResponse>('/settings', { values }),
    onSuccess: (data) => {
      queryClient.setQueryData(settingsKeys.detail(), data)
      void queryClient.invalidateQueries({ queryKey: settingsKeys.all })
      void queryClient.invalidateQueries({ queryKey: settingsKeys.health })
    },
  })
}

export function useResetSettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => post<SettingsResponse>('/settings/reset'),
    onSuccess: (data) => {
      queryClient.setQueryData(settingsKeys.detail(), data)
      void queryClient.invalidateQueries({ queryKey: settingsKeys.all })
    },
  })
}

export function useReloadSettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => post<SettingsResponse>('/settings/reload'),
    onSuccess: (data) => {
      queryClient.setQueryData(settingsKeys.detail(), data)
      void queryClient.invalidateQueries({ queryKey: settingsKeys.all })
      void queryClient.invalidateQueries({ queryKey: settingsKeys.health })
    },
  })
}

export function useDeleteSettingOverride() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (key: string) => del<unknown>(`/settings/${encodeURIComponent(key)}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: settingsKeys.all })
    },
  })
}

// ---------------------------------------------------------------- 健康

export function useHealth(options: { enabled?: MaybeRefOrGetter<boolean> } = {}) {
  return useQuery({
    queryKey: settingsKeys.health,
    queryFn: ({ signal }) => get<HealthResponse>('/health', { signal }),
    enabled: computed(() => toValue(options.enabled ?? true)),
    refetchInterval: 15000,
    retry: false,
    staleTime: 5000,
    meta: { suppressError: true },
  })
}

// ---------------------------------------------------------------- 维护

export function useMaintenanceStats(options: { enabled?: MaybeRefOrGetter<boolean> } = {}) {
  return useQuery({
    queryKey: settingsKeys.maintenance,
    queryFn: ({ signal }) => get<MaintenanceStats>('/maintenance/stats', { signal }),
    enabled: computed(() => toValue(options.enabled ?? true)),
    retry: false,
    staleTime: 5000,
  })
}

export function useCleanup() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: CleanupPayload) => post<CleanupResult>('/maintenance/cleanup', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: settingsKeys.maintenance })
    },
  })
}
