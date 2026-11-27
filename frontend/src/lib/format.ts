// 展示层格式化工具（日期/耗时/字节/状态映射/事件摘要/产物 URL）
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import 'dayjs/locale/zh-cn'

import type { Artifact, RunStatus, ScriptSource, StepConditionOutcome, StepResult } from '@/types/api'

dayjs.extend(relativeTime)
dayjs.locale('zh-cn')

export type Tone = 'gray' | 'blue' | 'green' | 'amber' | 'red' | 'violet' | 'cyan'

// ---------------------------------------------------------------- 时间
export function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  const d = dayjs(value)
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : String(value)
}

export function formatShortDateTime(value?: string | null): string {
  if (!value) return '—'
  const d = dayjs(value)
  return d.isValid() ? d.format('MM-DD HH:mm') : String(value)
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

/**
 * 运行进度：`step_index` 是当前节点下标（0-based），展示为“已处理到第几个节点”。
 * succeeded 直接显示 total/total；queued 显示 0；其余按当前节点计 1 个。
 * 列表、详情、控制台必须共用这一处，避免同一条运行出现 8/9 与 9/9 两种结果。
 */
export function runProgress(run: {
  status?: RunStatus | string | null
  step_index?: number | null
  total_steps?: number | null
  summary?: { total_steps?: number } | null
}): { current: number; total: number; percent: number } {
  const rawTotal = Number(run.total_steps ?? run.summary?.total_steps ?? 0)
  const total = Number.isFinite(rawTotal) ? Math.max(0, Math.floor(rawTotal)) : 0
  if (total <= 0) return { current: 0, total: 0, percent: 0 }
  if (run.status === 'queued') return { current: 0, total, percent: 0 }
  if (run.status === 'succeeded') return { current: total, total, percent: 100 }
  const rawIndex = Number(run.step_index ?? 0)
  const index = Number.isFinite(rawIndex) ? Math.max(0, Math.floor(rawIndex)) : 0
  const current = Math.min(total, index + 1)
  return { current, total, percent: Math.min(100, Math.round((current / total) * 100)) }
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

export function numberOrNull(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null
  const num = Number(value)
  return Number.isNaN(num) ? null : num
}

// ---------------------------------------------------------------- 状态映射
export interface StatusMeta {
  label: string
  tone: Tone
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
  queued: { label: '排队中', tone: 'gray' },
  running: { label: '运行中', tone: 'blue' },
  paused: { label: '已暂停', tone: 'amber' },
  stopping: { label: '停止中', tone: 'amber' },
  succeeded: { label: '成功', tone: 'green' },
  failed: { label: '失败', tone: 'red' },
  aborted: { label: '中止', tone: 'red' },
  stopped: { label: '已停止', tone: 'gray' },
  interrupted: { label: '被中断', tone: 'amber' },
}

export function runStatusMeta(status?: string | null): StatusMeta {
  if (!status) return { label: '未知', tone: 'gray' }
  return RUN_STATUS_META[status] ?? { label: status, tone: 'gray' }
}

const STEP_STATUS_META: Record<string, StatusMeta> = {
  passed: { label: '通过', tone: 'green' },
  failed: { label: '失败', tone: 'red' },
  skipped: { label: '跳过', tone: 'gray' },
  error: { label: '异常', tone: 'red' },
  running: { label: '执行中', tone: 'blue' },
  paused: { label: '已暂停', tone: 'amber' },
  pending: { label: '未执行', tone: 'gray' },
}

export function stepStatusMeta(status?: string | null): StatusMeta {
  if (!status) return { label: '未知', tone: 'gray' }
  return STEP_STATUS_META[status] ?? { label: status, tone: 'gray' }
}

const RECORDING_STATUS_META: Record<string, StatusMeta> = {
  recording: { label: '录制中', tone: 'red' },
  paused: { label: '已暂停', tone: 'amber' },
  stopped: { label: '已停止', tone: 'gray' },
}

export function recordingStatusMeta(status?: string | null): StatusMeta {
  if (!status) return { label: '未知', tone: 'gray' }
  return RECORDING_STATUS_META[status] ?? { label: status, tone: 'gray' }
}

export function recordingModeMeta(mode?: string | null): StatusMeta {
  if (mode === 'physical') return { label: '真机录制', tone: 'violet' }
  if (mode === 'web') return { label: '镜像录制', tone: 'blue' }
  return { label: '未知', tone: 'gray' }
}

export function confidenceMeta(confidence?: string | null): StatusMeta {
  switch (confidence) {
    case 'high':
      return { label: '高', tone: 'green' }
    case 'medium':
      return { label: '中', tone: 'amber' }
    case 'low':
      return { label: '低', tone: 'red' }
    default:
      return { label: confidence || '未知', tone: 'gray' }
  }
}

export function artifactKindMeta(kind?: string | null): StatusMeta {
  const map: Record<string, StatusMeta> = {
    screenshot: { label: '截图', tone: 'blue' },
    video: { label: '视频', tone: 'violet' },
    screenrecord: { label: '录屏', tone: 'violet' },
    logcat: { label: '日志', tone: 'cyan' },
    hierarchy: { label: '层级', tone: 'blue' },
    report: { label: '报告', tone: 'green' },
    network: { label: '网络', tone: 'amber' },
  }
  if (!kind) return { label: '未知', tone: 'gray' }
  return map[kind] ?? { label: kind, tone: 'gray' }
}

/** v0.2 脚本来源：手动 / 镜像录制 / 真机录制 / 导入。 */
export function sourceMeta(source?: ScriptSource | string | null): StatusMeta {
  switch (source) {
    case 'manual':
      return { label: '手动', tone: 'blue' }
    case 'mirror':
      return { label: '镜像录制', tone: 'green' }
    case 'physical':
      return { label: '真机录制', tone: 'violet' }
    case 'imported':
      return { label: '导入', tone: 'amber' }
    default:
      return { label: source || '未知', tone: 'gray' }
  }
}

export function settingSourceMeta(source?: string | null): StatusMeta {
  switch (source) {
    case 'file':
      return { label: '配置文件', tone: 'blue' }
    case 'env':
      return { label: '环境变量', tone: 'amber' }
    default:
      return { label: '默认值', tone: 'gray' }
  }
}

export function isActiveRunStatus(status?: string | null): boolean {
  return status === 'queued' || status === 'running' || status === 'paused' || status === 'stopping'
}

export function isTerminalRunStatus(status?: RunStatus | string | null): boolean {
  return (
    status === 'succeeded' ||
    status === 'failed' ||
    status === 'aborted' ||
    status === 'stopped' ||
    status === 'interrupted'
  )
}

// ---------------------------------------------------------------- 运行事件
const EVENT_TONE: Record<string, Tone> = {
  run_started: 'green',
  run_state: 'blue',
  step_started: 'blue',
  step_finished: 'green',
  step_skipped: 'gray',
  step_retry: 'amber',
  assert_result: 'cyan',
  log: 'gray',
  screenshot: 'violet',
  run_paused: 'amber',
  run_resumed: 'amber',
  manual_intervention: 'amber',
  run_finished: 'green',
  error: 'red',
  when_started: 'cyan',
  when_result: 'green',
}

export function runEventTone(type: string, payload?: Record<string, unknown> | null): Tone {
  if (type === 'step_finished' && payload && typeof payload.status === 'string' && payload.status !== 'passed') {
    return payload.status === 'failed' ? 'red' : 'gray'
  }
  if (type === 'assert_result' && payload && payload.passed === false) return 'red'
  if (type === 'when_result' && payload && payload.met === false) return 'amber'
  return EVENT_TONE[type] ?? 'gray'
}

const EVENT_LABEL: Record<string, string> = {
  when_started: '条件检查',
  when_result: '条件结果',
}

export function runEventLabel(type: string): string {
  return EVENT_LABEL[type] ?? type
}

function pick(payload: Record<string, unknown> | undefined | null, key: string): unknown {
  return payload ? payload[key] : undefined
}

function numberText(value: unknown): string {
  return typeof value === 'number' ? String(value) : value === undefined || value === null ? '' : String(value)
}

/** 事件一行的可读摘要（覆盖契约 §5 与 v0.2 when_started/when_result）。 */
export function summarizeRunEvent(event: { type: string; payload?: Record<string, unknown> | null }): string {
  const payload = event.payload ?? {}
  const str = (value: unknown): string =>
    value === null || value === undefined ? '' : typeof value === 'string' ? value : prettyJson(value)
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
    case 'step_skipped': {
      const reason = str(pick(payload, 'reason'))
      const reasonText =
        reason === 'when_not_met'
          ? '条件不满足'
          : reason === 'disabled'
            ? '节点已禁用'
            : reason === 'after_fail'
              ? '失败后跳过'
              : reason
      return `${str(pick(payload, 'step_id'))} 跳过（${reasonText}）`
    }
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
    case 'when_started': {
      const timeout = pick(payload, 'timeout_ms')
      const mode = pick(payload, 'mode')
      const summary = pick(payload, 'condition_summary')
      const modeText = mode === 'optional' ? '（optional）' : mode === 'require' ? '（require）' : ''
      const detail = summary !== undefined && summary !== null ? ` · ${str(summary)}` : ''
      return `${str(pick(payload, 'step_id')) || '节点'} 开始检查执行条件${modeText}${timeout !== undefined ? ` · 最长 ${numberText(timeout)}ms` : ''}${detail}`
    }
    case 'when_result': {
      const met = pick(payload, 'met')
      const waited = pick(payload, 'waited_ms')
      const summary = pick(payload, 'condition_summary')
      const mode = pick(payload, 'mode')
      const result = met === true ? '满足' : met === false ? '不满足' : '已求值'
      const modeText = mode === 'optional' ? '（optional 将跳过）' : mode === 'require' ? '（require 将失败）' : ''
      return `${str(pick(payload, 'step_id')) || '节点'} 条件${result}${modeText}${waited !== undefined ? ` · 等待 ${numberText(waited)}ms` : ''}${summary ? ` · ${str(summary)}` : ''}`
    }
    default:
      return shortText(payload, 180)
  }
}

function summarizeConditionPayload(value: unknown): string {
  if (typeof value === 'string') return value
  if (!value || typeof value !== 'object') return shortText(value, 80)
  const record = value as Record<string, unknown>
  if (typeof record.summary === 'string') return record.summary
  if (typeof record.type === 'string') {
    if (record.type === 'target') return `目标 ${String(record.by ?? '')} ${String(record.value ?? '')}`.trim()
    if (record.type === 'app') return `应用 ${String(record.package ?? '')}`.trim()
    if (record.type === 'time') return `时间 ${String(record.start ?? '')}-${String(record.end ?? '')}`
    if (record.type === 'group') return `分组 ${String(record.op ?? '')}`
    if (record.type === 'not') return '取反'
  }
  return shortText(value, 80)
}

// ---------------------------------------------------------------- 条件结果展示
export interface ConditionOutcomeView {
  tone: Tone
  label: string
  detail: string
}

/**
 * wait 节点的实际等待时长。
 *
 * 后端 execute_wait 的两种输出都会带 waited_ms：
 *   sleep -> {mode: "sleep", waited_ms}
 *   until -> {mode: "until", state, waited_ms}
 * 该等待发生在节点动作内部，不会出现在 output.when（执行条件）里，因此单独读取。
 */
export function stepWaitMs(step: StepResult): number | null {
  if (step.step_type !== 'wait') return null
  const raw = step.output?.waited_ms
  if (typeof raw !== 'number' || !Number.isFinite(raw) || raw < 0) return null
  return raw
}

function asOutcome(value: unknown): StepConditionOutcome | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as StepConditionOutcome
}

/** 从 step.when 或 output.when 读取条件求值结果（v0.2 运行详情展示用）。 */
export interface SubScriptContext {
  parentStepId: string
  depth: number
  scriptId: string
  scriptName: string
}

/** 子脚本内部节点：output.sub_script 由后端写入，用于缩进展示。 */
export function stepSubScriptContext(step: StepResult): SubScriptContext | null {
  const raw = step.output?.sub_script
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const meta = raw as Record<string, unknown>
  const parentStepId = typeof meta.parent_step_id === 'string' ? meta.parent_step_id : ''
  if (!parentStepId) return null
  return {
    parentStepId,
    depth: Number.isFinite(Number(meta.depth)) ? Math.max(1, Number(meta.depth)) : 1,
    scriptId: typeof meta.script_id === 'string' ? meta.script_id : '',
    scriptName: typeof meta.script_name === 'string' ? meta.script_name : '',
  }
}

export function stepConditionOutcome(step: StepResult): ConditionOutcomeView | null {
  const outcome =
    asOutcome(step.when) ??
    asOutcome(step.output?.when) ??
    asOutcome(step.output?.condition_result) ??
    null
  if (!outcome) return null
  const waited = typeof outcome.waited_ms === 'number' ? ` · 等待 ${formatDuration(outcome.waited_ms)}` : ''
  const timeout =
    typeof outcome.timeout_ms === 'number' ? ` · 超时 ${formatDuration(outcome.timeout_ms)}` : ''
  const detail = outcome.condition_summary
    ? ` · ${outcome.condition_summary}`
    : outcome.detail
      ? ` · ${outcome.detail}`
      : ''
  if (outcome.skipped || (outcome.met === false && step.status === 'skipped')) {
    return { tone: 'gray', label: '条件跳过', detail: `${waited || ' · 条件未满足'}${detail}` }
  }
  if (outcome.failed || (outcome.met === false && step.status === 'failed')) {
    return { tone: 'red', label: '条件失败', detail: `${waited || ' · 条件未满足'}${detail}` }
  }
  if (outcome.met === true) {
    return { tone: 'green', label: '条件满足', detail: `${waited}${timeout}${detail}` }
  }
  if (outcome.met === false) {
    return { tone: 'amber', label: '条件未满足', detail: `${waited}${timeout}${detail}` }
  }
  return { tone: 'gray', label: '条件已求值', detail: `${waited}${timeout}${detail}` }
}

// ---------------------------------------------------------------- 产物 URL
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

export function artifactHref(artifact: Pick<Artifact, 'path'> & { url?: string | null }): string | null {
  const direct = artifact.url
  if (typeof direct === 'string' && direct.trim()) return direct
  return artifactUrl(artifact)
}
