// 12 类节点的元信息、默认 params、新建节点构造、脚本数据兜底（v0.2：when 默认关闭）。
import { createDefaultWhen } from './lib/condition'
import type {
  NodeType,
  RerunSafety,
  Script,
  ScriptData,
  ScriptSettings,
  StepNode,
  When,
} from '@/types/api'

export interface NodeTypeMeta {
  label: string
  tone: 'blue' | 'green' | 'amber' | 'red' | 'violet' | 'cyan' | 'gray'
  icon: string
  subtitle: string
}

/** 节点「重跑安全性」选项：NodeForm 与脚本默认（新建节点初值）共用。 */
export const RERUN_SAFETY_OPTIONS: Array<{ value: RerunSafety; label: string }> = [
  { value: 'safe', label: 'safe（可安全重跑）' },
  { value: 'stateful', label: 'stateful（依赖状态）' },
  { value: 'destructive', label: 'destructive（破坏性，禁止重跑）' },
]

const RERUN_SAFETY_VALUES = new Set<RerunSafety>(['safe', 'stateful', 'destructive'])

export const NODE_TYPE_META: Record<NodeType, NodeTypeMeta> = {
  click: { label: '点击', tone: 'blue', icon: '👆', subtitle: 'click' },
  long_click: { label: '长按', tone: 'violet', icon: '✋', subtitle: 'long_click' },
  swipe: { label: '滑动', tone: 'cyan', icon: '↔', subtitle: 'swipe' },
  drag: { label: '拖拽', tone: 'violet', icon: '✊', subtitle: 'drag' },
  input: { label: '输入', tone: 'amber', icon: '⌨', subtitle: 'input' },
  key: { label: '按键', tone: 'amber', icon: '⌘', subtitle: 'key' },
  wait: { label: '等待', tone: 'green', icon: '⏳', subtitle: 'wait' },
  app: { label: '应用', tone: 'violet', icon: '▣', subtitle: 'app' },
  shell: { label: 'Shell', tone: 'gray', icon: '>_', subtitle: 'shell' },
  assert: { label: '断言', tone: 'green', icon: '✓', subtitle: 'assert' },
  sub_script: { label: '子脚本', tone: 'blue', icon: '⧉', subtitle: 'sub_script' },
  screenshot: { label: '截图', tone: 'amber', icon: '◉', subtitle: 'screenshot' },
}

export const NODE_TYPES: NodeType[] = [
  'click',
  'long_click',
  'swipe',
  'drag',
  'input',
  'key',
  'wait',
  'app',
  'shell',
  'assert',
  'sub_script',
  'screenshot',
]

/** 「新增节点」菜单分组：12 类节点一次扫完，不必在长列表里找。 */
export const NODE_TYPE_GROUPS: Array<{ label: string; types: NodeType[] }> = [
  { label: '交互', types: ['click', 'long_click', 'swipe', 'drag', 'input', 'key'] },
  { label: '流程', types: ['wait', 'app', 'sub_script'] },
  { label: '校验与采集', types: ['assert', 'screenshot'] },
  { label: '系统', types: ['shell'] },
]

function textTarget(): Record<string, unknown> {
  return { by: 'text', value: '', index: 0 }
}

const DEFAULT_PARAMS: Record<NodeType, () => Record<string, unknown>> = {
  click: () => ({ target: textTarget() }),
  long_click: () => ({ target: textTarget(), duration_ms: 800 }),
  swipe: () => ({
    from: { by: 'point', x: 540, y: 1600 },
    to: { by: 'point', x: 540, y: 600 },
    duration_ms: 300,
  }),
  drag: () => ({
    from: { by: 'point', x: 540, y: 1600 },
    to: { by: 'point', x: 540, y: 600 },
    duration_ms: 1000,
  }),
  input: () => ({ text: '', target: null, clear_first: false }),
  key: () => ({ key: 'back' }),
  wait: () => ({ mode: 'sleep', ms: 1000 }),
  app: () => ({ action: 'launch', package: '' }),
  shell: () => ({ command: '', timeout_ms: 10000, check_exit_code: true }),
  assert: () => ({ kind: 'exists', target: textTarget() }),
  sub_script: () => ({ script_id: '', variables: {} }),
  screenshot: () => ({ name: null }),
}

export function defaultParams(type: NodeType): Record<string, unknown> {
  const factory = DEFAULT_PARAMS[type] ?? DEFAULT_PARAMS.click
  return factory()
}

function nextStepId(existingIds: string[]): string {
  const used = new Set(existingIds)
  let index = existingIds.length + 1
  while (used.has(`s${index}`)) index += 1
  return `s${index}`
}

/** 新节点短名称：扫描现有「标签+数字」，取最大序号 +1（例如 点击1、点击2、拖拽1）。 */
export function nextNodeName(type: NodeType, steps: StepNode[]): string {
  const label = NODE_TYPE_META[type].label
  let max = 0
  for (const step of steps) {
    const name = String(step.name || '').trim()
    if (!name.startsWith(label)) continue
    const suffix = name.slice(label.length)
    if (!/^\d+$/.test(suffix)) continue
    const value = Number(suffix)
    if (Number.isFinite(value)) max = Math.max(max, value)
  }
  return `${label}${max + 1}`
}

export function createStep(type: NodeType, existingIds: string[]): StepNode {
  return {
    id: nextStepId(existingIds),
    name: `${NODE_TYPE_META[type].label}节点`,
    type,
    description: '',
    enabled: true,
    // v0.2：手动新节点默认不带执行条件；录制节点同样默认关闭。
    when: null,
    timeout_ms: null,
    delay_before_ms: 0,
    delay_after_ms: 0,
    retry: null,
    on_fail: null,
    screenshot_on_fail: true,
    rerun_safety: 'safe',
    tags: [],
    params: defaultParams(type),
  }
}

/** 供 UI 显式启用条件时使用。 */
export function createWhen(): When {
  return createDefaultWhen()
}

const DEFAULT_SETTINGS: ScriptSettings = {
  default_timeout_ms: 8000,
  default_delay_before_ms: 0,
  default_delay_after_ms: 0,
  on_fail: 'abort',
  logcat: false,
  logcat_scope: 'device',
  logcat_package: '',
  network_capture: false,
  retry: null,
  screenshot_on_fail: true,
  default_rerun_safety: 'safe',
}

export const DEFAULT_SCRIPT_SETTINGS: ScriptSettings = { ...DEFAULT_SETTINGS }

/** 新节点应用脚本默认值（目前是重跑安全性；清除数据快捷节点会再覆盖为 destructive）。 */
export function applyNewNodeDefaults(step: StepNode, settings: ScriptSettings): StepNode {
  return { ...step, rerun_safety: settings.default_rerun_safety }
}

export function normalizeScriptData(script: Script): ScriptData {
  return normalizeDraft({ ...(script.data ?? {}), name: script.data?.name ?? script.name })
}

/** 宽松兜底：后端返回的 data 可能缺字段（导入/迁移），前端补齐到完整 ScriptData。 */
export function normalizeDraft(raw: Partial<ScriptData>): ScriptData {
  const rawSafety = raw.settings?.default_rerun_safety
  return {
    schema_version: raw.schema_version ?? 1,
    name: raw.name ?? '未命名脚本',
    notes: raw.notes ?? '',
    variables: raw.variables ?? {},
    settings: {
      ...DEFAULT_SETTINGS,
      ...(raw.settings ?? {}),
      // 旧脚本可能没有该字段或存过 null；统一按关闭处理。
      network_capture: Boolean(raw.settings?.network_capture),
      logcat_scope: raw.settings?.logcat_scope === 'app' ? 'app' : 'device',
      logcat_package: String(raw.settings?.logcat_package ?? ''),
      default_rerun_safety:
        rawSafety && RERUN_SAFETY_VALUES.has(rawSafety) ? rawSafety : 'safe',
      // 旧版同时支持 defaults.retry 与 settings.retry；折叠成单一来源。
      retry: raw.settings?.retry ?? raw.defaults?.retry ?? null,
    },
    defaults: { retry: null },
    steps: Array.isArray(raw.steps)
      ? raw.steps.map((step) => ({ ...step, when: step.when ?? null }))
      : [],
  }
}

/** 深拷贝草稿，避免直接修改 vue-query 缓存对象。 */
export function cloneScriptData(data: ScriptData): ScriptData {
  return JSON.parse(JSON.stringify(data)) as ScriptData
}

export function scriptStepCount(script: Script): number {
  if (typeof script.step_count === 'number') return script.step_count
  return Array.isArray(script.data?.steps) ? script.data.steps.length : 0
}
