<script setup lang="ts">
import { ArrowLeft, Document, Refresh } from '@element-plus/icons-vue'
import { useQueryClient } from '@tanstack/vue-query'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { describeApiError } from '@/api/client'
import { runKeys, useRun, useRunEvents } from '@/api/runs'
import { useScript } from '@/api/scripts'
import { useRunEventsWs } from '@/api/useRunEventsWs'
import {
  formatDateTime,
  formatDuration,
  isTerminalRunStatus,
  runDurationMs,
  runProgress,
  runStatusMeta,
} from '@/lib/format'
import { useUiStore } from '@/stores/ui'
import type { RunEvent } from '@/types/api'
import AppTabs from '@/components/AppTabs.vue'
import type { AppTabItem } from '@/components/appTabs'
import PageHead from '@/components/PageHead.vue'
import StatusTag from '@/components/StatusTag.vue'

import RunArtifactsCard from './RunArtifactsCard.vue'
import RunControls from './RunControls.vue'
import RunEventsPanel from './RunEventsPanel.vue'
import RunReportsCard from './RunReportsCard.vue'
import RunStepsTable from './RunStepsTable.vue'
import './runs.css'
import Notice from '@/components/Notice.vue'

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

/** 本次运行变量：脚本默认 + 覆盖（覆盖项高亮，方便回查这次用的包名/账号）。 */
const runVariables = computed<Array<{ name: string; value: string; override: boolean }>>(() => {
  const data = run.value
  if (!data) return []
  const overrides = data.variables ?? {}
  const effective = data.effective_variables ?? overrides
  return Object.entries(effective).map(([name, value]) => ({ name, value, override: name in overrides }))
})
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

const runTabs = computed<AppTabItem[]>(() => [
  { key: 'steps', label: '步骤进度' },
  { key: 'events', label: '事件流', count: visibleEvents.value.length },
])

function setRunTab(key: string): void {
  if (key === 'steps' || key === 'events') activeTab.value = key
}


const totalSteps = computed(() => run.value?.total_steps || run.value?.steps?.length || 0)
const progress = computed(() =>
  runProgress({
    status: liveStatus.value,
    step_index: run.value?.step_index,
    total_steps: totalSteps.value,
  }),
)
const currentIndex = computed(() => progress.value.current)
const percent = computed(() => progress.value.percent)
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
    <PageHead :title="'运行 #' + run.id" @back="router.push('/scripts?tab=runs')">
      <template #back><el-icon><ArrowLeft /></el-icon></template>
      <template #title-extra>
        <StatusTag :tone="statusMeta.tone">{{ statusMeta.label }}</StatusTag>
        <StatusTag v-if="run.debug?.step_mode" tone="violet">单步调试</StatusTag>
      </template>
      <template #below-title>
        <div class="run-meta">
          <button type="button" class="cell-link" @click="run.script_id && router.push(`/scripts/${run.script_id}`)">
            {{ run.script_name || run.script_id || '—' }}
          </button>
          <span class="mirror-statusline-sep" />
          <span class="code-text">{{ run.device_serial || '—' }}</span>
          <span>开始 {{ formatDateTime(run.started_at) }}</span>
          <span>创建 {{ formatDateTime(run.created_at) }}</span>
        </div>
      </template>
      <template #actions>
        <el-button :icon="Document" @click="reportOpen = true">报告</el-button>
        <el-button :icon="Refresh" :loading="runQuery.isFetching.value" @click="runQuery.refetch()">刷新</el-button>
      </template>
    </PageHead>

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

    <Notice
      v-if="run.error?.message || run.error?.code"
      severity="danger"
      :title="`运行失败${run.error?.code ? `（${run.error.code}）` : ''}`"
    >
      {{ run.error?.message ?? '（无错误详情）' }}
    </Notice>

    <div class="run-grid">
      <div class="stack">
        <RunControls :run="run" :live-status="liveStatus" :script="scriptQuery.data.value" />

        <section class="card">
          <div class="card-head">
            <AppTabs
              :model-value="activeTab"
              :items="runTabs"
              variant="plain"
              aria-label="运行详情视图"
              @update:model-value="setRunTab"
            />
            <span class="hint">
              <span class="dot" :class="ws.connected.value ? 'live' : 'offline'" />
              {{ ws.connected.value ? '实时已连接' : ws.reconnectAttempt.value > 0 ? `重连中（第 ${ws.reconnectAttempt.value} 次）` : '实时未连接' }}
            </span>
          </div>

          <RunStepsTable
            v-if="activeTab === 'steps'"
            :steps="run.steps ?? []"
            :current-step-id="run.current_step_id"
            :artifacts="run.artifacts ?? []"
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
                <tr>
                  <td>变量</td>
                  <td>
                    <div v-if="runVariables.length > 0" class="run-vars">
                      <span
                        v-for="item in runVariables"
                        :key="item.name"
                        class="run-var"
                        :class="{ override: item.override }"
                        :title="item.override ? '本次运行覆盖' : '脚本默认'"
                      >
                        <span class="code-text">{{ item.name }}</span>
                        <span class="run-var-eq">=</span>
                        <span>{{ item.value || '（空）' }}</span>
                      </span>
                    </div>
                    <span v-else class="muted">—</span>
                  </td>
                </tr>
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
