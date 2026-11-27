import { describe, expect, it } from 'vitest'

import type { ScriptSettings } from '@/types/api'

import {
  DEFAULT_SCRIPT_SETTINGS,
  applyNewNodeDefaults,
  createStep,
  normalizeDraft,
} from './nodeDefaults'

describe('脚本采集设置', () => {
  it('network_capture 默认关闭，旧 null 也归一到 false', () => {
    expect(DEFAULT_SCRIPT_SETTINGS.network_capture).toBe(false)
    expect(normalizeDraft({ steps: [] }).settings.network_capture).toBe(false)
    expect(
      normalizeDraft({ settings: { network_capture: null } as unknown as ScriptSettings })
        .settings.network_capture,
    ).toBe(false)
  })

  it('保留脚本里的显式开关', () => {
    const on = normalizeDraft({ settings: { network_capture: true } as ScriptSettings })
    const off = normalizeDraft({ settings: { network_capture: false } as ScriptSettings })
    expect(on.settings.network_capture).toBe(true)
    expect(off.settings.network_capture).toBe(false)
  })
})

describe('脚本默认重试来源折叠', () => {
  it('旧 defaults.retry 在 settings.retry 为空时折叠进来，并清空 defaults', () => {
    const data = normalizeDraft({
      settings: { retry: null } as ScriptSettings,
      defaults: { retry: { count: 2, interval_ms: 500 } },
      steps: [],
    })
    expect(data.settings.retry).toEqual({ count: 2, interval_ms: 500 })
    expect(data.defaults.retry).toBeNull()
  })

  it('settings.retry 已有值时优先保留，defaults.retry 仍被清空', () => {
    const data = normalizeDraft({
      settings: { retry: { count: 1, interval_ms: 1000 } } as ScriptSettings,
      defaults: { retry: { count: 9, interval_ms: 9000 } },
      steps: [],
    })
    expect(data.settings.retry).toEqual({ count: 1, interval_ms: 1000 })
    expect(data.defaults.retry).toBeNull()
  })
})

describe('新建节点默认重跑安全性', () => {
  it('缺省为 safe，非法值回退 safe', () => {
    expect(DEFAULT_SCRIPT_SETTINGS.default_rerun_safety).toBe('safe')
    expect(normalizeDraft({ steps: [] }).settings.default_rerun_safety).toBe('safe')
    expect(
      normalizeDraft({ settings: { default_rerun_safety: 'unsafe' } as unknown as ScriptSettings })
        .settings.default_rerun_safety,
    ).toBe('safe')
  })

  it('applyNewNodeDefaults 把脚本默认写进新节点', () => {
    const step = createStep('click', [])
    const next = applyNewNodeDefaults(step, {
      ...DEFAULT_SCRIPT_SETTINGS,
      default_rerun_safety: 'destructive',
    })
    expect(next.rerun_safety).toBe('destructive')
    expect(step.rerun_safety).toBe('safe') // 不修改原对象
  })
})
