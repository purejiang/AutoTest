<script setup lang="ts">
import { computed } from 'vue'

import type { StepResult } from '@/types/api'
import { formatDuration, prettyJson, stepConditionOutcome, stepStatusMeta, stepWaitMs } from '@/lib/format'
import StatusTag from '@/components/StatusTag.vue'

const props = defineProps<{
  steps: StepResult[]
  currentStepId?: string | null
}>()

const outcomeMap = computed(
  () => new Map(props.steps.map((step) => [step.id, stepConditionOutcome(step)] as const)),
)

const waitMap = computed(
  () => new Map(props.steps.map((step) => [step.id, stepWaitMs(step)] as const)),
)

function outcomeFor(step: StepResult) {
  return outcomeMap.value.get(step.id) ?? null
}

function waitFor(step: StepResult): number | null {
  return waitMap.value.get(step.id) ?? null
}

function rowClassName({ row }: { row: StepResult }): string {
  if (row.status === 'failed' || row.status === 'error') return 'step-row-failed'
  if (row.step_id === props.currentStepId) return 'step-row-current'
  return ''
}
</script>

<template>
  <el-table
    :data="steps"
    row-key="id"
    class="steps-table"
    :row-class-name="rowClassName"
    scrollbar-always-on
  >
    <el-table-column type="expand">
      <template #default="{ row }">
        <div style="padding: 4px 12px 10px">
          <div class="hint" style="margin-bottom: 4px">节点输出 output</div>
          <pre class="pre-block">{{ row.output ? prettyJson(row.output) : '（无 output）' }}</pre>
        </div>
      </template>
    </el-table-column>
    <el-table-column label="#" width="50">
      <template #default="{ $index }">{{ $index + 1 }}</template>
    </el-table-column>
    <el-table-column label="节点" min-width="200">
      <template #default="{ row }">
        <div class="cell-main">
          {{ row.step_name || row.step_id }}
          <StatusTag v-if="row.step_id === currentStepId" tone="blue" style="margin-left: 4px">当前</StatusTag>
        </div>
        <div class="cell-sub">{{ row.step_id }} · {{ row.step_type }}</div>
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
