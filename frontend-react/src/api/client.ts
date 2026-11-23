// 统一 API 客户端：baseURL /api、错误信封 -> ApiError、查询串/列表/下载/二进制工具。
// 错误提示由 main.tsx 的 QueryCache/MutationCache 统一走 notifyApiError（antd message）。
import type { ApiErrorEnvelope, ItemsResponse } from '@/types/api'

export const API_BASE = '/api'

// ---------------------------------------------------------------- 鉴权 token（由 App 从 settings.effective["security.token"] 注入）

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

// ---------------------------------------------------------------- 全局错误提示（由 main.tsx 注入 antd message）

let errorNotifier: ((error: ApiError) => void) | null = null
let lastNotified: { key: string; at: number } | null = null

export function setApiErrorNotifier(notifier: ((error: ApiError) => void) | null): void {
  errorNotifier = notifier
}

/** 供 QueryCache/MutationCache 调用；3s 内相同错误去重，避免多查询同时失败刷屏。 */
export function notifyApiError(error: unknown): void {
  const apiError = toApiError(error)
  if (apiError.name === 'AbortError' || (error as { name?: string } | null)?.name === 'AbortError') return
  const key = `${apiError.code}:${apiError.message}`
  const now = Date.now()
  if (lastNotified && lastNotified.key === key && now - lastNotified.at < 3000) return
  lastNotified = { key, at: now }
  errorNotifier?.(apiError)
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

/** 跳过 undefined/null/''（保留 0 与 false）。 */
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
  if (/^https?:\/\//i.test(normalized) || normalized.startsWith('/ws/') || normalized.startsWith('/artifacts/')) {
    url = normalized
  } else if (normalized === '/api' || normalized.startsWith('/api/')) {
    url = normalized
  } else {
    url = `${API_BASE}${normalized.startsWith('/') ? normalized : `/${normalized}`}`
  }
  return `${url}${buildQuery(query)}`
}

// ---------------------------------------------------------------- 请求

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH'
  body?: unknown
  query?: QueryParams
  signal?: AbortSignal
  headers?: Record<string, string>
}

export type ExtraOptions = Omit<RequestOptions, 'method' | 'body'>

async function parseErrorResponse(response: Response): Promise<ApiError> {
  let envelope: ApiErrorEnvelope | null = null
  try {
    envelope = (await response.json()) as ApiErrorEnvelope
  } catch {
    envelope = null
  }
  if (envelope?.error?.code) {
    return new ApiError(
      envelope.error.code,
      envelope.error.message || '',
      response.status,
      envelope.error.details ?? {},
    )
  }
  const code = STATUS_TO_CODE[response.status] ?? (response.status >= 500 ? 'INTERNAL' : 'INVALID_ARGUMENT')
  return new ApiError(code, response.statusText || `HTTP ${response.status}`, response.status, {})
}

async function requestResponse(path: string, options: RequestOptions = {}): Promise<Response> {
  const { method = 'GET', body, query, signal, headers } = options
  const url = resolveApiUrl(path, query)
  const init: RequestInit = {
    method,
    signal,
    headers: {
      Accept: 'application/json',
      ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
      ...headers,
    },
  }
  if (body !== undefined && body !== null) {
    if (body instanceof FormData) {
      init.body = body
    } else {
      init.headers = { ...init.headers, 'Content-Type': 'application/json' }
      init.body = JSON.stringify(body)
    }
  }
  let response: Response
  try {
    response = await fetch(url, init)
  } catch (error) {
    if ((error as { name?: string } | null)?.name === 'AbortError') throw error
    throw new ApiError('NETWORK', '无法连接后端服务', 0, { url })
  }
  if (!response.ok) throw await parseErrorResponse(response)
  return response
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await requestResponse(path, options)
  if (response.status === 204 || response.status === 205) return undefined as T
  const contentType = response.headers.get('Content-Type') ?? ''
  if (contentType.includes('application/json')) {
    try {
      return (await response.json()) as T
    } catch {
      return undefined as T
    }
  }
  const text = await response.text()
  return text as unknown as T
}

export async function apiText(path: string, options: RequestOptions = {}): Promise<string> {
  const response = await requestResponse(path, options)
  return response.text()
}

export async function apiBlob(path: string, options: RequestOptions = {}): Promise<Blob> {
  const response = await requestResponse(path, options)
  return response.blob()
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

/** 契约列表返回 {items,total}；兼容后端直接返回数组的情况。 */
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
