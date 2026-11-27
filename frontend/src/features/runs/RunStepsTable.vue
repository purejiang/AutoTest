<script setup lang="ts">
import { computed } from 'vue'

import type { Artifact, StepResult } from '@/types/api'
import {
  artifactHref,
  artifactKindMeta,
  formatDuration,
  prettyJson,
  stepConditionOutcome,
  stepStatusMeta,
  stepSubScriptContext,
  stepWaitMs,
} from '@/lib/format'
import StatusTag from '@/components/StatusTag.vue'

const props = defineProps<{
  steps: StepResult[]
  currentStepId?: string | null
  artifacts?: Artifact[]
}>()

interface StepRow extends StepResult {
  depth: number
  subScriptName?: string
}

/** 父脚本节点在前、子脚本内部节点紧跟父节点缩进展示；旧运行没有 sub_script 元数据时保持平铺。 */
const rows = computed<StepRow[]>(() => {
  const byParent = new Map<string, StepResult[]>()
  const roots: StepResult[] = []
  for (const step of props.steps) {
    const ctx = stepSubScriptContext(step)
    if (ctx) {
      const list = byParent.get(ctx.parentStepId) ?? []
      list.push(step)
      byParent.set(ctx.parentStepId, list)
    } else {
      roots.push(step)
    }
  }
  const out: StepRow[] = []
  const seen = new Set<number>()
  function visit(step: StepResult, depth: number, subScriptName?: string): void {
    if (seen.has(step.id)) return
    seen.add(step.id)
    out.push({ ...step, depth, subScriptName })
    const ctx = stepSubScriptContext(step)
    for (const child of byParent.get(step.step_id) ?? []) {
      visit(child, depth + 1, stepSubScriptContext(child)?.scriptName ?? ctx?.scriptName)
    }
  }
  for (const root of roots) visit(root, 0)
  for (const step of props.steps) {
    if (!seen.has(step.id)) visit(step, 1, stepSubScriptContext(step)?.scriptName)
  }
  return out
})

const outcomeMap = computed(
  () => new Map(props.steps.map((step) => [step.id, stepConditionOutcome(step)] as const)),
)

const waitMap = computed(
  () => new Map(props.steps.map((step) => [step.id, stepWaitMs(step)] as const)),
)

// 节点证据：失败 logcat / 域名抓包 / 截图都按 step_result_id 关联到节点。
const artifactsByStep = computed(() => {
  const map = new Map<number, Artifact[]>()
  for (const artifact of props.artifacts ?? []) {
    if (artifact.step_result_id === null || artifact.step_result_id === undefined) continue
    const list = map.get(artifact.step_result_id) ?? []
    list.push(artifact)
    map.set(artifact.step_result_id, list)
  }
  return map
})

function evidenceFor(step: StepResult): Artifact[] {
  return artifactsByStep.value.get(step.id) ?? []
}

function hrefOf(artifact: Artifact): string | null {
  return artifactHref(artifact)
}

function nameOf(artifact: Artifact): string {
  return artifact.name || artifact.path.split(/[\\/]/).pop() || String(artifact.id)
}

function outcomeFor(step: StepResult) {
  return outcomeMap.value.get(step.id) ?? null
}

function waitFor(step: StepResult): number | null {
  return waitMap.value.get(step.id) ?? null
}

function rowClassName({ row }: { row: StepRow }): string {
  if (row.status === 'failed' || row.status === 'error') return 'step-row-failed'
  if (row.step_id === props.currentStepId) return 'step-row-current'
  return ''
}
</script>

<template>
  <el-table
    :data="rows"
    row-key="id"
    class="steps-table"
    :row-class-name="rowClassName"
    scrollbar-always-on
  >
    <el-table-column type="expand">
      <template #default="{ row }">
        <div style="padding: 4px 12px 10px">
          <template v-if="evidenceFor(row).length">
            <div class="hint" style="margin-bottom: 4px">节点证据</div>
            <div class="step-evidence">
              <template v-for="artifact in evidenceFor(row)" :key="artifact.id">
                <a
                  v-if="hrefOf(artifact)"
                  class="step-evidence-item"
                  :href="hrefOf(artifact) ?? undefined"
                  target="_blank"
                  rel="noreferrer"
                >
                  <StatusTag :tone="artifactKindMeta(artifact.kind).tone">
                    {{ artifactKindMeta(artifact.kind).label }}
                  </StatusTag>
                  <span>{{ nameOf(artifact) }}</span>
                </a>
                <span v-else class="step-evidence-item">
                  <StatusTag :tone="artifactKindMeta(artifact.kind).tone">
                    {{ artifactKindMeta(artifact.kind).label }}
                  </StatusTag>
                  <span>{{ nameOf(artifact) }}</span>
                  <span class="muted small">无直链</span>
                </span>
              </template>
            </div>
          </template>
          <div class="hint" style="margin-bottom: 4px; margin-top: 8px">节点输出 output</div>
          <pre class="pre-block">{{ row.output ? prettyJson(row.output) : '（无 output）' }}</pre>
        </div>
      </template>
    </el-table-column>
    <el-table-column label="#" width="50">
      <template #default="{ $index }">{{ $index + 1 }}</template>
    </el-table-column>
    <el-table-column label="节点" min-width="200">
      <template #default="{ row }">
        <div class="cell-main" :style="{ paddingLeft: `${row.depth * 16}px` }">
          <span v-if="row.depth" class="sub-script-branch" aria-hidden="true">↳</span>
          {{ row.step_name || row.step_id }}
          <StatusTag v-if="row.depth" tone="blue" style="margin-left: 4px">
            {{ row.subScriptName || '子脚本' }}
          </StatusTag>
          <StatusTag v-if="row.step_id === currentStepId" tone="blue" style="margin-left: 4px">当前</StatusTag>
        </div>
        <div class="cell-sub" :style="{ paddingLeft: `${row.depth * 16}px` }">
          {{ row.step_id }} · {{ row.step_type }}
        </div>
      </template>
    </el-table-column>
    <el-table-column label="状态" width="88">
      <template #default="{ row }">
        <StatusTag :tone="stepStatusMeta(row.status).tone">{{ stepStatusMeta(row.status).label }}</StatusTag>
      </template>
    </el-table-column>
    <el-table-column label="条件 / 等待" width="200">
      <template #default="{ row }">
        <template v-if="outcomeFor(row)">
          <el-tooltip :content="outcomeFor(row)?.detail || outcomeFor(row)?.label">
            <StatusTag :tone="outcomeFor(row)?.tone ?? 'gray'">{{ outcomeFor(row)?.label }}</StatusTag>
          </el-tooltip>
          <div v-if="outcomeFor(row)?.detail" class="cell-sub" style="max-width: 180px">
            {{ outcomeFor(row)?.detail }}
          </div>
          <div v-if="waitFor(row) !== null" class="cell-sub">动作等待 {{ formatDuration(waitFor(row)) }}</div>
        </template>
        <StatusTag v-else-if="waitFor(row) !== null" tone="cyan">
          等待 {{ formatDuration(waitFor(row)) }}
        </StatusTag>
        <span v-else class="muted small">—</span>
      </template>
    </el-table-column>
    <el-table-column label="尝试" width="56">
      <template #default="{ row }">{{ row.attempts ?? 1 }}</template>
    </el-table-column>
    <el-table-column label="耗时" width="82">
      <template #default="{ row }"><span class="small">{{ formatDuration(row.duration_ms) }}</span></template>
    </el-table-column>
    <el-table-column label="错误" min-width="150">
      <template #default="{ row }">
        <el-tooltip v-if="row.error?.message" :content="`${row.error.code ?? ''} ${row.error.message}`">
          <span class="small" style="color: var(--red)">{{ row.error.message }}</span>
        </el-tooltip>
        <span v-else class="muted small">—</span>
      </template>
    </el-table-column>
    <template #empty><el-empty description="暂无步骤结果" :image-size="56" /></template>
  </el-table>
</template>

<style scoped>
.sub-script-branch {
  margin-right: 4px;
  color: var(--muted-2);
}
</style>
