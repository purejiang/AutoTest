// 设备与手动控制 API hooks（契约 §7.1）
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, toValue, type MaybeRefOrGetter } from 'vue'

import type { Device, HierarchyNode } from '@/types/api'

import { apiBlob, apiText, get, itemsOf, post, type ItemsLike } from './client'

// ---------------------------------------------------------------- 类型

export interface AppInfo {
  package: string
  activity?: string
  label?: string
  type?: string
  [key: string]: unknown
}

export interface MirrorStatusInfo {
  active: boolean
  width?: number
  height?: number
  rotation?: number
  [key: string]: unknown
}

export interface ConnectDevicePayload {
  address: string
}

export interface PointPayload {
  x?: number
  y?: number
  nx?: number
  ny?: number
}

export interface LongPressPayload extends PointPayload {
  duration_ms?: number
}

export interface SwipePayload {
  x1?: number
  y1?: number
  x2?: number
  y2?: number
  nx1?: number
  ny1?: number
  nx2?: number
  ny2?: number
  duration_ms?: number
}

export interface KeyPayload {
  key?: string
  keycode?: number
}

export interface TextPayload {
  text: string
  clear_first?: boolean
}

export interface AppActionPayload {
  action: 'launch' | 'stop' | 'clear' | 'restart'
  package?: string
  activity?: string
}

export interface ShellPayload {
  command: string
  timeout_ms?: number
}

export interface ScreenRecordStartPayload {
  name?: string
  bit_rate?: number
  time_limit_s?: number
}

export interface ScreenRecordLast {
  name: string
  path: string
  size: number
  url?: string | null
  stopped_at?: string
}

export interface ScreenRecordStatus {
  ok?: boolean
  serial: string
  /** 是否仍在录制（达到 time_limit 后为 false，但 pending 仍为 true） */
  active: boolean
  /** 设备端还有未拉回的 mp4：需要“停止并保存”收尾 */
  pending?: boolean
  name?: string
  time_limit_s?: number
  elapsed_s?: number
  remaining_s?: number
  started_at?: string
  last?: ScreenRecordLast | null
}

export interface ScreenRecordResult extends ScreenRecordStatus {
  path?: string
  size?: number
  url?: string | null
  recording?: boolean
  last?: ScreenRecordLast | null
}

export interface DeviceLogcatLine {
  line: string
}

export interface ConnectResult {
  ok: boolean
  address: string
  output?: string
}

export interface HierarchyResponse {
  node: HierarchyNode | null
  raw: string | null
  rawUnavailable?: string
}

const base = (serial: string): string => `/devices/${encodeURIComponent(serial)}`

export const deviceKeys = {
  all: ['devices'] as const,
  list: () => [...deviceKeys.all, 'list'] as const,
  info: (serial: string) => [...deviceKeys.all, serial, 'info'] as const,
  hierarchy: (serial: string, raw: boolean) => [...deviceKeys.all, serial, 'hierarchy', raw] as const,
  logcat: (serial: string, lines: number) => [...deviceKeys.all, serial, 'logcat', lines] as const,
  apps: (serial: string, keyword: string, type: string) => [...deviceKeys.all, serial, 'apps', keyword, type] as const,
  screenshot: (serial: string, nonce: number) => [...deviceKeys.all, serial, 'screenshot', nonce] as const,
  mirror: (serial: string) => [...deviceKeys.all, serial, 'mirror'] as const,
  screenrecord: (serial: string) => [...deviceKeys.all, serial, 'screenrecord'] as const,
}

// ---------------------------------------------------------------- 查询

export function useDevices(
  options: {
    refetchInterval?: number | false
    enabled?: MaybeRefOrGetter<boolean>
    /** 调用方自己展示错误时抑制全局 toast（如新建录制弹窗）。 */
    suppressError?: boolean
  } = {},
) {
  return useQuery({
    queryKey: deviceKeys.list(),
    queryFn: async ({ signal }) => itemsOf(await get<ItemsLike<Device>>('/devices', { signal })),
    enabled: computed(() => toValue(options.enabled ?? true)),
    refetchInterval: options.refetchInterval ?? 5000,
    staleTime: 2000,
    meta: { suppressError: options.suppressError },
  })
}

export function useDeviceInfo(serial: MaybeRefOrGetter<string | null | undefined>) {
  const resolved = computed(() => toValue(serial))
  return useQuery({
    queryKey: computed(() => deviceKeys.info(resolved.value ?? '')),
    queryFn: ({ signal }) => get<Device>(`${base(resolved.value as string)}/info`, { signal }),
    enabled: computed(() => Boolean(resolved.value)),
    refetchInterval: 10000,
  })
}

/** 层级树：后端返回 {node, raw?}；兼容根节点或 {root|tree} 包装。 */
export function useDeviceHierarchy(
  serial: MaybeRefOrGetter<string | null | undefined>,
  options: { raw?: MaybeRefOrGetter<boolean>; enabled?: MaybeRefOrGetter<boolean> } = {},
) {
  const resolved = computed(() => toValue(serial))
  const raw = computed(() => Boolean(toValue(options.raw ?? false)))
  return useQuery({
    queryKey: computed(() => deviceKeys.hierarchy(resolved.value ?? '', raw.value)),
    queryFn: async ({ signal }) => {
      const data = await get<unknown>(`${base(resolved.value as string)}/hierarchy`, {
        signal,
        query: { raw: raw.value },
      })
      return normalizeHierarchyResponse(data)
    },
    enabled: computed(() => Boolean(resolved.value) && toValue(options.enabled ?? true)),
    staleTime: 0,
    retry: false,
  })
}

function normalizeHierarchyResponse(data: unknown): HierarchyResponse {
  if (!data || typeof data !== 'object') return { node: null, raw: null }
  const record = data as Record<string, unknown>
  const candidates = [record.node, record.root, record.tree, data]
  let node: HierarchyNode | null = null
  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== 'object') continue
    const value = candidate as HierarchyNode
    if (typeof value.class_name === 'string' || Array.isArray(value.children)) {
      node = normalizeHierarchyNode(value)
      break
    }
  }
  return {
    node,
    raw: typeof record.raw === 'string' ? record.raw : null,
    rawUnavailable: typeof record.raw_unavailable === 'string' ? record.raw_unavailable : undefined,
  }
}

function normalizeHierarchyNode(candidate: HierarchyNode): HierarchyNode {
  return {
    class_name: candidate.class_name ?? '',
    text: candidate.text ?? '',
    resource_id: candidate.resource_id ?? '',
    content_desc: candidate.content_desc ?? '',
    package: candidate.package ?? '',
    bounds: Array.isArray(candidate.bounds) ? candidate.bounds : [0, 0, 0, 0],
    clickable: Boolean(candidate.clickable),
    enabled: candidate.enabled !== false,
    visible: candidate.visible !== false,
    children: Array.isArray(candidate.children) ? candidate.children : [],
  }
}

export function useDeviceLogcat(
  serial: MaybeRefOrGetter<string | null | undefined>,
  lines = 200,
  options: { enabled?: MaybeRefOrGetter<boolean>; refetchInterval?: MaybeRefOrGetter<number | false> } = {},
) {
  const resolved = computed(() => toValue(serial))
  return useQuery({
    queryKey: computed(() => deviceKeys.logcat(resolved.value ?? '', lines)),
    queryFn: async ({ signal }) => {
      const text = await apiText(`${base(resolved.value as string)}/logcat`, { signal, query: { lines } })
      return parseLogcat(text)
    },
    enabled: computed(() => Boolean(resolved.value) && toValue(options.enabled ?? true)),
    refetchInterval: computed(() => toValue(options.refetchInterval ?? false)),
    staleTime: 0,
    retry: false,
  })
}

function parseLogcat(text: string): string[] {
  if (!text) return []
  const trimmed = text.trim()
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try {
      const data = JSON.parse(trimmed) as unknown
      if (Array.isArray(data)) return data.map((item) => String(item))
      if (data && typeof data === 'object') {
        const record = data as { lines?: unknown; text?: unknown }
        if (Array.isArray(record.lines)) return record.lines.map((item) => String(item))
        if (typeof record.text === 'string') return record.text.split(/\r?\n/)
      }
    } catch {
      // 不是 JSON，按纯文本处理
    }
  }
  return text.split(/\r?\n/)
}

export function useDeviceApps(
  serial: MaybeRefOrGetter<string | null | undefined>,
  params: { keyword?: MaybeRefOrGetter<string>; type?: MaybeRefOrGetter<string>; enabled?: MaybeRefOrGetter<boolean> } = {},
) {
  const resolved = computed(() => toValue(serial))
  const keyword = computed(() => toValue(params.keyword ?? ''))
  const type = computed(() => toValue(params.type ?? ''))
  return useQuery({
    queryKey: computed(() => deviceKeys.apps(resolved.value ?? '', keyword.value, type.value)),
    queryFn: async ({ signal }) =>
      itemsOf(
        await get<ItemsLike<AppInfo>>(`${base(resolved.value as string)}/apps`, {
          signal,
          // 空 type 不能直接发给后端：默认值需要走后端 third_party，显式传空会被判为非法。
          query: { keyword: keyword.value, type: type.value || undefined },
        }),
      ),
    enabled: computed(() => Boolean(resolved.value) && toValue(params.enabled ?? true)),
    staleTime: 15000,
    retry: false,
  })
}

/** 截图是二进制 PNG，返回 Blob 由调用方转 objectURL。 */
export function useDeviceScreenshot(
  serial: MaybeRefOrGetter<string | null | undefined>,
  options: { enabled?: MaybeRefOrGetter<boolean>; nonce?: MaybeRefOrGetter<number> } = {},
) {
  const resolved = computed(() => toValue(serial))
  const nonce = computed(() => toValue(options.nonce ?? 0))
  return useQuery({
    queryKey: computed(() => deviceKeys.screenshot(resolved.value ?? '', nonce.value)),
    queryFn: ({ signal }) => apiBlob(`${base(resolved.value as string)}/screenshot`, { signal }),
    enabled: computed(() => Boolean(resolved.value) && toValue(options.enabled ?? true)),
    staleTime: 0,
    retry: false,
  })
}

export function useMirrorStatus(serial: MaybeRefOrGetter<string | null | undefined>, enabled: MaybeRefOrGetter<boolean> = true) {
  const resolved = computed(() => toValue(serial))
  return useQuery({
    queryKey: computed(() => deviceKeys.mirror(resolved.value ?? '')),
    queryFn: ({ signal }) => get<MirrorStatusInfo>(`${base(resolved.value as string)}/mirror/status`, { signal }),
    enabled: computed(() => Boolean(resolved.value) && toValue(enabled)),
    refetchInterval: 5000,
    retry: false,
  })
}

// ---------------------------------------------------------------- 设备管理

export function useConnectDevice() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: ConnectDevicePayload) => post<ConnectResult>('/devices/connect', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: deviceKeys.all })
    },
  })
}

export function useDisconnectDevice() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (serial: string) => post<unknown>(`${base(serial)}/disconnect`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: deviceKeys.all })
    },
  })
}

// ---------------------------------------------------------------- 手动控制

export function useDeviceTap(serial: string) {
  return useMutation({ mutationFn: (payload: PointPayload) => post<unknown>(`${base(serial)}/tap`, payload) })
}

export function useDeviceLongPress(serial: string) {
  return useMutation({ mutationFn: (payload: LongPressPayload) => post<unknown>(`${base(serial)}/long_press`, payload) })
}

export function useDeviceSwipe(serial: string) {
  return useMutation({ mutationFn: (payload: SwipePayload) => post<unknown>(`${base(serial)}/swipe`, payload) })
}

export function useDeviceKey(serial: string) {
  return useMutation({ mutationFn: (payload: KeyPayload) => post<unknown>(`${base(serial)}/key`, payload) })
}

export function useDeviceText(serial: string) {
  return useMutation({ mutationFn: (payload: TextPayload) => post<unknown>(`${base(serial)}/text`, payload) })
}

export function useDeviceAppAction(serial: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: AppActionPayload) => post<unknown>(`${base(serial)}/app`, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: deviceKeys.list() })
    },
  })
}

export function useDeviceShell(serial: string) {
  return useMutation({ mutationFn: (payload: ShellPayload) => post<unknown>(`${base(serial)}/shell`, payload) })
}

// ---------------------------------------------------------------- 录屏 / 镜像

export function useScreenRecordStart(serial: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: ScreenRecordStartPayload) =>
      post<ScreenRecordResult>(`${base(serial)}/screenrecord/start`, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: deviceKeys.screenrecord(serial) })
    },
  })
}

export function useScreenRecordStop(serial: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => post<ScreenRecordResult>(`${base(serial)}/screenrecord/stop`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: deviceKeys.screenrecord(serial) })
      void queryClient.invalidateQueries({ queryKey: deviceKeys.list() })
    },
  })
}

export function useScreenRecordStatus(
  serial: MaybeRefOrGetter<string | null | undefined>,
  enabled: MaybeRefOrGetter<boolean> = true,
) {
  const resolved = computed(() => toValue(serial))
  return useQuery({
    queryKey: computed(() => deviceKeys.screenrecord(resolved.value ?? '')),
    queryFn: ({ signal }) =>
      get<ScreenRecordStatus>(`${base(resolved.value as string)}/screenrecord/status`, { signal }),
    enabled: computed(() => Boolean(resolved.value) && toValue(enabled)),
    refetchInterval: 1000,
    retry: false,
  })
}

export function useMirrorStart(serial: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => post<MirrorStatusInfo>(`${base(serial)}/mirror/start`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: deviceKeys.mirror(serial) })
      void queryClient.invalidateQueries({ queryKey: deviceKeys.list() })
    },
  })
}

export function useMirrorStop(serial: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => post<unknown>(`${base(serial)}/mirror/stop`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: deviceKeys.mirror(serial) })
      void queryClient.invalidateQueries({ queryKey: deviceKeys.list() })
    },
  })
}
