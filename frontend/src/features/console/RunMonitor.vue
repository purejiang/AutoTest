<script setup lang="ts">
import { ArrowLeft, CaretRight, CircleClose, Refresh, VideoPause } from '@element-plus/icons-vue'
import { useQueryClient } from '@tanstack/vue-query'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { runKeys, usePauseRun, useResumeRun, useRun, useStopRun } from '@/api/runs'
import { useScript } from '@/api/scripts'
import { useRunEventsWs } from '@/api/useRunEventsWs'
import type { StepResult } from '@/types/api'
import {
  formatDuration,
  isTerminalRunStatus,
  runDurationMs,
  runProgress,
  runStatusMeta,
  stepStatusMeta,
} from '@/lib/format'
import StatusTag from '@/components/StatusTag.vue'

const props = defineProps<{
  runId: number
  scriptId: string | null
}>()

const emit = defineEmits<{
  back: []
  runningChange: [active: boolean]
  statusChange: [status: string]
}>()

const router = useRouter()
const queryClient = useQueryClient()

const runQuery = useRun(computed(() => props.runId))
const run = computed(() => runQuery.data.value ?? null)
const scriptQuery = useScript(computed(() => props.scriptId ?? run.value?.script_id ?? undefined))
const ws = useRunEventsWs(computed(() => props.runId))

const pauseMutation = usePauseRun(computed(() => props.runId))
const resumeMutation = useResumeRun(computed(() => props.runId))
const stopMutation = useStopRun(computed(() => props.runId))

const liveStatus = computed(() => ws.status.value ?? run.value?.status ?? '')
const statusMeta = computed(() => runStatusMeta(liveStatus.value))
const active = computed(() => ['queued', 'running', 'paused', 'stopping'].includes(liveStatus.value))
const isPaused = computed(() => liveStatus.value === 'paused')
const isRunning = computed(() => liveStatus.value === 'running' || liveStatus.value === 'queued')

const now = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  ticker = setInterval(() => {
    now.value = Date.now()
  }, 1000)
})
onBeforeUnmount(() => {
  if (ticker !== null) clearInterval(ticker)
})

watch(
  liveStatus,
  (status) => {
    emit('runningChange', ['queued', 'running', 'paused', 'stopping'].includes(status))
    // 首次加载时 status 为空，保留 AutoPanel 刚创建运行时的 queued 状态，避免徽标闪一下。
    if (status) emit('statusChange', status)
  },
  { immediate: true },
)

watch(
  () => ws.status.value,
  (status) => {
    if (!status) return
    void queryClient.invalidateQueries({ queryKey: runKeys.detail(props.runId) })
    if (isTerminalRunStatus(status)) {
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    }
  },
)

const resultByStep = computed(() => {
  const map = new Map<string, StepResult>()
  for (const step of run.value?.steps ?? []) map.set(step.step_id, step)
  return map
})

interface StepRow {
  id: string
  name: string
  type: string
  index: number
  result: StepResult | null
}

const stepRows = computed<StepRow[]>(() => {
  const data = run.value
  if (!data) return []
  const scriptSteps = scriptQuery.data.value?.data?.steps ?? []
  if (scriptSteps.length > 0) {
    return scriptSteps.map((step, index) => ({
      id: step.id,
      name: step.name || step.id,
      type: step.type,
      index,
      result: resultByStep.value.get(step.id) ?? null,
    }))
  }
  return (data.steps ?? []).map((step, index) => ({
    id: step.step_id,
    name: step.step_name || step.step_id,
    type: step.step_type,
    index: step.step_index ?? index,
    result: step,
  }))
})

const totalSteps = computed(() => run.value?.total_steps || stepRows.value.length || 0)
const progress = computed(() =>
  runProgress({
    status: liveStatus.value,
    step_index: run.value?.step_index,
    total_steps: totalSteps.value,
  }),
)
const currentIndex = computed(() => progress.value.current)
const percent = computed(() => progress.value.percent)

const counts = computed(() => {
  const results = run.value?.steps ?? []
  const passed = results.filter((step) => step.status === 'passed').length
  const failed = results.filter((step) => step.status === 'failed' || step.status === 'error').length
  return { passed, failed }
})

const elapsed = computed(() => {
  const data = run.value
  if (!data) return null
  const fixed = runDurationMs(data)
  if (fixed !== null) return fixed
  if (!data.started_at) return null
  const start = Date.parse(data.started_at)
  return Number.isFinite(start) ? Math.max(0, now.value - start) : null
})

const terminalTitle = computed(() => {
  switch (liveStatus.value) {
    case 'succeeded':
      return '运行完成'
    case 'failed':
      return '运行失败'
    case 'aborted':
      return '运行中止'
    case 'stopped':
      return '运行已停止'
    case 'interrupted':
      return '运行被中断'
    default:
      return '运行结束'
  }
})

function stepStatus(row: StepRow): { label: string; tone: 'gray' | 'blue' | 'green' | 'red' | 'amber' } {
  if (row.result) {
    const meta = stepStatusMeta(row.result.status)
    return { label: meta.label, tone: meta.tone as 'gray' | 'blue' | 'green' | 'red' | 'amber' }
  }
  if (active.value && row.id === run.value?.current_step_id) return { label: '运行中', tone: 'blue' }
  return { label: '等待', tone: 'gray' }
}

function pause(): void {
  pauseMutation.mutate(undefined, { onSuccess: () => ElMessage.success('已请求暂停') })
}

function resume(): void {
  resumeMutation.mutate({ ack: true }, { onSuccess: () => ElMessage.success('已请求继续运行') })
}

async function stop(): Promise<void> {
  try {
    await ElMessageBox.confirm('停止运行？正在执行的动作最多等待 5s 后强制中断。', '停止运行', {
      type: 'warning',
      confirmButtonText: '停止',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  stopMutation.mutate(undefined, { onSuccess: () => ElMessage.success('已请求停止') })
}

function openDetail(): void {
  void router.push(`/runs/${props.runId}`)
}
</script>

<template>
  <div v-if="runQuery.isLoading.value && !run" class="console-run">
    <el-skeleton :rows="6" animated />
  </div>

  <div v-else-if="!run" class="console-run">
    <el-alert type="error" :closable="false" show-icon title="运行信息加载失败" />
    <el-button size="small" style="margin-top: 8px" @click="emit('back')">返回脚本列表</el-button>
  </div>

  <div v-else class="console-run">
    <div class="run-head">
      <div class="run-title">
        <StatusTag :tone="statusMeta.tone">{{ statusMeta.label }}</StatusTag>
        <strong :title="run.script_name || run.script_id || ''">{{
          run.script_name || run.script_id || '—'
        }}</strong>
        <span class="spacer" />
        <span class="mono small muted">{{ elapsed !== null ? formatDuration(elapsed) : '—' }}</span>
      </div>
      <div class="progress" :class="{ green: liveStatus === 'succeeded', red: liveStatus === 'failed' }">
        <span :style="{ width: `${percent}%` }" />
      </div>
      <div class="run-meta">
        <span class="run-device" :title="run.device_serial || ''">
          设备 <b>{{ run.device_serial || '—' }}</b>
        </span>
        <span>{{ currentIndex }} / {{ totalSteps }}</span>
        <span
          >通过 <b>{{ counts.passed }}</b></span
        >
        <span
          >失败 <b>{{ counts.failed }}</b></span
        >
        <span
          >耗时 <b>{{ elapsed !== null ? formatDuration(elapsed) : '—' }}</b></span
        >
      </div>
    </div>

    <div class="run-actions">
      <el-button
        v-if="isRunning"
        size="small"
        :icon="VideoPause"
        :loading="pauseMutation.isPending.value"
        @click="pause"
      >
        暂停
      </el-button>
      <el-button
        v-if="isPaused"
        size="small"
        type="primary"
        :icon="CaretRight"
        :loading="resumeMutation.isPending.value"
        @click="resume"
      >
        继续
      </el-button>
      <el-button
        v-if="active"
        size="small"
        type="danger"
        :icon="CircleClose"
        :loading="stopMutation.isPending.value"
        @click="stop"
      >
        停止
      </el-button>
      <el-button size="small" :icon="Refresh" :loading="runQuery.isFetching.value" @click="runQuery.refetch()"
        >刷新</el-button
      >
      <el-button size="small" @click="openDetail">详情</el-button>
    </div>

    <div class="run-nodes">
      <div
        v-for="row in stepRows"
        :key="row.id"
        :class="['console-node', { current: row.id === run.current_step_id && active }]"
      >
        <span class="node-index">{{ row.index + 1 }}</span>
        <span class="node-body">
          <span class="node-name">{{ row.name }}</span>
          <span class="node-sub">
            <span class="mono">{{ row.type }}</span>
            <span v-if="row.result?.duration_ms" class="mono">{{
              formatDuration(row.result.duration_ms)
            }}</span>
          </span>
          <span v-if="row.result?.error?.message" class="node-error">{{ row.result.error.message }}</span>
        </span>
        <StatusTag class="node-status" :tone="stepStatus(row).tone">{{ stepStatus(row).label }}</StatusTag>
      </div>
      <el-empty v-if="stepRows.length === 0" description="等待步骤数据…" :image-size="48" />
    </div>

    <div v-if="!active" class="run-terminal">
      <strong>{{ terminalTitle }}</strong>
      <div class="terminal-meta">
        <span
          >通过 <b>{{ counts.passed }}</b></span
        >
        <span
          >失败 <b>{{ counts.failed }}</b></span
        >
        <span
          >耗时 <b>{{ elapsed !== null ? formatDuration(elapsed) : '—' }}</b></span
        >
      </div>
      <div class="hint" style="margin-top: 4px">
        <span class="dot" :class="ws.connected.value ? 'live' : 'offline'" />
        {{ ws.connected.value ? '实时连接已连接' : '实时连接已断开' }}
      </div>
    </div>

    <button type="button" class="back-to-list" @click="emit('back')">
      <el-icon><ArrowLeft /></el-icon>返回脚本列表
    </button>
  </div>
</template>

<style scoped>
.console-run {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
  padding: 12px;
}

.run-head {
  flex: none;
}

.run-title {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}

.run-title strong {
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  font-size: 12.5px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-title .mono {
  flex: none;
  color: var(--muted);
  font-size: 10.5px;
}

.console-run .progress {
  height: 5px;
  margin: 9px 0 7px;
  overflow: hidden;
  border-radius: 3px;
  background: var(--surface-3);
}

.console-run .progress > span {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: var(--accent);
  transition: width 0.3s ease;
}

.console-run .progress.green > span {
  background: var(--green);
}

.console-run .progress.red > span {
  background: var(--red);
}

.console-run .run-meta {
  display: flex;
  align-items: center;
  gap: 11px;
  flex-wrap: wrap;
  margin: 0;
  color: var(--muted);
  font-size: 10.5px;
}

.console-run .run-meta b {
  color: var(--ink);
  font-weight: 650;
}

.run-device {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
}

.run-device b {
  max-width: 180px;
  overflow: hidden;
  font-family: var(--mono);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.console-run .run-actions {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
  margin: 10px 0 8px;
}

.run-nodes {
  flex: 1;
  min-height: 0;
  margin: 0 -4px;
  padding: 0 4px;
  overflow-y: auto;
}

.console-node {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  padding: 7px 5px;
  border-bottom: 1px solid var(--line-soft);
}

.console-node:last-child {
  border-bottom: 0;
}

.console-node.current {
  border-radius: 5px;
  background: var(--accent-soft);
}

.node-index {
  flex: none;
  width: 18px;
  padding-top: 2px;
  color: var(--muted-2);
  font-family: var(--mono);
  font-size: 9.5px;
  text-align: right;
}

.node-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.node-name {
  overflow: hidden;
  color: var(--ink);
  font-size: 11.5px;
  font-weight: 550;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-sub {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 1px;
  color: var(--muted-2);
  font-size: 9.5px;
}

.node-sub .mono {
  font-size: 9.5px;
}

.node-status {
  flex: none;
  margin-top: 1px;
}

.node-error {
  margin-top: 2px;
  overflow: hidden;
  color: var(--red);
  font-size: 9.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-terminal {
  flex: none;
  margin-top: 8px;
  padding: 9px 10px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface-2);
}

.run-terminal strong {
  color: var(--ink);
  font-size: 11.5px;
}

.terminal-meta {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 4px;
  color: var(--muted);
  font-size: 10.5px;
}

.terminal-meta b {
  color: var(--ink);
  font-weight: 650;
}

.back-to-list {
  display: inline-flex;
  align-items: center;
  align-self: flex-start;
  gap: 5px;
  flex: none;
  height: 26px;
  margin-top: 8px;
  padding: 0 9px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface);
  color: var(--muted);
  font-size: 11.5px;
  cursor: pointer;
}

.back-to-list:hover {
  background: var(--surface-2);
  color: var(--ink);
}

@media (max-width: 640px) {
  .console-run {
    padding: 9px;
  }

  .run-device b {
    max-width: 120px;
  }

  .console-run .run-meta {
    gap: 8px;
  }
}
</style>
