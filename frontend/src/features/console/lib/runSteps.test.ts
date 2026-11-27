import { describe, expect, it } from 'vitest'

import type { StepResult } from '@/types/api'

import { buildLiveStepRows } from './runSteps'

function result(partial: Partial<StepResult> & Pick<StepResult, 'id' | 'step_id'>): StepResult {
  return {
    run_id: 1,
    step_name: partial.step_id,
    step_type: 'click',
    step_index: 0,
    status: 'passed',
    attempts: 1,
    started_at: null,
    ended_at: null,
    duration_ms: 1,
    error: null,
    output: null,
    ...partial,
  }
}

describe('buildLiveStepRows', () => {
  it('父脚本节点不会因为结果兜底而被重复追加到子节点下', () => {
    const steps = [
      { id: 'p1', name: '调用子脚本', type: 'sub_script' },
      { id: 'p2', name: '后续点击', type: 'click' },
    ]
    const results = [
      result({ id: 1, step_id: 'p2', step_name: '后续点击' }),
      result({ id: 2, step_id: 'p1', step_name: '调用子脚本', step_type: 'sub_script' }),
    ]

    const rows = buildLiveStepRows(steps, results)

    expect(rows.map((row) => [row.id, row.depth])).toEqual([
      ['p1', 0],
      ['p2', 0],
    ])
  })

  it('子脚本节点缩进挂在父节点下，后续主节点仍是 depth 0', () => {
    const steps = [
      { id: 'p1', name: '调用子脚本', type: 'sub_script' },
      { id: 'p2', name: '后续点击', type: 'click' },
    ]
    const results = [
      result({ id: 1, step_id: 'p2', step_name: '后续点击' }),
      result({
        id: 2,
        step_id: 'c1',
        step_name: '子点击',
        output: {
          sub_script: {
            parent_step_id: 'p1',
            depth: 1,
            script_id: 'child',
            script_name: '子脚本',
          },
        },
      }),
      result({ id: 3, step_id: 'p1', step_name: '调用子脚本', step_type: 'sub_script' }),
    ]

    const rows = buildLiveStepRows(steps, results)

    expect(rows.map((row) => [row.id, row.depth, row.subScriptName ?? null])).toEqual([
      ['p1', 0, null],
      ['c1', 1, '子脚本'],
      ['p2', 0, null],
    ])
  })
})
