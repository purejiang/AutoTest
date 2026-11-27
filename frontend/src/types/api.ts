// AutoTest v0.2 前端类型（与 docs/contracts.md 及后端 v0.2 模型对齐）
//
// v0.2 变更（不保留旧数据兼容分支）：
//   - Script.source = manual | mirror | physical | imported；origin_recording_id 记录来源录制。
//   - 节点执行条件改为 { mode, timeout_ms, poll_interval_ms, condition }；
//     condition 支持 group(and/or) / not / target / app / time。
//   - 运行事件新增 when_started / when_result。

export type RunStatus =
  | 'queued'
  | 'running'
  | 'paused'
  | 'stopping'
  | 'succeeded'
  | 'failed'
  | 'aborted'
  | 'stopped'
  | 'interrupted'

export type StepResultStatus = 'passed' | 'failed' | 'skipped' | 'error'

export type RecordingStatus = 'recording' | 'paused' | 'stopped'

/** 录制模式：web = 镜像录制（值的名字沿用后端契约），physical = 真机录制。 */
export type RecordingMode = 'web' | 'physical'

export type NodeType =
  | 'click'
  | 'long_click'
  | 'swipe'
  | 'drag'
  | 'input'
  | 'key'
  | 'wait'
  | 'app'
  | 'shell'
  | 'assert'
  | 'sub_script'
  | 'screenshot'

export type TargetBy =
  | 'text'
  | 'text_contains'
  | 'resource_id'
  | 'content_desc'
  | 'class_name'
  | 'xpath'
  | 'point'

export interface Target {
  by: TargetBy
  value?: string
  index?: number
  x?: number
  y?: number
}

// ---------------------------------------------------------------- v0.2 执行条件

export type ConditionState = 'visible' | 'gone'
export type AppConditionState = 'foreground' | 'background'
export type ConditionGroupOp = 'and' | 'or'

/** 目标条件：检查某个元素可见/消失。 */
export interface ConditionTarget {
  type: 'target'
  target: Target
  state: ConditionState
}

/** 应用条件：检查前台应用。 */
export interface ConditionApp {
  type: 'app'
  package: string
  activity?: string | null
  state: AppConditionState
}

/** 时间条件：days 用 1..7（周一=1，周日=7）；start/end 为本地 HH:mm。 */
export interface ConditionTime {
  type: 'time'
  start: string
  end: string
  days: number[]
}

/** 逻辑分组：可嵌套任意条件。 */
export interface ConditionGroup {
  type: 'group'
  op: ConditionGroupOp
  items: ConditionNode[]
}

/** 取反。 */
export interface ConditionNot {
  type: 'not'
  item: ConditionNode
}

export type ConditionNode = ConditionGroup | ConditionNot | ConditionTarget | ConditionApp | ConditionTime

export interface When {
  mode: 'require' | 'optional'
  timeout_ms: number
  poll_interval_ms: number
  condition: ConditionNode
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
export interface ClickParams {
  target: Target
}
export interface LongClickParams {
  target: Target
  duration_ms?: number
}
export interface SwipeParams {
  from: Target
  to: Target
  duration_ms?: number
}
export interface DragParams {
  from: Target
  to: Target
  duration_ms?: number
}
export interface InputParams {
  text: string
  target?: Target | null
  clear_first?: boolean
}
export interface KeyParams {
  key?: string
  keycode?: number
}
export interface WaitParams {
  mode: 'sleep' | 'until'
  ms?: number
  target?: Target | null
  state?: 'visible' | 'gone'
  timeout_ms?: number
  interval_ms?: number
}
export interface AppParams {
  action: 'launch' | 'stop' | 'clear' | 'restart'
  package?: string | null
  activity?: string | null
}
export interface ShellParams {
  command: string
  timeout_ms?: number
  check_exit_code?: boolean
}
export interface AssertParams {
  kind: 'exists' | 'not_exists' | 'text_contains' | 'foreground'
  target?: Target | null
  value?: string
}
export interface SubScriptParams {
  script_id: string
  variables?: Record<string, string>
}
export interface ScreenshotParams {
  name?: string | null
}

export interface StepNode {
  id: string
  name: string
  type: NodeType
  description: string
  enabled: boolean
  /** null = 未启用执行条件（默认关闭） */
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
  /** 全节点默认执行前停顿；节点 delay_before_ms=0 时生效（0 = 不停顿） */
  default_delay_before_ms: number
  /** 全节点默认执行后停顿；节点 delay_after_ms=0 时生效（0 = 不停顿） */
  default_delay_after_ms: number
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

export type ScriptSource = 'manual' | 'mirror' | 'physical' | 'imported'

/** 脚本列表/详情附带的“最近一次运行”摘要（后端 additive 字段）。 */
export interface ScriptLastRun {
  id: number
  status: RunStatus
  started_at: string | null
  ended_at: string | null
  created_at: string
  device_serial: string
  error: { code?: string; message?: string } | null
  summary: {
    passed_steps?: number
    failed_steps?: number
    skipped_steps?: number
    duration_ms?: number
    total_steps?: number
  } | null
}

export interface Script {
  id: string
  name: string
  notes: string
  source: ScriptSource
  /** 由录制生成时的来源录制 id；手动/导入为空。 */
  origin_recording_id?: string | null
  schema_version: number
  data: ScriptData
  step_count?: number
  /** 脚本是 JSON 文件，时间戳可能缺失（导入/手写文件）时为 null。 */
  created_at: string | null
  updated_at: string | null
  last_run?: ScriptLastRun | null
}

export interface ValidationErrorItem {
  path: string
  code: string
  message: string
}

export interface ScriptValidation {
  valid: boolean
  errors: ValidationErrorItem[]
  warnings: ValidationErrorItem[]
}

// ---- 设备
export interface DeviceMirrorInfo {
  active: boolean
  width?: number
  height?: number
}

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
  fake?: boolean
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
    total_steps?: number
  } | null
  created_at: string
  debug?: { step_mode?: boolean; breakpoints?: string[] } | null
  /** 本次运行的变量覆盖（只含与脚本默认值不同的项）。 */
  variables?: Record<string, string>
  /** 本次运行实际生效的变量（脚本默认 + 覆盖）。 */
  effective_variables?: Record<string, string>
}

/** 条件求值结果（后端字段未完全冻结时兼容多种落点，仅用于展示）。 */
export interface StepConditionOutcome {
  evaluated?: boolean
  met?: boolean
  skipped?: boolean
  failed?: boolean
  waited_ms?: number
  timeout_ms?: number
  state?: string
  detail?: string
  /** 后端 v0.2 的条件中文摘要（when_result / step output 携带）。 */
  condition_summary?: string
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
  /** v0.2：条件求值结果（可能位于 output.when 内）。 */
  when?: StepConditionOutcome | null
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
  mode: RecordingMode
  serial: string
  status: RecordingStatus
  started_at: string | null
  ended_at: string | null
  event_count: number
  /** 已转脚本时关联的脚本 id / 名称（v0.2 录制页新增关联脚本列）。 */
  script_id: string | null
  script_name: string | null
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
    /** 后端落库字段为 node */
    node?: Target | null
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
  desc?: string | null
  min?: number | null
  max?: number | null
  /** type='enum' 时的可选项（后端可能返回 null） */
  options?: Array<{ label: string; value: string }> | null
  /** 代码默认值（GET /api/settings 的 additive 字段） */
  default?: unknown
  /** bootstrap = 启动项（只读，改配置文件/环境变量）；runtime = 设置页可保存 */
  layer?: 'bootstrap' | 'runtime' | string | null
  /** 是否允许在设置页保存（启动项为 false） */
  writable?: boolean | null
  /** 对应环境变量名（没有映射时为 null） */
  env?: string | null
}

export interface SettingsResponse {
  effective: Record<string, unknown>
  sources: Record<string, 'default' | 'file' | 'env'>
  /** 配置文件路径（启动项提示用，additive 字段） */
  config_file?: string
  /** POST /api/settings/reload 时：改了但需要重启才生效的启动项 */
  restart_required?: string[] | null
  schema: SettingEntry[]
}

export interface HealthResponse {
  status: string
  version: string
  db: string
  adb: { path: string; version: string; devices: number }
  data_dir: string
  /** 脚本 JSON 目录（additive） */
  scripts_dir?: string
  /** 配置文件路径（additive） */
  config_file?: string
  /** 后端运行代码的构建元信息（源码运行时由 git 推断） */
  build?: { commit: string | null; dirty: boolean | null; built_at: string | null }
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

// ---- 批量删除（v0.2 单条失败不阻塞其他条）
export interface BulkDeleteFailure<TId extends string | number = string> {
  id: TId
  code: string
  message: string
}

export interface BulkDeleteResult<TId extends string | number = string> {
  deleted: number
  failed: BulkDeleteFailure<TId>[]
}

export interface ItemsResponse<T> {
  items: T[]
  total: number
}
