// 网络抓包（域名模式）API：start / stop / status
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, toValue, type MaybeRefOrGetter } from 'vue'

import { get, post } from './client'

/** 域名采集状态：后端只返回域名摘要，不包含 header/body/path/query。 */
export interface NetworkCaptureInfo {
  serial: string
  status?: 'running' | 'stopped'
  active: boolean
  mode?: string
  format?: string
  /** 代理监听端口（127.0.0.1） */
  port: number | null
  /** JSONL 产物路径；停止后保留最近一次路径 */
  path: string | null
  size: number
  domain_count: number
  record_count?: number
  /** 最近域名（新→旧） */
  recent_domains: string[]
  started_at?: string
  [key: string]: unknown
}

export interface NetworkStartResult {
  ok: boolean
  serial: string
  started: boolean
  capture: NetworkCaptureInfo
}

export interface NetworkStopResult {
  ok: boolean
  serial: string
  started: boolean
  capture: NetworkCaptureInfo
}

export const networkKeys = {
  all: ['network'] as const,
  status: (serial: string) => [...networkKeys.all, serial, 'status'] as const,
}

const base = (serial: string): string => `/devices/${encodeURIComponent(serial)}/network`
const normalized = (serial: string | null | undefined): string | null => {
  const value = (serial ?? '').trim()
  return value ? value : null
}

export function useNetworkStatus(
  serial: MaybeRefOrGetter<string | null | undefined>,
  enabled: MaybeRefOrGetter<boolean> = true,
) {
  const resolved = computed(() => normalized(toValue(serial)))
  return useQuery({
    queryKey: computed(() => networkKeys.status(resolved.value ?? '')),
    queryFn: ({ signal }) => get<NetworkCaptureInfo>(`${base(resolved.value as string)}/status`, { signal }),
    enabled: computed(() => Boolean(resolved.value) && toValue(enabled)),
    // 抓包中实时刷新域名列表；未开始时也保持较低频率以便显示最近一次结果。
    refetchInterval: 1500,
    retry: false,
  })
}

export function useNetworkStart(serial: MaybeRefOrGetter<string | null | undefined>) {
  const queryClient = useQueryClient()
  const resolved = computed(() => normalized(toValue(serial)))
  return useMutation({
    mutationFn: () => post<NetworkStartResult>(`${base(resolved.value as string)}/start`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: networkKeys.status(resolved.value ?? '') })
    },
  })
}

export function useNetworkStop(serial: MaybeRefOrGetter<string | null | undefined>) {
  const queryClient = useQueryClient()
  const resolved = computed(() => normalized(toValue(serial)))
  return useMutation({
    mutationFn: () => post<NetworkStopResult>(`${base(resolved.value as string)}/stop`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: networkKeys.status(resolved.value ?? '') })
    },
  })
}
