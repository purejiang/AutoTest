import { describe, expect, it } from 'vitest'

import {
  cloneCondition,
  conditionTypeLabel,
  createAppCondition,
  createConditionNode,
  createDefaultWhen,
  createGroup,
  createNot,
  createTargetCondition,
  createTimeCondition,
  describeCondition,
  describeWhen,
} from './condition'

describe('condition factories', () => {
  it('creates the default when with one target condition', () => {
    const when = createDefaultWhen()
    expect(when.mode).toBe('require')
    expect(when.timeout_ms).toBe(3000)
    expect(when.poll_interval_ms).toBe(350)
    expect(when.condition.type).toBe('group')
    if (when.condition.type === 'group') {
      expect(when.condition.op).toBe('and')
      expect(when.condition.items).toHaveLength(1)
      expect(when.condition.items[0]?.type).toBe('target')
    }
  })

  it('creates each node type', () => {
    expect(createTargetCondition()).toEqual({
      type: 'target',
      target: { by: 'text', value: '', index: 0 },
      state: 'visible',
    })
    expect(createAppCondition()).toEqual({
      type: 'app',
      package: '',
      activity: '',
      state: 'foreground',
    })
    expect(createTimeCondition()).toEqual({
      type: 'time',
      start: '00:00',
      end: '23:59',
      days: [1, 2, 3, 4, 5, 6, 7],
    })
    expect(createConditionNode('app').type).toBe('app')
    expect(createConditionNode('time').type).toBe('time')
    expect(createConditionNode('target').type).toBe('target')
    expect(createNot().type).toBe('not')
    expect(createGroup('or').op).toBe('or')
  })

  it('clones deeply', () => {
    const group = createGroup('and', [createTargetCondition()])
    const clone = cloneCondition(group)
    if (clone.type === 'group') clone.items.push(createAppCondition())
    expect(group.items).toHaveLength(1)
    expect(clone).not.toBe(group)
  })
})

describe('describeCondition', () => {
  it('describes target / app / time leaves', () => {
    expect(
      describeCondition({ type: 'target', target: { by: 'text', value: '登录' }, state: 'visible' }),
    ).toBe('文本「登录」 可见')
    expect(describeCondition({ type: 'target', target: { by: 'point', x: 10, y: 20 }, state: 'gone' })).toBe(
      '坐标 (10, 20) 消失',
    )
    expect(describeCondition({ type: 'app', package: 'com.foo', activity: '', state: 'foreground' })).toBe(
      'com.foo 在前台',
    )
    expect(
      describeCondition({
        type: 'time',
        start: '09:00',
        end: '18:00',
        days: [1, 2],
      }),
    ).toBe('周一、周二 09:00–18:00')
    expect(describeCondition({ type: 'time', start: '00:00', end: '23:59', days: [] })).toBe(
      '每天 00:00–23:59',
    )
  })

  it('describes groups / not / empty group', () => {
    const and = createGroup('and', [createTargetCondition(), createAppCondition()])
    expect(describeCondition(and)).toBe('（文本「」 可见 且 当前应用 在前台）')
    const single = createGroup('or', [createTargetCondition()])
    expect(describeCondition(single)).toBe('文本「」 可见')
    expect(describeCondition(createGroup('and'))).toBe('（空 AND 分组）')
    expect(describeCondition(createNot(createTargetCondition()))).toBe('非（文本「」 可见）')
    expect(describeCondition(null)).toBe('未配置条件')
  })

  it('wraps nested groups with a different operator', () => {
    const inner = createGroup('or', [createTargetCondition(), createAppCondition()])
    const outer = createGroup('and', [inner])
    expect(describeCondition(outer)).toBe('（（文本「」 可见 或 当前应用 在前台））')
  })
})

describe('describeWhen', () => {
  it('describes require / optional modes', () => {
    const when = createDefaultWhen()
    expect(describeWhen(null)).toBe('未启用执行条件')
    expect(describeWhen(when)).toContain('条件不满足则失败')
    expect(describeWhen({ ...when, mode: 'optional' })).toContain('条件不满足则跳过')
    expect(describeWhen({ ...when, timeout_ms: 0 })).toContain('只检查一次')
  })
})

describe('conditionTypeLabel', () => {
  it('labels every node type', () => {
    expect(conditionTypeLabel(createGroup('and'))).toBe('AND 分组')
    expect(conditionTypeLabel(createGroup('or'))).toBe('OR 分组')
    expect(conditionTypeLabel(createNot())).toBe('取反 NOT')
    expect(conditionTypeLabel(createTargetCondition())).toBe('目标 target')
    expect(conditionTypeLabel(createAppCondition())).toBe('应用 app')
    expect(conditionTypeLabel(createTimeCondition())).toBe('时间 time')
  })
})
