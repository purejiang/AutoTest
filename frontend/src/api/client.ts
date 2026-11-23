// 统一 API 客户端：Axios 实例（baseURL /api）、错误信封 -> ApiError、查询串/列表/下载工具。
// 错误提示由 main.ts 注入的 notifier 统一走 Element Plus ElMessage。
import axios, { type AxiosRequestConfig, type AxiosResponse } from 'axios'

import type { ApiErrorEnvelope, ItemsResponse } from '@/types/api'

export const API_BASE = '/api'

// ---------------------------------------------------------------- 鉴权 token（settings.effective["security.token"] 注入）

let authToken = ''

export function setAuthToken(token: string | null | undefined): void {
  authToken = (token ?? '').trim()
}

export function getAuthToken(): string {
  return authToken
}

// ---------------------------------------------------------------- 错误

/** 后端契约 §1 的代码 -> HTTP 状态；无信封响应时按状态码兜底。 */
const STATUS_TO_CODE: Record<number, string> = {
  400: 'INVALID_ARGUMENT',
  401: 'UNAUTHORIZED',
  404: 'NOT_FOUND',
  409: 'CONFLICT',
  422: 'VALIDATION_ERROR',
  500: 'INTERNAL',
  502: 'SCRCPY_FAILED',
  503: 'DEVICE_UNAVAILABLE',
}

const ERROR_HINTS: Record<string, string> = {
  INVALID_ARGUMENT: '参数非法',
  VALIDATION_ERROR: '校验失败',
  UNAUTHORIZED: '未授权：请检查「设置 · 访问令牌」',
  NOT_FOUND: '资源不存在或已被删除',
  CONFLICT: '操作冲突：资源被占用或状态不允许，请稍后重试',
  DEVICE_UNAVAILABLE: '设备不可用：请确认 adb 已连接且设备在线',
  DEVICE_BUSY: '设备正被其他运行/录制占用',
  SCRCPY_FAILED: '镜像启动失败',
  UNSUPPORTED: '当前环境不支持该操作',
  INTERNAL: '服务器内部错误',
  NETWORK: '无法连接后端服务（请确认后端已在 127.0.0.1:8787 启动）',
}

export class ApiError extends Error {
  readonly code: string
  readonly details: Record<string, unknown>
  readonly status: number

  constructor(code: string, message: string, status = 0, details: Record<string, unknown> = {}) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.details = details
  }

  get isNetworkError(): boolean {
    return this.status === 0
  }
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  if (error instanceof Error) return new ApiError('INTERNAL', error.message, 0)
  return new ApiError('INTERNAL', String(error), 0)
}

/** 给用户看的可读错误（含 code 便于排查）。 */
export function describeApiError(error: unknown): string {
  const apiError = toApiError(error)
  const hint = ERROR_HINTS[apiError.code]
  let text = hint ?? (apiError.message || '请求失败')
  if (apiError.code === 'VALIDATION_ERROR') {
    const errors = apiError.details?.errors
    if (Array.isArray(errors) && errors.length > 0) {
      const first = errors[0] as Record<string, unknown>
      const firstMessage = typeof first?.message === 'string' ? first.message : ''
      if (firstMessage) {
        text = `${hint ?? '校验失败'}：${firstMessage}${errors.length > 1 ? `（共 ${errors.length} 项）` : ''}`
      }
    }
  }
  if (apiError.code !== 'NETWORK' && apiError.code !== 'INTERNAL' && !hint) {
    text = `${text}（${apiError.code}）`
  }
  return text
}

// ---------------------------------------------------------------- 全局错误提示

let errorNotifier: ((error: ApiError) => void) | null = null
let lastNotified: { key: string; at: number } | null = null

export function setApiErrorNotifier(notifier: ((error: ApiError) => void) | null): void {
  errorNotifier = notifier
}

/** 3s 内相同错误去重，避免多个查询同时失败刷屏。 */
export function notifyApiError(error: unknown): void {
  const apiError = toApiError(error)
  if (isAbortError(error)) return
  const key = `${apiError.code}:${apiError.message}`
  const now = Date.now()
  if (lastNotified && lastNotified.key === key && now - lastNotified.at < 3000) return
  lastNotified = { key, at: now }
  errorNotifier?.(apiError)
}

export function isAbortError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false
  const record = error as { name?: string; code?: string }
  return (
    record.name === 'AbortError' ||
    record.name === 'CanceledError' ||
    record.code === 'ERR_CANCELED' ||
    axios.isCancel(error)
  )
}

/** 从 VALIDATION_ERROR details.errors 中提取逐项错误。 */
export function validationErrorsOf(error: unknown): { path: string; code: string; message: string }[] {
  const apiError = toApiError(error)
  const raw = apiError.details?.errors
  if (!Array.isArray(raw)) return []
  return raw.map((item) => {
    const record = (item ?? {}) as Record<string, unknown>
    return {
      path: typeof record.path === 'string' ? record.path : '',
      code: typeof record.code === 'string' ? record.code : '',
      message: typeof record.message === 'string' ? record.message : String(record.message ?? ''),
    }
  })
}

// ---------------------------------------------------------------- URL / 查询串

export type QueryValue = string | number | boolean | null | undefined
export type QueryParams = Record<string, QueryValue | QueryValue[]>

export function buildQuery(query?: QueryParams): string {
  if (!query) return ''
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) {
      for (const item of value) {
        if (item === undefined || item === null || item === '') continue
        params.append(key, String(item))
      }
    } else {
      params.append(key, String(value))
    }
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

export function resolveApiUrl(path: string, query?: QueryParams): string {
  const normalized = path.trim()
  let url: string
  if (
    /^https?:\/\//i.test(normalized) ||
    normalized.startsWith('/ws/') ||
    normalized.startsWith('/artifacts/')
  ) {
    url = normalized
  } else if (normalized === '/api' || normalized.startsWith('/api/')) {
    url = normalized
  } else {
    url = `${API_BASE}${normalized.startsWith('/') ? normalized : `/${normalized}`}`
  }
  return `${url}${buildQuery(query)}`
}

// ---------------------------------------------------------------- Axios 实例

const http = axios.create({
  baseURL: API_BASE,
  timeout: 60_000,
  headers: { Accept: 'application/json' },
})

http.interceptors.request.use((config) => {
  if (authToken) config.headers.Authorization = `Bearer ${authToken}`
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    if (isAbortError(error)) return Promise.reject(error)
    if (axios.isAxiosError(error)) {
      const response = error.response
      if (!response) {
        return Promise.reject(new ApiError('NETWORK', '无法连接后端服务', 0))
      }
      const envelope = response.data as ApiErrorEnvelope | undefined
      if (envelope?.error?.code) {
        return Promise.reject(
          new ApiError(
            envelope.error.code,
            envelope.error.message || '',
            response.status,
            envelope.error.details ?? {},
          ),
        )
      }
      const code =
        STATUS_TO_CODE[response.status] ?? (response.status >= 500 ? 'INTERNAL' : 'INVALID_ARGUMENT')
      return Promise.reject(
        new ApiError(code, response.statusText || `HTTP ${response.status}`, response.status, {}),
      )
    }
    return Promise.reject(error)
  },
)

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH'
  body?: unknown
  query?: QueryParams
  signal?: AbortSignal
  headers?: Record<string, string>
  responseType?: 'json' | 'text' | 'blob'
}

export type ExtraOptions = Omit<RequestOptions, 'method' | 'body'>

function requestConfig(path: string, options: RequestOptions): AxiosRequestConfig {
  const config: AxiosRequestConfig = {
    url: path,
    method: options.method ?? 'GET',
    params: options.query,
    signal: options.signal,
    headers: options.headers,
    responseType: options.responseType ?? 'json',
  }
  if (options.body !== undefined && options.body !== null) {
    config.data = options.body
  }
  return config
}

async function requestResponse(path: string, options: RequestOptions): Promise<AxiosResponse> {
  return http.request(requestConfig(path, options))
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await requestResponse(path, options)
  return response.data as T
}

export async function apiText(path: string, options: RequestOptions = {}): Promise<string> {
  const response = await requestResponse(path, { ...options, responseType: 'text', method: 'GET' })
  return typeof response.data === 'string' ? response.data : String(response.data ?? '')
}

export async function apiBlob(path: string, options: RequestOptions = {}): Promise<Blob> {
  const response = await requestResponse(path, { ...options, responseType: 'blob', method: 'GET' })
  return response.data as Blob
}

// ---------------------------------------------------------------- 便捷方法

export function get<T>(path: string, options: ExtraOptions = {}): Promise<T> {
  return apiRequest<T>(path, { ...options, method: 'GET' })
}

export function post<T>(path: string, body?: unknown, options: ExtraOptions = {}): Promise<T> {
  return apiRequest<T>(path, { ...options, method: 'POST', body: body ?? {} })
}

export function put<T>(path: string, body?: unknown, options: ExtraOptions = {}): Promise<T> {
  return apiRequest<T>(path, { ...options, method: 'PUT', body: body ?? {} })
}

export function del<T>(path: string, body?: unknown, options: ExtraOptions = {}): Promise<T> {
  return apiRequest<T>(path, { ...options, method: 'DELETE', body })
}

// ---------------------------------------------------------------- 列表信封

export type ItemsLike<T> = T[] | ItemsResponse<T>

export function itemsOf<T>(raw: ItemsLike<T> | null | undefined): T[] {
  if (!raw) return []
  if (Array.isArray(raw)) return raw
  return Array.isArray(raw.items) ? raw.items : []
}

export function totalOf<T>(raw: ItemsLike<T> | null | undefined): number {
  if (!raw) return 0
  if (Array.isArray(raw)) return raw.length
  return typeof raw.total === 'number' ? raw.total : itemsOf(raw).length
}

// ---------------------------------------------------------------- 下载

export function downloadFile(path: string, filename?: string): void {
  const anchor = document.createElement('a')
  anchor.href = resolveApiUrl(path)
  if (filename) anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}
