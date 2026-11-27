import { describe, expect, it } from 'vitest'

import {
  editRetry,
  normalizeScriptRetry,
  resolveRetry,
  scriptRetryOn,
  toggleRetry,
} from './retry'

const defaultRetry = { count: 1, interval_ms: 1000 }

describe('resolveRetry', () => {
  it('节点未配置且脚本没有默认：不重试', () => {
    expect(resolveRetry(null, null)).toEqual({
      mode: 'off',
      enabled: false,
      effective: null,
      overridden: false,
    })
  })

  it('节点未配置但脚本有默认：显示为继承且生效', () => {
    expect(resolveRetry(null, defaultRetry)).toEqual({
      mode: 'inherit',
      enabled: true,
      effective: defaultRetry,
      overridden: false,
    })
  })

  it('节点 count 0 是显式关闭，覆盖脚本默认', () => {
    expect(resolveRetry({ count: 0, interval_ms: 500 }, defaultRetry)).toEqual({
      mode: 'off',
      enabled: false,
      effective: null,
      overridden: true,
    })
  })

  it('节点自定义：按节点值生效', () => {
    const custom = { count: 2, interval_ms: 500 }
    expect(resolveRetry(custom, defaultRetry)).toEqual({
      mode: 'custom',
      enabled: true,
      effective: custom,
      overridden: true,
    })
  })
})

describe('toggleRetry', () => {
  it('关闭时脚本有默认：写 count 0 显式覆盖，保留脚本间隔', () => {
    expect(toggleRetry(false, null, { count: 2, interval_ms: 800 })).toEqual({
      count: 0,
      interval_ms: 800,
    })
  })

  it('关闭时没有脚本默认：回到 null', () => {
    expect(toggleRetry(false, null, null)).toBeNull()
  })

  it('从显式关闭重新打开：保留间隔，次数取脚本默认', () => {
    expect(toggleRetry(true, { count: 0, interval_ms: 800 }, defaultRetry)).toEqual({
      count: 1,
      interval_ms: 800,
    })
  })

  it('从继承打开：物化为脚本默认的自定义值', () => {
    expect(toggleRetry(true, null, defaultRetry)).toEqual({ count: 1, interval_ms: 1000 })
  })
})

describe('editRetry', () => {
  it('继承态修改次数：自动物化节点自定义，间隔取脚本默认', () => {
    expect(editRetry('count', 3, null, defaultRetry)).toEqual({ count: 3, interval_ms: 1000 })
  })

  it('次数改成 0：保留 count 0 作为显式关闭', () => {
    expect(editRetry('count', 0, { count: 2, interval_ms: 500 }, null)).toEqual({
      count: 0,
      interval_ms: 500,
    })
  })

  it('间隔会被夹在合法范围', () => {
    expect(editRetry('interval_ms', 99_999, null, null)).toEqual({
      count: 1,
      interval_ms: 60_000,
    })
    expect(editRetry('interval_ms', -5, null, null).interval_ms).toBe(0)
  })
})

describe('脚本默认重试归一', () => {
  it('count 0 / null 都视为关闭', () => {
    expect(scriptRetryOn(null)).toBe(false)
    expect(scriptRetryOn({ count: 0, interval_ms: 1000 })).toBe(false)
    expect(normalizeScriptRetry({ count: 0, interval_ms: 1000 })).toBeNull()
  })

  it('count > 0 保留', () => {
    expect(scriptRetryOn(defaultRetry)).toBe(true)
    expect(normalizeScriptRetry(defaultRetry)).toEqual(defaultRetry)
  })
})
