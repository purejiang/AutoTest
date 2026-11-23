// 12 类节点的元信息、默认 params、新建节点构造、脚本数据兜底
import type { NodeType, Script, ScriptData, ScriptSettings, StepNode } from '@/types/api'

export interface NodeTypeMeta {
  label: string
  color: string
  icon: string
}

export const NODE_TYPE_META: Record<NodeType, NodeTypeMeta> = {
  click: { label: '点击', color: 'blue', icon: '👆' },
  long_click: { label: '长按', color: 'geekblue', icon: '✋' },
  swipe: { label: '滑动', color: 'cyan', icon: '↔️' },
  drag: { label: '拖拽', color: 'purple', icon: '✊' },
  input: { label: '输入', color: 'orange', icon: '⌨️' },
  key: { label: '按键', color: 'gold', icon: '🔘' },
  wait: { label: '等待', color: 'lime', icon: '⏳' },
  app: { label: '应用', color: 'magenta', icon: '📱' },
  shell: { label: 'Shell', color: 'volcano', icon: '>_' },
  assert: { label: '断言', color: 'green', icon: '✓' },
  sub_script: { label: '子脚本', color: 'processing', icon: '🧩' },
  screenshot: { label: '截图', color: 'pink', icon: '📷' },
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

export const NODE_TYPE_OPTIONS = NODE_TYPES.map((type) => ({
  value: type,
  label: `${NODE_TYPE_META[type].icon} ${NODE_TYPE_META[type].label}`,
}))

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

export function createStep(type: NodeType, existingIds: string[]): StepNode {
  return {
    id: nextStepId(existingIds),
    name: `${NODE_TYPE_META[type].label}节点`,
    type,
    description: '',
    enabled: true,
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

const DEFAULT_SETTINGS: ScriptSettings = {
  default_timeout_ms: 8000,
  on_fail: 'abort',
  logcat: false,
  retry: null,
  screenshot_on_fail: true,
}

export const DEFAULT_SCRIPT_SETTINGS: ScriptSettings = { ...DEFAULT_SETTINGS }

export function normalizeScriptData(script: Script): ScriptData {
  return normalizeDraft({ ...(script.data ?? {}), name: script.data?.name ?? script.name })
}

/** 宽松兜底：后端返回的 data 可能缺字段（导入/迁移），前端补齐到完整 ScriptData。 */
export function normalizeDraft(raw: Partial<ScriptData>): ScriptData {
  return {
    schema_version: raw.schema_version ?? 1,
    name: raw.name ?? '未命名脚本',
    notes: raw.notes ?? '',
    variables: raw.variables ?? {},
    settings: { ...DEFAULT_SETTINGS, ...(raw.settings ?? {}) },
    defaults: { retry: null, ...(raw.defaults ?? {}) },
    steps: Array.isArray(raw.steps) ? raw.steps : [],
  }
}

/** 深拷贝草稿，避免直接修改 react-query 缓存对象。 */
export function cloneScriptData(data: ScriptData): ScriptData {
  return JSON.parse(JSON.stringify(data)) as ScriptData
}

export function scriptStepCount(script: Script): number {
  if (typeof script.step_count === 'number') return script.step_count
  return Array.isArray(script.data?.steps) ? script.data.steps.length : 0
}
