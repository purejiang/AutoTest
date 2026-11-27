// 节点重试的三态解析：null=继承脚本默认，{count:0}=显式关闭，count>0=自定义。
// 纯逻辑，NodeForm/ScriptSettingsForm 与单测共用。
import type { Retry } from '@/types/api'

export type RetryMode = 'inherit' | 'custom' | 'off'

export interface RetryState {
  mode: RetryMode
  /** 实际是否会发生重试（继承时看脚本默认）。 */
  enabled: boolean
  /** 实际生效的 retry 值；不重试时为 null。 */
  effective: Retry | null
  /** node.retry 是否非 null（显式自定义或显式关闭）。 */
  overridden: boolean
}

export const RETRY_COUNT_MAX = 20
export const RETRY_INTERVAL_MAX = 60_000

/** count 是额外重试次数：1 = 最多执行 2 次；0 = 不重试。 */
export function hasRetry(retry: Retry | null | undefined): retry is Retry {
  return Boolean(retry && retry.count > 0)
}

export function resolveRetry(nodeRetry: Retry | null, scriptRetry: Retry | null): RetryState {
  if (nodeRetry === null) {
    if (hasRetry(scriptRetry)) {
      return { mode: 'inherit', enabled: true, effective: scriptRetry, overridden: false }
    }
    return { mode: 'off', enabled: false, effective: null, overridden: false }
  }
  if (!hasRetry(nodeRetry)) {
    return { mode: 'off', enabled: false, effective: null, overridden: true }
  }
  return { mode: 'custom', enabled: true, effective: nodeRetry, overridden: true }
}

/** 切换开关：关 = 有脚本默认时显式覆盖为 count 0；开 = 物化一个自定义值。 */
export function toggleRetry(
  enabled: boolean,
  nodeRetry: Retry | null,
  scriptRetry: Retry | null,
): Retry | null {
  if (!enabled) {
    if (hasRetry(scriptRetry)) {
      return { count: 0, interval_ms: scriptRetry.interval_ms }
    }
    return null
  }
  const interval = nodeRetry?.interval_ms ?? scriptRetry?.interval_ms ?? 1000
  const count = nodeRetry && nodeRetry.count > 0 ? nodeRetry.count : (scriptRetry?.count ?? 1)
  return { count: Math.min(RETRY_COUNT_MAX, Math.max(1, count)), interval_ms: interval }
}

/** 编辑次数/间隔；继承态编辑会自动物化为节点自定义。 */
export function editRetry(
  field: 'count' | 'interval_ms',
  value: number,
  nodeRetry: Retry | null,
  scriptRetry: Retry | null,
): Retry {
  const base =
    nodeRetry && nodeRetry.count >= 0
      ? nodeRetry
      : (scriptRetry ?? { count: 1, interval_ms: 1000 })
  if (field === 'count') {
    const count = Math.min(RETRY_COUNT_MAX, Math.max(0, Math.trunc(value || 0)))
    return { count, interval_ms: base.interval_ms }
  }
  const interval = Math.min(RETRY_INTERVAL_MAX, Math.max(0, Math.trunc(value || 0)))
  return { count: base.count, interval_ms: interval }
}

/** 脚本默认重试：count 0 等价于关闭，统一归一到 null。 */
export function scriptRetryOn(retry: Retry | null | undefined): boolean {
  return hasRetry(retry)
}

export function normalizeScriptRetry(retry: Retry | null): Retry | null {
  return hasRetry(retry) ? retry : null
}
