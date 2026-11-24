// logcat 手动采集 API（start/stop/status）；与 devices.ts 的“最近日志”查询互补。
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, toValue, type MaybeRefOrGetter } from 'vue'

import { get, post } from './client'

export interface LogcatStartPayload {
  /** 关联运行（可选）：手动采集一般不需要。 */
  run_id?: number
  /** 环形缓冲最大行数（可选，后端默认 capture.logcat_max_lines）。 */
  max_lines?: number
  /** P0 只接受不生效：后端响应 package_filter=false。 */
  package?: string
}

export interface LogcatCaptureStatus {
  ok?: boolean
  serial: string
  active: boolean
  /** 当前环形缓冲（活跃）或最近一次保存的行数。 */
  lines: number
  started_at?: string | null
  /** 停止后的保存路径；采集中为 null。 */
  path?: string | null
  size?: number
  max_lines?: number
  run_id?: number | null
  started_by_run?: boolean
  package?: string | null
  stopped_at?: string | null
  /** 最近一次已保存的采集摘要（无活跃采集时用于显示路径）。 */
  last?: LogcatCaptureStatus | null
}

export interface LogcatStartResult extends LogcatCaptureStatus {
  started?: boolean
  package_requested?: string | null
  package_filter?: boolean
  package_ignored?: boolean
  capture?: LogcatCaptureStatus | null
}

export interface LogcatStopResult {
  ok?: boolean
  serial: string
  active: boolean
  stopped?: boolean
  already_stopped?: boolean
  path?: string | null
  size?: number
  lines?: number
  started_at?: string | null
  stopped_at?: string | null
  run_id?: number | null
  url?: string | null
  last?: LogcatCaptureStatus | null
}

const base = (serial: string): string => `/devices/${encodeURIComponent(serial)}`

export const logcatKeys = {
  all: ['logcat-capture'] as const,
  status: (serial: string) => [...logcatKeys.all, serial, 'status'] as const,
}

export function useLogcatCaptureStatus(
  serial: MaybeRefOrGetter<string | null | undefined>,
  options: {
    enabled?: MaybeRefOrGetter<boolean>
    refetchInterval?: MaybeRefOrGetter<number | false>
  } = {},
) {
  const resolved = computed(() => toValue(serial))
  const enabled = computed(() => Boolean(resolved.value) && toValue(options.enabled ?? true))
  return useQuery({
    queryKey: computed(() => logcatKeys.status(resolved.value ?? '')),
    queryFn: ({ signal }) =>
      get<LogcatCaptureStatus>(`${base(resolved.value as string)}/logcat/status`, { signal }),
    enabled,
    refetchInterval: computed(() => (enabled.value ? toValue(options.refetchInterval ?? false) : false)),
    staleTime: 0,
    retry: false,
  })
}

export function useStartLogcatCapture(serial: MaybeRefOrGetter<string | null | undefined>) {
  const queryClient = useQueryClient()
  const resolved = computed(() => toValue(serial))
  return useMutation({
    mutationFn: (payload: LogcatStartPayload = {}) =>
      post<LogcatStartResult>(`${base(resolved.value as string)}/logcat/start`, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: logcatKeys.status(resolved.value ?? '') })
    },
  })
}

export function useStopLogcatCapture(serial: MaybeRefOrGetter<string | null | undefined>) {
  const queryClient = useQueryClient()
  const resolved = computed(() => toValue(serial))
  return useMutation({
    mutationFn: () => post<LogcatStopResult>(`${base(resolved.value as string)}/logcat/stop`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: logcatKeys.status(resolved.value ?? '') })
    },
  })
}
