// 设备与手动控制 API hooks（契约 §7.1）
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type { Device, HierarchyNode, ItemsResponse } from '@/types/api'

import { apiBlob, apiText, get, itemsOf, post, type ItemsLike } from './client'

// ---------------------------------------------------------------- 类型（契约未进 types/api.ts 的响应细节）

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

/** 绝对坐标 (x,y) 与归一化坐标 (nx,ny) 二选一。 */
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

export interface ScreenRecordResult {
  [key: string]: unknown
}

export interface DeviceLogcatLine {
  line: string
}

export interface ConnectResult {
  ok: boolean
  address: string
  output?: string
}

/** GET /api/devices/{serial}/hierarchy 的响应：{node, raw?}（raw=true 时附带原始 XML） */
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
}

// ---------------------------------------------------------------- 查询

export function useDevices(options: { refetchInterval?: number | false; enabled?: boolean } = {}) {
  return useQuery<Device[]>({
    queryKey: deviceKeys.list(),
    queryFn: async ({ signal }) =>
      itemsOf(await get<ItemsLike<Device>>('/devices', { signal })),
    enabled: options.enabled ?? true,
    refetchInterval: options.refetchInterval ?? 5000,
    staleTime: 2000,
  })
}

export function useDeviceInfo(serial: string | null | undefined) {
  return useQuery<Device>({
    queryKey: deviceKeys.info(serial ?? ''),
    queryFn: ({ signal }) => get<Device>(`${base(serial as string)}/info`, { signal }),
    enabled: Boolean(serial),
    refetchInterval: 10000,
  })
}

/** 层级树：后端返回 {node, raw?}；兼容根节点或 {root|tree} 包装。 */
export function useDeviceHierarchy(
  serial: string | null | undefined,
  options: { raw?: boolean; enabled?: boolean } = {},
) {
  const raw = options.raw ?? false
  return useQuery<HierarchyResponse>({
    queryKey: deviceKeys.hierarchy(serial ?? '', raw),
    queryFn: async ({ signal }) => {
      const data = await get<unknown>(`${base(serial as string)}/hierarchy`, { signal, query: { raw } })
      return normalizeHierarchyResponse(data)
    },
    enabled: Boolean(serial) && (options.enabled ?? true),
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
  serial: string | null | undefined,
  lines = 200,
  options: { enabled?: boolean; refetchInterval?: number | false } = {},
) {
  return useQuery<string[]>({
    queryKey: deviceKeys.logcat(serial ?? '', lines),
    queryFn: async ({ signal }) => {
      const text = await apiText(`${base(serial as string)}/logcat`, { signal, query: { lines } })
      return parseLogcat(text)
    },
    enabled: Boolean(serial) && (options.enabled ?? true),
    refetchInterval: options.refetchInterval ?? false,
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
  serial: string | null | undefined,
  params: { keyword?: string; type?: string; enabled?: boolean } = {},
) {
  const keyword = params.keyword ?? ''
  const type = params.type ?? ''
  return useQuery<AppInfo[]>({
    queryKey: deviceKeys.apps(serial ?? '', keyword, type),
    queryFn: async ({ signal }) =>
      itemsOf(
        await get<ItemsLike<AppInfo>>(`${base(serial as string)}/apps`, {
          signal,
          query: { keyword, type },
        }),
      ),
    enabled: Boolean(serial) && (params.enabled ?? true),
    staleTime: 15000,
    retry: false,
  })
}

/** 截图是二进制 PNG，返回 Blob 由调用方转 objectURL。 */
export function useDeviceScreenshot(
  serial: string | null | undefined,
  options: { enabled?: boolean; nonce?: number } = {},
) {
  return useQuery<Blob>({
    queryKey: deviceKeys.screenshot(serial ?? '', options.nonce ?? 0),
    queryFn: ({ signal }) => apiBlob(`${base(serial as string)}/screenshot`, { signal }),
    enabled: Boolean(serial) && (options.enabled ?? true),
    staleTime: 0,
    retry: false,
  })
}

export function useMirrorStatus(serial: string | null | undefined, enabled = true) {
  return useQuery<MirrorStatusInfo>({
    queryKey: deviceKeys.mirror(serial ?? ''),
    queryFn: ({ signal }) => get<MirrorStatusInfo>(`${base(serial as string)}/mirror/status`, { signal }),
    enabled: Boolean(serial) && enabled,
    refetchInterval: 5000,
    retry: false,
  })
}

// ---------------------------------------------------------------- 设备管理

export function useConnectDevice() {
  const queryClient = useQueryClient()
  return useMutation<ConnectResult, Error, ConnectDevicePayload>({
    mutationFn: (payload) => post<ConnectResult>('/devices/connect', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: deviceKeys.all })
    },
  })
}

export function useDisconnectDevice() {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, string>({
    mutationFn: (serial) => post<unknown>(`${base(serial)}/disconnect`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: deviceKeys.all })
    },
  })
}

// ---------------------------------------------------------------- 手动控制

export function useDeviceTap(serial: string) {
  return useMutation<unknown, Error, PointPayload>({
    mutationFn: (payload) => post<unknown>(`${base(serial)}/tap`, payload),
  })
}

export function useDeviceLongPress(serial: string) {
  return useMutation<unknown, Error, LongPressPayload>({
    mutationFn: (payload) => post<unknown>(`${base(serial)}/long_press`, payload),
  })
}

export function useDeviceSwipe(serial: string) {
  return useMutation<unknown, Error, SwipePayload>({
    mutationFn: (payload) => post<unknown>(`${base(serial)}/swipe`, payload),
  })
}

export function useDeviceKey(serial: string) {
  return useMutation<unknown, Error, KeyPayload>({
    mutationFn: (payload) => post<unknown>(`${base(serial)}/key`, payload),
  })
}

export function useDeviceText(serial: string) {
  return useMutation<unknown, Error, TextPayload>({
    mutationFn: (payload) => post<unknown>(`${base(serial)}/text`, payload),
  })
}

export function useDeviceAppAction(serial: string) {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, AppActionPayload>({
    mutationFn: (payload) => post<unknown>(`${base(serial)}/app`, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: deviceKeys.list() })
    },
  })
}

export function useDeviceShell(serial: string) {
  return useMutation<unknown, Error, ShellPayload>({
    mutationFn: (payload) => post<unknown>(`${base(serial)}/shell`, payload),
  })
}

// ---------------------------------------------------------------- 录屏 / 镜像

export function useScreenRecordStart(serial: string) {
  return useMutation<ScreenRecordResult, Error, ScreenRecordStartPayload>({
    mutationFn: (payload) => post<ScreenRecordResult>(`${base(serial)}/screenrecord/start`, payload),
  })
}

export function useScreenRecordStop(serial: string) {
  const queryClient = useQueryClient()
  return useMutation<ScreenRecordResult, Error, void>({
    mutationFn: () => post<ScreenRecordResult>(`${base(serial)}/screenrecord/stop`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: deviceKeys.list() })
    },
  })
}

export function useMirrorStart(serial: string) {
  const queryClient = useQueryClient()
  return useMutation<MirrorStatusInfo, Error, void>({
    mutationFn: () => post<MirrorStatusInfo>(`${base(serial)}/mirror/start`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: deviceKeys.mirror(serial) })
      void queryClient.invalidateQueries({ queryKey: deviceKeys.list() })
    },
  })
}

export function useMirrorStop(serial: string) {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, void>({
    mutationFn: () => post<unknown>(`${base(serial)}/mirror/stop`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: deviceKeys.mirror(serial) })
      void queryClient.invalidateQueries({ queryKey: deviceKeys.list() })
    },
  })
}

// 供页面把 ItemsResponse 风格直接交给表格时复用
export type DeviceItemsResponse = ItemsResponse<Device>
