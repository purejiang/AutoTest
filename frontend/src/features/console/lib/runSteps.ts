import type { StepResult } from '@/types/api'
import { stepSubScriptContext } from '@/lib/format'

export interface LiveStepRow {
  key: string
  id: string
  name: string
  type: string
  index: number
  result: StepResult | null
  depth: number
  subScriptName?: string
}

/** 父脚本节点 + 子脚本内部节点；子节点按 output.sub_script 挂到父节点下。 */
export function buildLiveStepRows(
  scriptSteps: Array<{ id: string; name?: string; type: string }>,
  results: StepResult[],
): LiveStepRow[] {
  const resultByStep = new Map<string, StepResult>()
  const childrenByParent = new Map<string, StepResult[]>()
  for (const result of results) {
    const ctx = stepSubScriptContext(result)
    if (ctx) {
      const list = childrenByParent.get(ctx.parentStepId) ?? []
      list.push(result)
      childrenByParent.set(ctx.parentStepId, list)
    } else if (!resultByStep.has(result.step_id)) {
      resultByStep.set(result.step_id, result)
    }
  }

  const rows: LiveStepRow[] = []
  const seen = new Set<number>()

  function visitResult(
    result: StepResult,
    depth: number,
    index: number,
    subScriptName?: string,
  ): void {
    if (seen.has(result.id)) return
    seen.add(result.id)
    const ctx = stepSubScriptContext(result)
    rows.push({
      key: `r:${result.id}`,
      id: result.step_id,
      name: result.step_name || result.step_id,
      type: result.step_type,
      index,
      result,
      depth,
      subScriptName: subScriptName ?? ctx?.scriptName,
    })
    for (const child of childrenByParent.get(result.step_id) ?? []) {
      visitResult(child, depth + 1, index, stepSubScriptContext(child)?.scriptName)
    }
  }

  function visitScriptStep(
    step: { id: string; name?: string; type: string },
    index: number,
  ): void {
    const parentResult = resultByStep.get(step.id) ?? null
    // 父节点结果必须标记 seen，否则兜底逻辑会把主脚本节点再次当子节点追加。
    if (parentResult !== null) seen.add(parentResult.id)
    rows.push({
      key: `p:${step.id}:${index}`,
      id: step.id,
      name: step.name || step.id,
      type: step.type,
      index,
      result: parentResult,
      depth: 0,
    })
    for (const child of childrenByParent.get(step.id) ?? []) {
      visitResult(child, 1, index, stepSubScriptContext(child)?.scriptName)
    }
  }

  if (scriptSteps.length > 0) {
    scriptSteps.forEach((step, index) => visitScriptStep(step, index))
  } else {
    for (const result of results) {
      if (stepSubScriptContext(result)) continue
      visitResult(result, 0, result.step_index ?? rows.length)
    }
  }
  // 没有父节点可挂的孤立子节点也展示，但绝不能把已展示的父节点重复塞进来。
  for (const result of results) {
    if (seen.has(result.id)) continue
    visitResult(result, 1, result.step_index ?? rows.length, stepSubScriptContext(result)?.scriptName)
  }
  return rows
}
