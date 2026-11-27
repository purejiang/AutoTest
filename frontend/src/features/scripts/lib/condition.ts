// v0.2 执行条件：工厂、深拷贝、递归中文摘要。
import type {
  ConditionApp,
  ConditionGroup,
  ConditionNode,
  ConditionTarget,
  ConditionTime,
  When,
} from '@/types/api'

// 后端契约：days 使用 ISO 星期编号 1..7（周一=1，周日=7）。
export const WEEKDAY_OPTIONS: Array<{ value: number; label: string }> = [
  { value: 1, label: '一' },
  { value: 2, label: '二' },
  { value: 3, label: '三' },
  { value: 4, label: '四' },
  { value: 5, label: '五' },
  { value: 6, label: '六' },
  { value: 7, label: '日' },
]

export const CONDITION_TYPE_OPTIONS = [
  { value: 'target', label: '目标 target' },
  { value: 'app', label: '应用 app' },
  { value: 'time', label: '时间 time' },
]

export function createTargetCondition(): ConditionTarget {
  return { type: 'target', target: { by: 'text', value: '', index: 0 }, state: 'visible' }
}

export function createAppCondition(): ConditionApp {
  return { type: 'app', package: '', activity: '', state: 'foreground' }
}

export function createTimeCondition(): ConditionTime {
  return { type: 'time', start: '00:00', end: '23:59', days: [1, 2, 3, 4, 5, 6, 7] }
}

export function createGroup(op: 'and' | 'or' = 'and', items: ConditionNode[] = []): ConditionGroup {
  return { type: 'group', op, items }
}

export function createNot(item: ConditionNode = createTargetCondition()): ConditionNode {
  return { type: 'not', item }
}

export function createConditionNode(type: 'target' | 'app' | 'time'): ConditionNode {
  if (type === 'app') return createAppCondition()
  if (type === 'time') return createTimeCondition()
  return createTargetCondition()
}

/** 打开 when 时的默认条件：一个包含“目标可见”的 AND 分组。 */
export function createDefaultWhen(): When {
  return {
    mode: 'require',
    timeout_ms: 3000,
    poll_interval_ms: 350,
    condition: createGroup('and', [createTargetCondition()]),
  }
}

export function cloneCondition<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function targetSummary(target: ConditionTarget['target']): string {
  if (target.by === 'point') return `坐标 (${target.x ?? '?'}, ${target.y ?? '?'})`
  const byLabel: Record<string, string> = {
    text: '文本',
    text_contains: '文本包含',
    resource_id: '资源 ID',
    content_desc: '内容描述',
    class_name: '类名',
    xpath: 'XPath',
  }
  return `${byLabel[target.by] ?? target.by}「${target.value ?? ''}」`
}

function appSummary(node: ConditionApp): string {
  const app = node.package || node.activity || '当前应用'
  const state = node.state === 'foreground' ? '在前台' : '在后台'
  return `${app} ${state}`
}

function timeSummary(node: ConditionTime): string {
  const days = node.days.length === 0 ? '每天' : node.days.map((day) => `周${WEEKDAY_OPTIONS.find((item) => item.value === day)?.label ?? day}`).join('、')
  return `${days} ${node.start || '00:00'}–${node.end || '23:59'}`
}

/** 递归条件摘要（UI 展示/事件摘要共用）。 */
export function describeCondition(node: ConditionNode | null | undefined): string {
  if (!node) return '未配置条件'
  switch (node.type) {
    case 'group': {
      if (node.items.length === 0) return node.op === 'and' ? '（空 AND 分组）' : '（空 OR 分组）'
      const joiner = node.op === 'and' ? ' 且 ' : ' 或 '
      const text = node.items.map((item) => wrapChild(item, node.op)).join(joiner)
      return node.items.length > 1 ? `（${text}）` : text
    }
    case 'not':
      return `非（${describeCondition(node.item)}）`
    case 'target':
      return `${targetSummary(node.target)} ${node.state === 'visible' ? '可见' : '消失'}`
    case 'app':
      return appSummary(node)
    case 'time':
      return timeSummary(node)
    default:
      return '未知条件'
  }
}

function wrapChild(node: ConditionNode, parentOp: 'and' | 'or'): string {
  if (node.type === 'group' && node.items.length > 1 && node.op !== parentOp) {
    return `（${describeCondition(node)}）`
  }
  return describeCondition(node)
}

/** when 的完整摘要（含模式与超时）。 */
export function describeWhen(when: When | null | undefined): string {
  if (!when) return '未启用执行条件'
  const mode = when.mode === 'require' ? '条件不满足则失败' : '条件不满足则跳过'
  const timeout = when.timeout_ms > 0 ? `${when.timeout_ms}ms 超时` : '只检查一次'
  return `${describeCondition(when.condition)} · ${mode} · ${timeout}`
}

export function conditionTypeLabel(node: ConditionNode): string {
  switch (node.type) {
    case 'group':
      return node.op === 'and' ? 'AND 分组' : 'OR 分组'
    case 'not':
      return '取反 NOT'
    case 'target':
      return '目标 target'
    case 'app':
      return '应用 app'
    case 'time':
      return '时间 time'
  }
}
