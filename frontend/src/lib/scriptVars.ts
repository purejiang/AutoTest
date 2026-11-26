// 脚本变量工具（无 DOM 依赖，可直接用 Node 跑测试）：
// - 扫描脚本/节点 JSON 里用到的 `{{var}}`（正则与后端 context.py 一致）；
// - 声明变量与缺失变量的判断；
// - 输入框光标处插入 `{{var}}`（编辑器变量按钮用）；
// - 运行覆盖的 diff（只发送与脚本默认值不同的项）；
// - 按脚本记忆上次运行参数（localStorage，隐私模式下静默降级）。

/** 与后端 `app/runner/context.py::_VAR_RE` 完全一致。 */
export const VARIABLE_RE_SOURCE = String.raw`\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}`
export const VARIABLE_RE = new RegExp(VARIABLE_RE_SOURCE, 'g')
/** 变量名允许的字符（与插值正则的捕获组一致）。 */
export const VARIABLE_NAME_RE = /^[A-Za-z0-9_.-]+$/
export const RUN_VARS_STORAGE_PREFIX = 'autotest.runVars.v1.'

export function buildToken(name: string): string {
  return `{{${name}}}`
}

export function validVariableName(name: string): boolean {
  return VARIABLE_NAME_RE.test(name)
}

/** 从一段文本里提取变量名（去重、保序）。 */
export function parseVariableNames(text: string): string[] {
  const names: string[] = []
  const seen = new Set<string>()
  for (const match of text.matchAll(VARIABLE_RE)) {
    const name = match[1]
    if (!name || seen.has(name)) continue
    seen.add(name)
    names.push(name)
  }
  return names
}

/**
 * 递归扫描任意 JSON（脚本 data / 单个节点 params+when），收集用到的变量名（排序去重）。
 * 只用字符串字段；数字/布尔不会出现变量。
 */
export function collectUsedVariables(data: unknown, out: Set<string> = new Set<string>()): string[] {
  const walk = (value: unknown): void => {
    if (typeof value === 'string') {
      for (const name of parseVariableNames(value)) out.add(name)
      return
    }
    if (Array.isArray(value)) {
      for (const item of value) walk(item)
      return
    }
    if (value && typeof value === 'object') {
      for (const item of Object.values(value as Record<string, unknown>)) walk(item)
    }
  }
  walk(data)
  return [...out].sort()
}

/** 规范化脚本 variables（只保留字符串值）。 */
export function declaredVariables(
  variables: Record<string, unknown> | null | undefined,
): Record<string, string> {
  const out: Record<string, string> = {}
  if (!variables) return out
  for (const [key, value] of Object.entries(variables)) {
    if (typeof value === 'string') out[key] = value
  }
  return out
}

export function missingVariables(declared: Record<string, string>, used: Iterable<string>): string[] {
  const out: string[] = []
  for (const name of used) {
    if (!(name in declared)) out.push(name)
  }
  return out
}

/** 脚本默认值 + 覆盖值合并（覆盖优先）。 */
export function mergeVariableValues(
  defaults: Record<string, string>,
  overrides: Record<string, string>,
): Record<string, string> {
  return { ...defaults, ...overrides }
}

/** 只保留“真正的覆盖”：与脚本默认值相同/未声明且为空的项不发送。 */
export function diffOverrides(
  defaults: Record<string, string>,
  values: Record<string, string>,
): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(values)) {
    if (typeof value !== 'string') continue
    if (!(key in defaults) || defaults[key] !== value) out[key] = value
  }
  return out
}

export interface InsertTokenResult {
  text: string
  /** 插入后光标应停留的位置。 */
  caret: number
}

/** 在 [start, end) 处插入 `{{name}}`；越界会夹紧，选区反向会自动交换。 */
export function insertToken(value: string, name: string, start?: number, end?: number): InsertTokenResult {
  const token = buildToken(name)
  const length = value.length
  const clamp = (index: number): number => Math.min(Math.max(0, index), length)
  let from = start === undefined ? length : clamp(start)
  let to = end === undefined ? from : clamp(end)
  if (to < from) {
    const swap = from
    from = to
    to = swap
  }
  return { text: value.slice(0, from) + token + value.slice(to), caret: from + token.length }
}

// ---------------------------------------------------------------- 按脚本记忆

function storage(): Storage | null {
  try {
    if (typeof window === 'undefined') return null
    return window.localStorage ?? null
  } catch {
    // 隐私模式 / 禁用 storage
    return null
  }
}

function storageKey(scriptId: string): string {
  return `${RUN_VARS_STORAGE_PREFIX}${scriptId}`
}

/** 读取上次运行参数（只含覆盖项）；读不到返回空对象。 */
export function loadRememberedRunVars(scriptId: string): Record<string, string> {
  const store = storage()
  if (!store || !scriptId) return {}
  try {
    const raw = store.getItem(storageKey(scriptId))
    if (!raw) return {}
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    const out: Record<string, string> = {}
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof value === 'string') out[key] = value
    }
    return out
  } catch {
    return {}
  }
}

export function saveRememberedRunVars(scriptId: string, overrides: Record<string, string>): void {
  const store = storage()
  if (!store || !scriptId) return
  try {
    if (Object.keys(overrides).length === 0) store.removeItem(storageKey(scriptId))
    else store.setItem(storageKey(scriptId), JSON.stringify(overrides))
  } catch {
    // 配额/禁用：忽略
  }
}

export function clearRememberedRunVars(scriptId: string): void {
  const store = storage()
  if (!store || !scriptId) return
  try {
    store.removeItem(storageKey(scriptId))
  } catch {
    // 忽略
  }
}
