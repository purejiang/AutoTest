// 展示层格式化工具（日期/耗时/字节/状态映射/事件摘要/产物 URL）
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import 'dayjs/locale/zh-cn'

import type { Artifact, RunStatus } from '@/types/api'

dayjs.extend(relativeTime)
dayjs.locale('zh-cn')

// ---------------------------------------------------------------- 时间
export function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  const d = dayjs(value)
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : String(value)
}

export function formatTime(value?: string | null): string {
  if (!value) return '—'
  const d = dayjs(value)
  return d.isValid() ? d.format('HH:mm:ss') : String(value)
}

export function formatRelative(value?: string | null): string {
  if (!value) return '—'
  const d = dayjs(value)
  return d.isValid() ? d.fromNow() : String(value)
}

// ---------------------------------------------------------------- 数值
export function formatDuration(ms?: number | null): string {
  if (ms === null || ms === undefined || Number.isNaN(Number(ms))) return '—'
  const total = Math.max(0, Math.round(Number(ms)))
  if (total < 1000) return `${total} ms`
  const seconds = total / 1000
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 2 : 1)} s`
  const mins = Math.floor(seconds / 60)
  const rest = Math.floor(seconds % 60)
  if (mins < 60) return `${mins}m ${String(rest).padStart(2, '0')}s`
  const hours = Math.floor(mins / 60)
  return `${hours}h ${String(mins % 60).padStart(2, '0')}m`
}

export function formatBytes(bytes?: number | null): string {
  if (bytes === null || bytes === undefined || Number.isNaN(Number(bytes))) return '—'
  const value = Number(bytes)
  if (value < 1024) return `${value} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let size = value / 1024
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toFixed(size >= 100 ? 0 : size >= 10 ? 1 : 2)} ${units[unit]}`
}

/** 运行耗时：优先 summary.duration_ms，其次 started_at→ended_at 差值。 */
export function runDurationMs(run: {
  started_at?: string | null
  ended_at?: string | null
  summary?: { duration_ms?: number } | null
}): number | null {
  const fromSummary = run.summary?.duration_ms
  if (typeof fromSummary === 'number') return fromSummary
  if (run.started_at && run.ended_at) {
    const start = dayjs(run.started_at)
    const end = dayjs(run.ended_at)
    if (start.isValid() && end.isValid()) return Math.max(0, end.diff(start))
  }
  return null
}

export function elapsedSince(value?: string | null): number | null {
  if (!value) return null
  const start = dayjs(value)
  if (!start.isValid()) return null
  return Math.max(0, dayjs().diff(start))
}

// ---------------------------------------------------------------- JSON
export function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export type ParseJsonResult<T = unknown> = { ok: true; value: T } | { ok: false; error: string }

export function safeParseJson<T = unknown>(text: string): ParseJsonResult<T> {
  try {
    return { ok: true, value: JSON.parse(text) as T }
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) }
  }
}

export function shortText(value: unknown, max = 120): string {
  if (value === null || value === undefined) return ''
  const text = typeof value === 'string' ? value : prettyJson(value)
  return text.length > max ? `${text.slice(0, max)}…` : text
}

/** antd InputNumber 的 onChange 值统一转成 number | null。 */
export function numberOrNull(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null
  const num = Number(value)
  return Number.isNaN(num) ? null : num
}

// ---------------------------------------------------------------- 状态映射
export interface StatusMeta {
  label: string
  color: string
}

export const RUN_STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: 'queued', label: '排队中' },
  { value: 'running', label: '运行中' },
  { value: 'paused', label: '已暂停' },
  { value: 'stopping', label: '停止中' },
  { value: 'succeeded', label: '成功' },
  { value: 'failed', label: '失败' },
  { value: 'aborted', label: '中止' },
  { value: 'stopped', label: '已停止' },
  { value: 'interrupted', label: '被中断' },
]

const RUN_STATUS_META: Record<string, StatusMeta> = {
  queued: { label: '排队中', color: 'default' },
  running: { label: '运行中', color: 'processing' },
  paused: { label: '已暂停', color: 'warning' },
  stopping: { label: '停止中', color: 'warning' },
  succeeded: { label: '成功', color: 'success' },
  failed: { label: '失败', color: 'error' },
  aborted: { label: '中止', color: 'error' },
  stopped: { label: '已停止', color: 'default' },
  interrupted: { label: '被中断', color: 'warning' },
}

export function runStatusMeta(status?: string | null): StatusMeta {
  if (!status) return { label: '未知', color: 'default' }
  return RUN_STATUS_META[status] ?? { label: status, color: 'default' }
}

const STEP_STATUS_META: Record<string, StatusMeta> = {
  passed: { label: '通过', color: 'success' },
  failed: { label: '失败', color: 'error' },
  skipped: { label: '跳过', color: 'default' },
  error: { label: '异常', color: 'volcano' },
}

export function stepStatusMeta(status?: string | null): StatusMeta {
  if (!status) return { label: '未知', color: 'default' }
  return STEP_STATUS_META[status] ?? { label: status, color: 'default' }
}

const RECORDING_STATUS_META: Record<string, StatusMeta> = {
  recording: { label: '录制中', color: 'processing' },
  paused: { label: '已暂停', color: 'warning' },
  stopped: { label: '已停止', color: 'default' },
}

export function recordingStatusMeta(status?: string | null): StatusMeta {
  if (!status) return { label: '未知', color: 'default' }
  return RECORDING_STATUS_META[status] ?? { label: status, color: 'default' }
}

export function confidenceMeta(confidence?: string | null): StatusMeta {
  switch (confidence) {
    case 'high':
      return { label: '高置信', color: 'success' }
    case 'medium':
      return { label: '中置信', color: 'warning' }
    case 'low':
      return { label: '低置信', color: 'error' }
    default:
      return { label: confidence || '未知', color: 'default' }
  }
}

export function artifactKindMeta(kind?: string | null): StatusMeta {
  const map: Record<string, StatusMeta> = {
    screenshot: { label: '截图', color: 'blue' },
    video: { label: '视频', color: 'purple' },
    screenrecord: { label: '录屏', color: 'magenta' },
    logcat: { label: '日志', color: 'cyan' },
    hierarchy: { label: '层级', color: 'geekblue' },
    report: { label: '报告', color: 'green' },
    network: { label: '网络', color: 'orange' },
  }
  if (!kind) return { label: '未知', color: 'default' }
  return map[kind] ?? { label: kind, color: 'default' }
}

export function sourceMeta(source?: string | null): StatusMeta {
  switch (source) {
    case 'manual':
      return { label: '手动', color: 'blue' }
    case 'recorded':
      return { label: '录制', color: 'green' }
    case 'imported':
      return { label: '导入', color: 'orange' }
    default:
      return { label: source || '未知', color: 'default' }
  }
}

export function settingSourceMeta(source?: string | null): StatusMeta {
  switch (source) {
    case 'db':
      return { label: '数据库覆盖', color: 'green' }
    case 'file':
      return { label: '配置文件', color: 'blue' }
    default:
      return { label: '默认值', color: 'default' }
  }
}

export function isActiveRunStatus(status?: string | null): boolean {
  return status === 'queued' || status === 'running' || status === 'paused' || status === 'stopping'
}

export function isTerminalRunStatus(status?: RunStatus | string | null): boolean {
  return status === 'succeeded' || status === 'failed' || status === 'aborted' || status === 'stopped' || status === 'interrupted'
}

// ---------------------------------------------------------------- 运行事件
const EVENT_TYPE_COLOR: Record<string, string> = {
  run_started: 'green',
  run_state: 'blue',
  step_started: 'processing',
  step_finished: 'success',
  step_skipped: 'default',
  step_retry: 'orange',
  assert_result: 'cyan',
  log: 'default',
  screenshot: 'purple',
  run_paused: 'warning',
  run_resumed: 'warning',
  manual_intervention: 'gold',
  run_finished: 'green',
  error: 'error',
}

export function runEventColor(type: string, payload?: Record<string, unknown> | null): string {
  if (type === 'step_finished' && payload && typeof payload.status === 'string' && payload.status !== 'passed') {
    return payload.status === 'failed' ? 'error' : 'default'
  }
  if (type === 'assert_result' && payload && payload.passed === false) return 'error'
  return EVENT_TYPE_COLOR[type] ?? 'default'
}

function pick(payload: Record<string, unknown> | undefined | null, key: string): unknown {
  return payload ? payload[key] : undefined
}

/** 事件一行的可读摘要（尽量覆盖契约 §5 全部类型）。 */
export function summarizeRunEvent(event: { type: string; payload?: Record<string, unknown> | null }): string {
  const payload = event.payload ?? {}
  const str = (value: unknown): string => (value === null || value === undefined ? '' : typeof value === 'string' ? value : prettyJson(value))
  switch (event.type) {
    case 'run_started':
      return `${str(pick(payload, 'script_name'))} → ${str(pick(payload, 'device_serial'))}（共 ${str(pick(payload, 'total_steps'))} 步）`
    case 'run_state':
      return `状态 ${str(pick(payload, 'status'))}${pick(payload, 'step_index') !== undefined ? ` · 步骤 ${str(pick(payload, 'step_index'))}` : ''}`
    case 'step_started':
      return `#${str(pick(payload, 'index'))} ${str(pick(payload, 'name')) || str(pick(payload, 'step_id'))} · ${str(pick(payload, 'type'))}（第 ${str(pick(payload, 'attempt'))} 次）`
    case 'step_finished': {
      const error = payload.error
      const errorText = error && typeof error === 'object' ? ` · ${str((error as Record<string, unknown>).message)}` : ''
      return `${str(pick(payload, 'step_id'))} → ${str(pick(payload, 'status'))} · ${str(pick(payload, 'duration_ms'))}ms${errorText}`
    }
    case 'step_skipped':
      return `${str(pick(payload, 'step_id'))} 跳过（${str(pick(payload, 'reason'))}）`
    case 'step_retry':
      return `${str(pick(payload, 'step_id'))} 第 ${str(pick(payload, 'attempt'))} 次重试：${str(pick(payload, 'error'))}`
    case 'assert_result':
      return `${str(pick(payload, 'step_id'))} 断言 ${str(pick(payload, 'kind'))} ${pick(payload, 'passed') ? '通过' : '未通过'} · ${str(pick(payload, 'detail'))}`
    case 'log':
      return `[${str(pick(payload, 'level'))}] ${str(pick(payload, 'message'))}`
    case 'screenshot':
      return `截图 ${str(pick(payload, 'name')) || str(pick(payload, 'artifact_id'))}${pick(payload, 'step_id') ? ` · ${str(pick(payload, 'step_id'))}` : ''}`
    case 'run_paused':
      return `暂停（${str(pick(payload, 'reason'))}${pick(payload, 'at_step_id') ? ` · ${str(pick(payload, 'at_step_id'))}` : ''}）`
    case 'run_resumed':
      return `恢复（${str(pick(payload, 'resume_kind'))}）`
    case 'manual_intervention':
      return `人工介入 ${str(pick(payload, 'kind'))}`
    case 'run_finished':
      return `结束：${str(pick(payload, 'status'))} · 通过 ${str(pick(payload, 'passed_steps'))} / 失败 ${str(pick(payload, 'failed_steps'))} / 跳过 ${str(pick(payload, 'skipped_steps'))} · ${str(pick(payload, 'duration_ms'))}ms`
    case 'error':
      return `${str(pick(payload, 'code'))}: ${str(pick(payload, 'message'))}`
    default:
      return shortText(payload, 180)
  }
}

// ---------------------------------------------------------------- 产物 URL
/**
 * 后端 artifact.path 是数据目录内的文件系统路径；静态资源按 /artifacts 挂载（vite 已代理）。
 * 尽力从绝对路径中截出 artifacts 之后的相对路径，无法解析时返回 null（页面降级为纯文本）。
 */
export function artifactUrl(artifact: Pick<Artifact, 'path'> | { path?: string | null }): string | null {
  const raw = artifact.path
  if (!raw) return null
  if (/^https?:\/\//i.test(raw)) return raw
  const normalized = raw.replace(/\\/g, '/')
  if (normalized.startsWith('/artifacts/')) return normalized
  const marker = '/artifacts/'
  const index = normalized.indexOf(marker)
  if (index >= 0) return `/artifacts/${normalized.slice(index + marker.length)}`
  return null
}
