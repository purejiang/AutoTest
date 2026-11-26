// 节点参数摘要：把 StepNode.params 压成节点列表里的一行可读文本。
import type { StepNode, Target } from '@/types/api'

type Params = Record<string, unknown>

const APP_ACTION_LABELS: Record<string, string> = {
  launch: '启动',
  stop: '停止',
  clear: '清除数据',
  restart: '重启',
}

function asRecord(value: unknown): Params | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Params
}

function asString(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return ''
  return String(value)
}

function asNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function compact(value: string, max = 40): string {
  const text = value.trim()
  return text.length > max ? `${text.slice(0, max)}…` : text
}

function targetOf(value: unknown): Target | null {
  const record = asRecord(value)
  if (!record || typeof record.by !== 'string') return null
  return record as unknown as Target
}

/** 单点/元素目标 → `x: 1, y: 2` 或 `text=登录`。 */
export function describeTarget(value: unknown): string {
  const target = targetOf(value)
  if (!target) return ''
  if (target.by === 'point') {
    return `x: ${asNumber(target.x) ?? '?'}, y: ${asNumber(target.y) ?? '?'}`
  }
  const label = compact(asString(target.value))
  return label ? `${target.by}=${label}` : target.by
}

/** 坐标点 → `(1, 2)`；元素目标沿用 describeTarget。 */
function describePoint(value: unknown): string {
  const target = targetOf(value)
  if (!target) return '—'
  if (target.by === 'point') {
    return `(${asNumber(target.x) ?? '?'}, ${asNumber(target.y) ?? '?'})`
  }
  return describeTarget(target)
}

function durationSuffix(params: Params): string {
  const duration = asNumber(params.duration_ms)
  return duration === null ? '' : `${duration} ms`
}

/** 节点列表参数行（不含标题/类型标签）。 */
export function describeNodeParams(step: StepNode): string {
  const params = asRecord(step.params) ?? {}
  let summary = ''

  switch (step.type) {
    case 'click': {
      summary = describeTarget(params.target)
      break
    }
    case 'long_click': {
      const target = describeTarget(params.target)
      const duration = durationSuffix(params)
      summary = [target, duration].filter(Boolean).join(' · ')
      break
    }
    case 'swipe':
    case 'drag': {
      summary = `${describePoint(params.from)} → ${describePoint(params.to)}`
      const duration = durationSuffix(params)
      if (duration) summary += ` · ${duration}`
      break
    }
    case 'input': {
      const text = asString(params.text)
      const target = describeTarget(params.target)
      summary = [`「${compact(text || '空文本', 30)}」`, target].filter(Boolean).join(' · ')
      break
    }
    case 'key': {
      const key = asString(params.key)
      const keycode = asNumber(params.keycode)
      summary = key || (keycode === null ? '' : `keycode=${keycode}`)
      break
    }
    case 'wait': {
      const mode = asString(params.mode) || 'sleep'
      if (mode === 'until') {
        const state = asString(params.state) || 'visible'
        const target = describeTarget(params.target)
        summary = `until ${state}${target ? ` · ${target}` : ''}`
      } else {
        summary = `${asNumber(params.ms) ?? 1000} ms`
      }
      break
    }
    case 'app': {
      const action = asString(params.action) || 'launch'
      const label = APP_ACTION_LABELS[action] ?? action
      const pkg = compact(asString(params.package))
      summary = pkg ? `${label} ${pkg}` : label
      break
    }
    case 'shell': {
      summary = compact(asString(params.command), 60)
      break
    }
    case 'assert': {
      const kind = asString(params.kind) || 'exists'
      const target = describeTarget(params.target)
      const value = compact(asString(params.value))
      summary = [kind, target, value].filter(Boolean).join(' ')
      break
    }
    case 'sub_script': {
      summary = compact(asString(params.script_id), 50)
      break
    }
    case 'screenshot': {
      const name = asString(params.name)
      summary = name ? compact(name, 40) : '自动命名'
      break
    }
    default:
      summary = ''
  }

  const suffixes: string[] = []
  if (!step.enabled) suffixes.push('已禁用')
  if (step.when) suffixes.push('带条件')
  return [summary, ...suffixes].filter(Boolean).join(' · ')
}
