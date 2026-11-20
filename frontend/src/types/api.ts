// AutoTest 前端类型（与后端 docs/contracts.md §3/§5/§7 对齐；Lead 维护，只做向后兼容追加）

export type RunStatus =
  | 'queued' | 'running' | 'paused' | 'stopping'
  | 'succeeded' | 'failed' | 'aborted' | 'stopped' | 'interrupted'

export type StepResultStatus = 'passed' | 'failed' | 'skipped' | 'error'

export type RecordingStatus = 'recording' | 'paused' | 'stopped'

export type NodeType =
  | 'click' | 'long_click' | 'swipe' | 'drag' | 'input' | 'key'
  | 'wait' | 'app' | 'shell' | 'assert' | 'sub_script' | 'screenshot'

export type TargetBy =
  | 'text' | 'text_contains' | 'resource_id' | 'content_desc'
  | 'class_name' | 'xpath' | 'point'

export interface Target {
  by: TargetBy
  value?: string
  index?: number
  x?: number
  y?: number
}

export interface When {
  target: Target
  mode: 'require' | 'optional'
  timeout_ms: number
  state: 'visible' | 'gone'
}

export interface Retry {
  count: number
  interval_ms: number
}

export interface OnFail {
  action: 'abort' | 'continue' | 'goto'
  target_id?: string
}

export type RerunSafety = 'safe' | 'destructive' | 'stateful'

// ---- 12 类节点 params
export interface ClickParams { target: Target }
export interface LongClickParams { target: Target; duration_ms?: number }
export interface SwipeParams { from: Target; to: Target; duration_ms?: number }
export interface DragParams { from: Target; to: Target; duration_ms?: number }
export interface InputParams { text: string; target?: Target | null; clear_first?: boolean }
export interface KeyParams { key?: string; keycode?: number }
export interface WaitParams {
  mode: 'sleep' | 'until'
  ms?: number
  target?: Target | null
  state?: 'visible' | 'gone'
  timeout_ms?: number
  interval_ms?: number
}
export interface AppParams { action: 'launch' | 'stop' | 'clear' | 'restart'; package?: string | null; activity?: string | null }
export interface ShellParams { command: string; timeout_ms?: number; check_exit_code?: boolean }
export interface AssertParams { kind: 'exists' | 'not_exists' | 'text_contains' | 'foreground'; target?: Target | null; value?: string }
export interface SubScriptParams { script_id: string; variables?: Record<string, string> }
export interface ScreenshotParams { name?: string | null }

export interface StepNode {
  id: string
  name: string
  type: NodeType
  description: string
  enabled: boolean
  when: When | null
  timeout_ms: number | null
  delay_before_ms: number
  delay_after_ms: number
  retry: Retry | null
  on_fail: OnFail | null
  screenshot_on_fail: boolean
  rerun_safety: RerunSafety
  tags: string[]
  params: Record<string, unknown>
}

export interface ScriptSettings {
  default_timeout_ms: number
  on_fail: 'abort' | 'continue'
  logcat: boolean
  retry: Retry | null
  screenshot_on_fail: boolean
}

export interface ScriptData {
  schema_version: number
  name: string
  notes: string
  variables: Record<string, string>
  settings: ScriptSettings
  defaults: { retry: Retry | null }
  steps: StepNode[]
}

export interface Script {
  id: string
  name: string
  notes: string
  source: 'manual' | 'recorded' | 'imported'
  schema_version: number
  data: ScriptData
  step_count?: number
  created_at: string
  updated_at: string
}

export interface ValidationErrorItem { path: string; code: string; message: string }

export interface ScriptValidation {
  valid: boolean
  errors: ValidationErrorItem[]
  warnings: ValidationErrorItem[]
}

// ---- 设备
export interface DeviceMirrorInfo { active: boolean; width?: number; height?: number }

export interface Device {
  serial: string
  state: string
  model: string
  brand: string
  android_version: string
  sdk: number
  screen_width: number
  screen_height: number
  rotation: number
  mirror: DeviceMirrorInfo
  busy: boolean
  busy_reason?: string
}

export interface HierarchyNode {
  class_name: string
  text: string
  resource_id: string
  content_desc: string
  package: string
  bounds: [number, number, number, number]
  clickable: boolean
  enabled: boolean
  visible: boolean
  children: HierarchyNode[]
}

// ---- 运行
export interface Run {
  id: number
  script_id: string | null
  script_name: string
  device_serial: string
  status: RunStatus
  started_at: string | null
  ended_at: string | null
  current_step_id: string | null
  step_index: number
  total_steps: number
  error: { code?: string; message?: string } | null
  summary: {
    passed_steps?: number
    failed_steps?: number
    skipped_steps?: number
    duration_ms?: number
  } | null
  created_at: string
  debug?: { step_mode?: boolean; breakpoints?: string[] } | null
}

export interface StepResult {
  id: number
  run_id: number
  step_id: string
  step_name: string
  step_type: string
  step_index: number
  status: StepResultStatus
  attempts: number
  started_at: string | null
  ended_at: string | null
  duration_ms: number
  error: { code?: string; message?: string } | null
  output: Record<string, unknown> | null
}

export interface RunDetail extends Run {
  steps: StepResult[]
  artifacts: Artifact[]
}

export interface RunEvent {
  seq: number
  type: string
  payload: Record<string, unknown>
  created_at: string
}

export interface Artifact {
  id: number
  run_id: number | null
  step_result_id: number | null
  kind: string
  name: string
  path: string
  /** 产物直链（后端 /artifacts/<相对路径>），无直链时为 null */
  url?: string | null
  size: number
  created_at: string
}

// ---- WS 信封
export interface WsStateMsg {
  kind: 'state'
  run_id?: number
  status?: RunStatus | RecordingStatus
  last_seq?: number
  event_count?: number
}

export interface WsEventMsg {
  kind: 'event'
  seq: number
  type: string
  payload: Record<string, unknown>
  confidence?: string
  ts: string
}

export interface WsControlMsg {
  kind: 'config' | 'flush' | 'error' | 'stats' | 'pong' | 'hello'
  [key: string]: unknown
}

// ---- 录制
export interface Recording {
  id: string
  name: string
  mode: 'web' | 'physical'
  serial: string
  status: RecordingStatus
  started_at: string | null
  ended_at: string | null
  event_count: number
  script_id: string | null
  meta: Record<string, unknown> | null
  created_at: string
}

export interface RecordingEvent {
  seq: number
  kind: string
  payload: {
    x?: number
    y?: number
    duration_ms?: number
    text?: string
    key?: string
    /** 后端落库字段为 node；保留 target 以兼容旧版本 */
    node?: Target | null
    target?: Target | null
    from?: { x: number; y: number }
    to?: { x: number; y: number }
    confidence_reason?: string
    [key: string]: unknown
  }
  confidence: 'high' | 'medium' | 'low'
  created_at: string
}

// ---- 报告/设置/健康
export interface Report {
  id: string
  run_id: number
  status: string
  path: string
  size: number
  summary: Record<string, unknown> | null
  created_at: string
  url?: string
}

export interface SettingEntry {
  key: string
  type: 'string' | 'int' | 'bool' | 'enum'
  title: string
  desc?: string
  min?: number
  max?: number
  /** type='enum' 时的可选项 */
  options?: Array<{ label: string; value: string }>
}

export interface SettingsResponse {
  effective: Record<string, unknown>
  sources: Record<string, 'default' | 'file' | 'db'>
  schema: SettingEntry[]
}

export interface HealthResponse {
  status: string
  version: string
  db: string
  adb: { path: string; version: string; devices: number }
  data_dir: string
}

export interface MaintenanceStats {
  db_size: number
  artifacts_size: number
  runs: number
  artifacts: number
  oldest_run_at: string | null
  retention_days: number
}

// ---- API 错误信封
export interface ApiErrorEnvelope {
  error: { code: string; message: string; details?: Record<string, unknown> }
}

export interface ItemsResponse<T> {
  items: T[]
  total: number
}
