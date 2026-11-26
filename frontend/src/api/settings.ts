// 设置 / 健康 / 维护 hooks（契约 §7.6 + §7.5 maintenance）
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, toValue, type MaybeRefOrGetter } from 'vue'

import type { HealthResponse, MaintenanceStats, SettingsResponse } from '@/types/api'

import { del, get, post, put } from './client'

export interface CleanupPayload {
  retention_days?: number
  dry_run?: boolean
}

export type CleanupResult = Record<string, unknown>

export const settingsKeys = {
  all: ['settings'] as const,
  detail: () => [...settingsKeys.all, 'detail'] as const,
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
