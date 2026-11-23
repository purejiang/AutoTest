// 设置 / 健康 / 维护 hooks（契约 §7.6 + §7.5 maintenance）
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

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
  return useQuery<SettingsResponse>({
    queryKey: settingsKeys.detail(),
    queryFn: ({ signal }) => get<SettingsResponse>('/settings', { signal }),
    staleTime: 5000,
  })
}

export function useUpdateSettings() {
  const queryClient = useQueryClient()
  return useMutation<SettingsResponse, Error, Record<string, unknown>>({
    mutationFn: (values) => put<SettingsResponse>('/settings', { values }),
    onSuccess: (data) => {
      queryClient.setQueryData(settingsKeys.detail(), data)
      void queryClient.invalidateQueries({ queryKey: settingsKeys.all })
      void queryClient.invalidateQueries({ queryKey: settingsKeys.health })
    },
  })
}

export function useResetSettings() {
  const queryClient = useQueryClient()
  return useMutation<SettingsResponse, Error, void>({
    mutationFn: () => post<SettingsResponse>('/settings/reset'),
    onSuccess: (data) => {
      queryClient.setQueryData(settingsKeys.detail(), data)
      void queryClient.invalidateQueries({ queryKey: settingsKeys.all })
    },
  })
}

export function useDeleteSettingOverride() {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, string>({
    mutationFn: (key) => del<unknown>(`/settings/${encodeURIComponent(key)}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: settingsKeys.all })
    },
  })
}

// ---------------------------------------------------------------- 健康

export function useHealth(options: { enabled?: boolean } = {}) {
  return useQuery<HealthResponse>({
    queryKey: settingsKeys.health,
    queryFn: ({ signal }) => get<HealthResponse>('/health', { signal }),
    enabled: options.enabled ?? true,
    refetchInterval: 15000,
    retry: false,
    staleTime: 5000,
    // 后端未启动是预期状态，Header 自行展示离线，不弹全局错误提示
    meta: { suppressError: true },
  })
}

// ---------------------------------------------------------------- 维护

export function useMaintenanceStats(options: { enabled?: boolean } = {}) {
  return useQuery<MaintenanceStats>({
    queryKey: settingsKeys.maintenance,
    queryFn: ({ signal }) => get<MaintenanceStats>('/maintenance/stats', { signal }),
    enabled: options.enabled ?? true,
    retry: false,
    staleTime: 5000,
  })
}

export function useCleanup() {
  const queryClient = useQueryClient()
  return useMutation<CleanupResult, Error, CleanupPayload>({
    mutationFn: (payload) => post<CleanupResult>('/maintenance/cleanup', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: settingsKeys.maintenance })
    },
  })
}
