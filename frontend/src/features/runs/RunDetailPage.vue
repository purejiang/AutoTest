<script setup lang="ts">
import { ArrowLeft, Document, Refresh } from '@element-plus/icons-vue'
import { useQueryClient } from '@tanstack/vue-query'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { describeApiError } from '@/api/client'
import { runKeys, useRun, useRunEvents } from '@/api/runs'
import { useScript } from '@/api/scripts'
import { useRunEventsWs } from '@/api/useRunEventsWs'
import { isTerminalRunStatus, formatDateTime, formatDuration, runDurationMs, runStatusMeta } from '@/lib/format'
import { useUiStore } from '@/stores/ui'
import type { RunEvent } from '@/types/api'
import StatusTag from '@/components/StatusTag.vue'

import RunArtifactsCard from './RunArtifactsCard.vue'
import RunControls from './RunControls.vue'
import RunEventsPanel from './RunEventsPanel.vue'
import RunReportsCard from './RunReportsCard.vue'
import RunStepsTable from './RunStepsTable.vue'

const route = useRoute()
const router = useRouter()
const queryClient = useQueryClient()
const ui = useUiStore()

const runId = computed(() => Number(route.params.id))
const validId = computed(() => Number.isFinite(runId.value) && runId.value > 0)
const activeTab = ref<'steps' | 'events'>('steps')
const reportOpen = ref(false)

const runQuery = useRun(computed(() => (validId.value ? runId.value : undefined)))
const scriptQuery = useScript(computed(() => runQuery.data.value?.script_id ?? undefined))
const eventsQuery = useRunEvents(computed(() => (validId.value ? runId.value : undefined)), { limit: 500 })
const ws = useRunEventsWs(computed(() => (validId.value ? runId.value : undefined)))

const run = computed(() => runQuery.data.value)
const liveStatus = computed(() => ws.status.value ?? run.value?.status ?? '')
const statusMeta = computed(() => runStatusMeta(liveStatus.value))

watch(
  () => ws.status.value,
  (status) => {
    if (!validId.value || !status) return
    void queryClient.invalidateQueries({ queryKey: runKeys.detail(runId.value) })
    if (isTerminalRunStatus(status)) {
      void queryClient.invalidateQueries({ queryKey: ['runs', 'events', runId.value] })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    }
  },
)

const clearedSeq = ref(0)
const mergedEvents = computed<RunEvent[]>(() => {
  const merged = new Map<number, RunEvent>()
  for (const event of eventsQuery.data.value?.items ?? []) merged.set(event.seq, event)
  for (const event of ws.events.value) merged.set(event.seq, event)
  return Array.from(merged.values()).sort((a, b) => a.seq - b.seq)
})
const visibleEvents = computed(() =>
  clearedSeq.value > 0 ? mergedEvents.value.filter((event) => event.seq > clearedSeq.value) : mergedEvents.value,
)

const totalSteps = computed(() => run.value?.total_steps || run.value?.steps?.length || 0)
const currentIndex = computed(() => {
  if (!run.value) return 0
  const total = totalSteps.value
  if (total <= 0) return 0
  if (liveStatus.value === 'succeeded') return total
  if (liveStatus.value === 'queued') return 0
  // step_index 是当前节点下标（0-based）；进度按“已处理到第几个节点”展示。
  return Math.min(total, Math.max(0, Number(run.value.step_index ?? 0) + 1))
})
const percent = computed(() =>
  totalSteps.value > 0 ? Math.min(100, Math.round((currentIndex.value / totalSteps.value) * 100)) : 0,
)
const progressTone = computed(() => {
  if (liveStatus.value === 'failed' || liveStatus.value === 'aborted') return 'red'
  if (liveStatus.value === 'succeeded') return 'green'
  if (liveStatus.value === 'paused' || liveStatus.value === 'stopping') return 'amber'
  return ''
})

function clearEvents(): void {
  clearedSeq.value = mergedEvents.value.length > 0 ? mergedEvents.value[mergedEvents.value.length - 1].seq : 0
}
</script>

<template>
  <div v-if="!validId" class="page">
    <el-result icon="warning" title="无效的运行 ID">
      <template #extra><el-button type="primary" @click="router.push('/scripts?tab=runs')">返回运行记录</el-button></template>
    </el-result>
  </div>

  <div v-else-if="runQuery.isLoading.value" class="page">
    <el-skeleton :rows="10" animated />
  </div>

  <div v-else-if="runQuery.isError.value || !run" class="page">
    <el-result icon="warning" title="运行详情加载失败" :sub-title="describeApiError(runQuery.error.value)">
      <template #extra>
        <el-button @click="runQuery.refetch()">重试</el-button>
        <el-button type="primary" @click="router.push('/scripts?tab=runs')">返回运行记录</el-button>
      </template>
    </el-result>
  </div>

  <div v-else class="page">
    <div class="run-top">
      <div class="grow">
        <div class="run-title-line">
          <button type="button" class="icon-btn" title="返回运行记录" @click="router.push('/scripts?tab=runs')">
            <el-icon><ArrowLeft /></el-icon>
          </button>
          <h1>运行 #{{ run.id }}</h1>
          <StatusTag :tone="statusMeta.tone">{{ statusMeta.label }}</StatusTag>
          <StatusTag v-if="run.debug?.step_mode" tone="violet">单步调试</StatusTag>
        </div>
        <div class="run-meta">
          <button type="button" class="cell-link" @click="run.script_id && router.push(`/scripts/${run.script_id}`)">
            {{ run.script_name || run.script_id || '—' }}
          </button>
          <span class="mirror-statusline-sep" />
          <span class="code-text">{{ run.device_serial || '—' }}</span>
          <span>开始 {{ formatDateTime(run.started_at) }}</span>
          <span>创建 {{ formatDateTime(run.created_at) }}</span>
        </div>
      </div>
      <div class="run-actions">
        <el-button :icon="Document" @click="reportOpen = true">报告</el-button>
        <el-button :icon="Refresh" :loading="runQuery.isFetching.value" @click="runQuery.refetch()">刷新</el-button>
      </div>
    </div>

    <div class="card">
      <div class="card-body">
        <div class="run-summary">
          <div class="run-progress-line">
            <span class="hint">进度</span>
            <div class="progress" :class="progressTone"><span :style="{ width: `${percent}%` }" /></div>
            <span class="small">{{ currentIndex }} / {{ totalSteps }} · {{ percent }}%</span>
          </div>
          <div class="run-kv">
            <span>通过 <strong>{{ run.summary?.passed_steps ?? 0 }}</strong></span>
            <span>失败 <strong>{{ run.summary?.failed_steps ?? 0 }}</strong></span>
            <span>跳过 <strong>{{ run.summary?.skipped_steps ?? 0 }}</strong></span>
            <span>耗时 <strong>{{ formatDuration(runDurationMs(run)) }}</strong></span>
            <span>当前 <strong class="code-text">{{ run.current_step_id || '—' }}</strong></span>
          </div>
        </div>
      </div>
    </div>

    <el-alert
      v-if="run.error?.message || run.error?.code"
      type="error"
      show-icon
      :closable="false"
      :title="`运行失败${run.error?.code ? `（${run.error.code}）` : ''}`"
      :description="run.error?.message ?? '（无错误详情）'"
    />

    <div class="run-grid">
      <div class="stack">
        <RunControls :run="run" :live-status="liveStatus" :script="scriptQuery.data.value" />

        <section class="card">
          <div class="card-head">
            <div class="row">
              <button
                type="button"
                class="tab-btn"
                :class="{ active: activeTab === 'steps' }"
                @click="activeTab = 'steps'"
              >
                步骤进度
              </button>
              <button
                type="button"
                class="tab-btn"
                :class="{ active: activeTab === 'events' }"
                @click="activeTab = 'events'"
              >
                事件流
                <span class="tag blue" style="margin-left: 4px">{{ visibleEvents.length }}</span>
              </button>
            </div>
            <span class="hint">
              <span class="dot" :class="ws.connected.value ? 'live' : 'offline'" />
              {{ ws.connected.value ? 'WS 已连接' : ws.reconnectAttempt.value > 0 ? `重连中（第 ${ws.reconnectAttempt.value} 次）` : 'WS 未连接' }}
            </span>
          </div>

          <RunStepsTable
            v-if="activeTab === 'steps'"
            :steps="run.steps ?? []"
            :current-step-id="run.current_step_id"
          />
          <RunEventsPanel
            v-else
            :events="visibleEvents"
            :connected="ws.connected.value"
            :reconnect-attempt="ws.reconnectAttempt.value"
            :auto-scroll="ui.eventsAutoScroll"
            @toggle-auto-scroll="ui.setEventsAutoScroll"
            @clear="clearEvents"
          />
        </section>
      </div>

      <div class="stack">
        <section class="card">
          <div class="card-head"><h2>产物</h2><StatusTag>{{ run.artifacts?.length ?? 0 }}</StatusTag></div>
          <div class="card-body">
            <RunArtifactsCard :artifacts="run.artifacts ?? []" />
          </div>
        </section>
        <section class="card">
          <div class="card-head"><h2>运行信息</h2></div>
          <div class="card-body">
            <table class="mini-table">
              <tbody>
                <tr><td>运行 ID</td><td class="code-text">#{{ run.id }}</td></tr>
                <tr><td>脚本</td><td>{{ run.script_name || run.script_id || '—' }}</td></tr>
                <tr><td>设备</td><td class="code-text">{{ run.device_serial || '—' }}</td></tr>
                <tr><td>状态</td><td>{{ statusMeta.label }}</td></tr>
                <tr><td>调试</td><td>{{ run.debug?.step_mode ? `单步${run.debug?.breakpoints?.length ? ` · 断点 ${run.debug.breakpoints.join(',')}` : ''}` : '否' }}</td></tr>
                <tr><td>开始</td><td>{{ formatDateTime(run.started_at) }}</td></tr>
                <tr><td>结束</td><td>{{ formatDateTime(run.ended_at) }}</td></tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>

    <el-dialog v-model="reportOpen" title="运行报告" width="760px" append-to-body>
      <RunReportsCard :run-id="run.id" />
    </el-dialog>
  </div>
</template>
