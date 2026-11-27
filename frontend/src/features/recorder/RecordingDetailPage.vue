<script setup lang="ts">
import {
  ArrowLeft,
  Delete,
  Document,
  SwitchButton,
  VideoPause,
  VideoPlay,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox, type TableInstance } from 'element-plus'
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { describeApiError } from '@/api/client'
import {
  useBulkDeleteRecordingEvents,
  useConvertRecording,
  useDeleteRecording,
  useDeleteRecordingEvent,
  usePauseRecording,
  useRecording,
  useRecordingEvents,
  useResumeRecording,
  useStopRecording,
} from '@/api/recordings'
import { useRecordingEventsWs } from '@/api/useRecordingEventsWs'
import type { RecordingEvent } from '@/types/api'
import {
  confidenceMeta,
  formatDateTime,
  formatDuration,
  recordingModeMeta,
  recordingStatusMeta,
} from '@/lib/format'
import { notifyBulkDeleteResult } from '@/lib/bulk'
import PageHead from '@/components/PageHead.vue'
import StatusTag from '@/components/StatusTag.vue'
import Notice from '@/components/Notice.vue'

const MAX_EVENTS = 1000

const route = useRoute()
const router = useRouter()
const id = computed(() => String(route.params.id ?? ''))

const recordingQuery = useRecording(id)
const recording = computed(() => recordingQuery.data.value)
const isLive = computed(
  () => recording.value?.status === 'recording' || recording.value?.status === 'paused',
)
const isRecording = computed(() => recording.value?.status === 'recording')
const isPaused = computed(() => recording.value?.status === 'paused')
const isStopped = computed(() => recording.value?.status === 'stopped')

// WS 实时事件：只有录制中/暂停中才连接；断线重连由 composable 负责，失败时由下面的 REST 轮询兜底。
const recordingWs = useRecordingEventsWs(id, { enabled: isLive, maxEvents: MAX_EVENTS })
const eventsQuery = useRecordingEvents(id, {
  limit: MAX_EVENTS,
  // 仅在 active && WS 未连通时回退 4s 轮询；WS 正常或进入终态后关闭轮询。
  refetchInterval: computed(() => (isLive.value && !recordingWs.connected.value ? 4000 : false)),
})
const pauseMutation = usePauseRecording(id)
const resumeMutation = useResumeRecording(id)
const stopMutation = useStopRecording(id)
const deleteEventMutation = useDeleteRecordingEvent(id)
const bulkDeleteEventsMutation = useBulkDeleteRecordingEvents(id)
const convertMutation = useConvertRecording(id)
const deleteRecordingMutation = useDeleteRecording()
const eventsTableRef = ref<TableInstance>()
const selectedEvents = ref<RecordingEvent[]>([])

/** 录制中通过 WS 删除事件时，后端已删除的 seq 不再从 WS 缓冲区复活。 */
const suppressedSeqs = ref<Set<number>>(new Set())

const restEvents = computed(() => eventsQuery.data.value?.items ?? [])
const events = computed<RecordingEvent[]>(() => {
  const merged = new Map<number, RecordingEvent>()
  for (const event of restEvents.value) merged.set(event.seq, event)
  for (const event of recordingWs.events.value) merged.set(event.seq, event)
  const ordered = [...merged.values()]
    .filter((event) => !suppressedSeqs.value.has(event.seq))
    .sort((a, b) => a.seq - b.seq)
  return ordered.length > MAX_EVENTS ? ordered.slice(ordered.length - MAX_EVENTS) : ordered
})
/** REST event_count 是权威值；WS/内存事件只用于实时增长时兜底。 */
const eventCount = computed(() => Math.max(recording.value?.event_count ?? 0, events.value.length))
const statusMeta = computed(() => recordingStatusMeta(recording.value?.status))
const modeMeta = computed(() => recordingModeMeta(recording.value?.mode))
const hasScript = computed(() => Boolean(recording.value?.script_id))
const convertLabel = computed(() => (hasScript.value ? '重新生成' : '转脚本'))
const isPhysical = computed(() => recording.value?.mode === 'physical')

const captureError = computed(() => {
  const value = recording.value?.meta?.capture_error
  return typeof value === 'string' ? value.trim() : ''
})

const touchDeviceText = computed(() => {
  if (!isPhysical.value) return ''
  const meta = recording.value?.meta ?? {}
  const name = typeof meta.touch_device_name === 'string' ? meta.touch_device_name : ''
  const path = typeof meta.touch_device === 'string' ? meta.touch_device : ''
  const screenValue = Array.isArray(meta.screen) ? meta.screen : []
  const screen =
    screenValue.length >= 2 && screenValue[0] != null && screenValue[1] != null
      ? `${String(screenValue[0])}×${String(screenValue[1])}`
      : ''
  const device = name && path ? `${name}（${path}）` : name || path
  return [device, screen].filter(Boolean).join(' · ')
})

const eventsEmptyDescription = computed(() =>
  isPhysical.value
    ? '暂无事件：在手机上操作后事件会实时出现在这里；若仍为 0，请查看上方采集信息/错误。'
    : '暂无事件：去控制台在镜像上操作并开启录制。',
)

const liveHint = computed(() => {
  if (isPaused.value) return '已暂停，事件不会追加'
  if (!recordingWs.connected.value) {
    return recordingWs.reconnectAttempt.value > 0
      ? `实时连接中断，正在第 ${recordingWs.reconnectAttempt.value} 次重连（暂时每 4 秒刷新）`
      : '实时连接建立中（暂时每 4 秒刷新）'
  }
  return eventCount.value > 0 ? '' : '等待第 1 个事件…'
})

const convertOpen = ref(false)
const convertName = ref('')
const convertTargetMode = ref<'point' | 'element'>('point')
const autoConvertHandled = ref(false)

const KIND_META: Record<string, { label: string; tone: 'blue' | 'violet' | 'cyan' | 'amber' | 'gray' }> = {
  tap: { label: '点击 tap', tone: 'blue' },
  long_press: { label: '长按 long_press', tone: 'violet' },
  swipe: { label: '滑动 swipe', tone: 'cyan' },
  drag: { label: '拖拽 drag', tone: 'violet' },
  input: { label: '输入 input', tone: 'amber' },
  key: { label: '按键 key', tone: 'amber' },
  app: { label: '应用 app', tone: 'violet' },
}

function kindMeta(kind: string): { label: string; tone: 'blue' | 'violet' | 'cyan' | 'amber' | 'gray' } {
  return KIND_META[kind] ?? { label: kind, tone: 'gray' }
}

function rowClassName({ row }: { row: RecordingEvent }): string {
  return row.confidence === 'low' ? 'recording-event-low' : ''
}

function targetText(event: RecordingEvent): string {
  const target = event.payload.node ?? null
  if (!target) return '—'
  if (target.by === 'point') return `point (${target.x ?? '?'}, ${target.y ?? '?'})`
  return `${target.by} = ${target.value ?? ''}`
}

function formatPoint(point: { x?: number; y?: number } | undefined): string {
  if (!point) return '—'
  return `(${point.x ?? '?'}, ${point.y ?? '?'})`
}

function coordText(event: RecordingEvent): string {
  const payload = event.payload
  if (payload.from || payload.to) return `${formatPoint(payload.from)} → ${formatPoint(payload.to)}`
  if (payload.x !== undefined || payload.y !== undefined) return `${payload.x ?? '?'}, ${payload.y ?? '?'}`
  return '—'
}

function locationText(event: RecordingEvent): string {
  const target = targetText(event)
  return target !== '—' ? target : coordText(event)
}

function durationText(event: RecordingEvent): string | null {
  const duration = event.payload.duration_ms
  return typeof duration === 'number' ? formatDuration(duration) : null
}

function handleEventSelectionChange(rows: RecordingEvent[]): void {
  selectedEvents.value = rows
}

function clearEventSelection(): void {
  eventsTableRef.value?.clearSelection()
  selectedEvents.value = []
}

function suppressSeqs(seqs: number[]): void {
  const next = new Set(suppressedSeqs.value)
  for (const seq of seqs) next.add(seq)
  suppressedSeqs.value = next
}

async function scrollEventsToBottom(): Promise<void> {
  await nextTick()
  eventsTableRef.value?.setScrollTop(Number.MAX_SAFE_INTEGER)
}

async function refreshAll(): Promise<void> {
  clearEventSelection()
  await Promise.all([recordingQuery.refetch(), eventsQuery.refetch()])
}

async function bulkDeleteEvents(): Promise<void> {
  const seqs = selectedEvents.value.map((event) => event.seq)
  if (seqs.length === 0) return
  try {
    await ElMessageBox.confirm(
      `删除选中的 ${seqs.length} 个事件？删除后转脚本时不会包含这些事件；单条失败不会阻塞其他条。`,
      '批量删除事件',
      { type: 'warning', confirmButtonText: '删除选中', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const result = await bulkDeleteEventsMutation.mutateAsync(seqs)
    notifyBulkDeleteResult(result, '事件')
    const failed = new Set((Array.isArray(result.failed) ? result.failed : []).map((item) => Number(item.id)))
    suppressSeqs(seqs.filter((seq) => !failed.has(seq)))
    clearEventSelection()
    await Promise.all([eventsQuery.refetch(), recordingQuery.refetch()])
  } catch {
    // 全局错误提示
  }
}

function openConvert(): void {
  convertName.value = `${recording.value?.name || '录制'} 脚本`
  convertTargetMode.value = 'point'
  convertOpen.value = true
}

async function primaryConvert(): Promise<void> {
  if (hasScript.value) {
    try {
      await ElMessageBox.confirm(
        '该录制已关联脚本。重新生成会基于当前事件创建一个新的关联脚本；之前的脚本不会被自动删除，但录制将指向新脚本。已删除的事件不会参与生成。',
        '重新生成脚本',
        { type: 'warning', confirmButtonText: '重新生成', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }
  openConvert()
}

async function handleConvert(): Promise<void> {
  try {
    const result = await convertMutation.mutateAsync({
      name: convertName.value.trim() || undefined,
      target_mode: convertTargetMode.value,
    })
    const warnings = Array.isArray(result.warnings) ? result.warnings : []
    if (warnings.length > 0) {
      const first = warnings[0]
      const text =
        typeof first === 'string'
          ? first
          : first && typeof first === 'object'
            ? `${first.reason ?? '低置信度事件'}${first.seq !== undefined ? `（#${first.seq}）` : ''}`
            : String(first)
      ElMessage.warning(`已生成脚本，但有 ${warnings.length} 条提示：${text}`)
    }
    const scriptId = result.script?.id ?? result.script_id ?? result.id
    convertOpen.value = false
    if (scriptId) {
      ElMessage.success('已生成脚本，正在打开编辑器')
      await router.push(`/scripts/${scriptId}`)
    } else {
      ElMessage.success('已生成脚本，请到脚本列表查看')
      await router.push('/scripts')
    }
  } catch {
    // 全局错误提示
  }
}

function openScript(): void {
  if (recording.value?.script_id) void router.push(`/scripts/${recording.value.script_id}`)
}

async function pauseRecording(): Promise<void> {
  pauseMutation.mutate(undefined, { onSuccess: () => ElMessage.success('录制已暂停') })
}

function resumeRecording(): void {
  resumeMutation.mutate(undefined, { onSuccess: () => ElMessage.success('录制已继续') })
}

async function stopRecording(): Promise<void> {
  try {
    await ElMessageBox.confirm('停止后不能再追加事件，但可以转脚本。', '停止录制', {
      type: 'warning',
      confirmButtonText: '停止',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  stopMutation.mutate(undefined, {
    async onSuccess() {
      ElMessage.success('录制已停止')
      await recordingQuery.refetch()
      await eventsQuery.refetch()
    },
  })
}

async function removeEvent(seq: number): Promise<void> {
  try {
    await ElMessageBox.confirm(`删除事件 #${seq}？删除后转脚本时不会包含该事件。`, '删除事件', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  deleteEventMutation.mutate(seq, {
    onSuccess: () => {
      ElMessage.success(`已删除事件 #${seq}`)
      suppressSeqs([seq])
      clearEventSelection()
      void eventsQuery.refetch()
      void recordingQuery.refetch()
    },
  })
}

async function removeRecording(): Promise<void> {
  if (!recording.value) return
  try {
    await ElMessageBox.confirm('录制与其事件会被永久删除。', '删除录制', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  deleteRecordingMutation.mutate(recording.value.id, {
    onSuccess: () => {
      ElMessage.success('录制已删除')
      void router.push('/recorder')
    },
  })
}

function openConvertFromQuery(): void {
  if (autoConvertHandled.value) return
  if (route.query.action !== 'to_script') return
  if (!recording.value) return
  autoConvertHandled.value = true
  openConvert()
  const next = { ...route.query }
  delete next.action
  void router.replace({ query: next })
}

watch(
  () => [recording.value?.id, route.query.action] as const,
  () => openConvertFromQuery(),
  { immediate: true },
)

// WS state 状态变化（尤其是 stopped）时刷新 recording，保持摘要/按钮状态一致。
watch(
  () => recordingWs.status.value,
  (next, previous) => {
    if (!next || next === previous) return
    if (next !== recording.value?.status) void recordingQuery.refetch()
    if (next === 'stopped') void eventsQuery.refetch()
  },
)

// 外部停止（非本页点停止）时补一次事件请求。
watch(
  () => recording.value?.status,
  (next, previous) => {
    if (next === 'stopped' && previous && previous !== 'stopped') void eventsQuery.refetch()
  },
)

watch(id, () => {
  suppressedSeqs.value = new Set()
})

// 实时追加时事件表自动滚到底；首帧/首次加载也贴底。
watch(
  () => [events.value.length, isLive.value] as const,
  ([length, live], [previousLength, previousLive]) => {
    if (!live) return
    if (length > previousLength || (!previousLive && length > 0)) void scrollEventsToBottom()
  },
)
</script>

<template>
  <div v-if="recordingQuery.isLoading.value" class="page">
    <el-skeleton :rows="10" animated />
  </div>

  <div v-else-if="recordingQuery.isError.value || !recording" class="page">
    <el-result icon="warning" title="录制加载失败" :sub-title="describeApiError(recordingQuery.error.value)">
      <template #extra>
        <el-button @click="refreshAll">重试</el-button>
        <el-button type="primary" @click="router.push('/recorder')">返回录制草稿</el-button>
      </template>
    </el-result>
  </div>

  <div v-else class="page">
    <PageHead
      :title="recording.name || recording.id"
      :subtitle="`${recording.id} · ${recording.serial || '—'}`"
      @back="router.push('/recorder')"
    >
      <template #back><el-icon><ArrowLeft /></el-icon></template>
      <template #title-extra>
        <StatusTag :tone="statusMeta.tone">{{ statusMeta.label }}</StatusTag>
        <StatusTag :tone="modeMeta.tone">{{ modeMeta.label }}</StatusTag>
      </template>
      <template #actions>
        <el-button v-if="isRecording" :icon="VideoPause" :loading="pauseMutation.isPending.value" @click="pauseRecording">
          暂停
        </el-button>
        <el-button v-if="isPaused" :icon="VideoPlay" :loading="resumeMutation.isPending.value" @click="resumeRecording">
          继续
        </el-button>
        <el-button
          v-if="isLive"
          type="danger"
          :icon="SwitchButton"
          :loading="stopMutation.isPending.value"
          @click="stopRecording"
        >
          停止
        </el-button>
        <el-button v-if="isStopped && recording.script_id" type="primary" :icon="Document" @click="openScript">
          打开脚本
        </el-button>
        <el-button
          v-if="isStopped"
          :type="recording.script_id ? 'default' : 'primary'"
          :icon="Document"
          @click="primaryConvert"
        >
          {{ convertLabel }}
        </el-button>
        <el-button v-if="isStopped" type="danger" plain :icon="Delete" @click="removeRecording">删除</el-button>
      </template>
    </PageHead>

    <div v-if="isLive" class="recording-livebar">
      <span :class="['dot', { 'live-pulse': isRecording }]" />
      <StatusTag :tone="statusMeta.tone">{{ statusMeta.label }}</StatusTag>
      <span class="hint">已记录 <b>{{ eventCount }}</b> 个事件</span>
      <span class="spacer" />
      <span v-if="liveHint" class="hint">{{ liveHint }}</span>
      <StatusTag :tone="recordingWs.connected.value ? 'green' : 'amber'" dot>
        {{ recordingWs.connected.value ? '实时' : '轮询' }}
      </StatusTag>
    </div>

    <Notice v-if="captureError" severity="danger" title="触摸采集中断">
      <div class="row wrap">
        <span>{{ captureError }}</span>
        <span class="hint">
          {{ isLive ? '请使用页头「停止」后重新创建。' : '该录制可能不完整，建议重新创建。' }}
        </span>
      </div>
    </Notice>

    <section class="card">
      <div class="card-head">
        <h2>录制摘要</h2>
        <span class="hint">停止后可转脚本</span>
      </div>
      <div class="card-body">
        <div class="recording-summary">
          <div class="kv">
            <span>事件数</span>
            <strong>{{ eventCount }}</strong>
          </div>
          <div class="kv">
            <span>开始时间</span>
            <strong>{{ formatDateTime(recording.started_at) }}</strong>
          </div>
          <div class="kv">
            <span>结束时间</span>
            <strong>{{ formatDateTime(recording.ended_at) }}</strong>
          </div>
          <div class="kv">
            <span>关联脚本</span>
            <strong>
              <span v-if="recording.script_id">{{ recording.script_name || recording.script_id }}</span>
              <span v-else class="muted">尚未生成</span>
            </strong>
          </div>
          <div v-if="touchDeviceText" class="kv kv-wide">
            <span>采集设备</span>
            <strong class="code-text" :title="touchDeviceText">{{ touchDeviceText }}</strong>
          </div>
        </div>
      </div>
    </section>

    <section class="card">
      <div class="card-head">
        <h2>
          录制事件
          <span class="tag blue" style="margin-left: 4px">{{ eventCount }}</span>
        </h2>
        <span class="hint">
          按录制顺序 · 低置信度事件高亮 · 删除后不参与转脚本 · 最多保留最近 {{ MAX_EVENTS }} 条
        </span>
      </div>

      <div v-if="selectedEvents.length > 0" class="batch-bar">
        <strong>已选 {{ selectedEvents.length }} 个事件</strong>
        <span class="small">删除后转脚本不包含这些事件</span>
        <span class="spacer" />
        <el-button
          size="small"
          type="danger"
          :loading="bulkDeleteEventsMutation.isPending.value"
          @click="bulkDeleteEvents"
        >
          删除选中
        </el-button>
        <el-button size="small" @click="clearEventSelection">取消</el-button>
      </div>

      <Notice
        v-if="eventsQuery.isError.value && !recordingWs.connected.value"
        severity="danger"
        title="事件加载失败"
        style="margin: 8px"
      >
        <div class="row wrap">
          <span>{{ describeApiError(eventsQuery.error.value) }}</span>
          <el-button size="small" @click="refreshAll">重试</el-button>
        </div>
      </Notice>

      <el-table
        ref="eventsTableRef"
        v-loading="eventsQuery.isLoading.value"
        :data="events"
        row-key="seq"
        :row-class-name="rowClassName"
        max-height="560"
        @selection-change="handleEventSelectionChange"
      >
        <el-table-column type="selection" width="36" />
        <el-table-column label="seq" width="64">
          <template #default="{ row }"><span class="code-text small">{{ row.seq }}</span></template>
        </el-table-column>
        <el-table-column label="类型" width="122">
          <template #default="{ row }">
            <StatusTag :tone="kindMeta(row.kind).tone">{{ kindMeta(row.kind).label }}</StatusTag>
          </template>
        </el-table-column>
        <el-table-column label="目标 / 坐标" min-width="240">
          <template #default="{ row }">
            <span class="code-text small">{{ locationText(row) }}</span>
            <div v-if="targetText(row) !== '—' && coordText(row) !== '—'" class="cell-sub">
              {{ coordText(row) }}
            </div>
            <div v-if="durationText(row)" class="cell-sub">时长 {{ durationText(row) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="置信度" width="84">
          <template #default="{ row }">
            <el-tooltip :content="String(row.payload.confidence_reason ?? '')">
              <StatusTag :tone="confidenceMeta(row.confidence).tone">{{ confidenceMeta(row.confidence).label }}</StatusTag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="142">
          <template #default="{ row }"><span class="small muted">{{ formatDateTime(row.created_at) }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="60" align="right">
          <template #default="{ row }">
            <el-button link type="danger" :icon="Delete" title="删除事件" @click="removeEvent(row.seq)" />
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :description="eventsEmptyDescription">
            <el-button
              v-if="!isPhysical"
              type="primary"
              @click="router.push({ path: '/console', query: { tab: 'manual', manual: 'record' } })"
            >
              去控制台录制
            </el-button>
          </el-empty>
        </template>
      </el-table>

      <div v-if="events.length < eventCount" class="table-foot">
        <span>当前加载 {{ events.length }} / 共 {{ eventCount }} 个事件</span>
      </div>
    </section>

    <el-dialog v-model="convertOpen" :title="hasScript ? '重新生成脚本' : '转脚本'" width="540px" append-to-body>
      <el-form label-position="top" size="small">
        <el-form-item label="脚本名称">
          <el-input
            v-model="convertName"
            :placeholder="recording ? `默认：${recording.name}` : '默认：录制名称'"
            maxlength="200"
          />
        </el-form-item>
        <el-form-item label="点击定位方式">
          <el-radio-group v-model="convertTargetMode">
            <el-radio-button value="point">绝对坐标（推荐）</el-radio-button>
            <el-radio-button value="element">元素定位</el-radio-button>
          </el-radio-group>
          <div class="hint" style="margin-top: 4px">
            坐标：只记绝对像素点，游戏/自绘界面等无障碍树不可靠时最稳；元素：优先用文本/资源 id 定位。
          </div>
        </el-form-item>
        <div class="hint">已删除的事件不会参与转换；转换完成后会跳转到脚本编辑器。</div>
      </el-form>
      <template #footer>
        <el-button @click="convertOpen = false">取消</el-button>
        <el-button type="primary" :loading="convertMutation.isPending.value" @click="handleConvert">
          {{ hasScript ? '重新生成' : '生成脚本' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.recording-livebar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 11px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--red-soft);
  color: var(--red);
  font-size: 11px;
}

.recording-livebar .dot {
  width: 7px;
  height: 7px;
  background: var(--red);
  box-shadow: 0 0 0 3px var(--red-soft);
}

.recording-livebar .live-pulse {
  animation: recording-live-pulse 1.2s ease-in-out infinite;
}

.recording-livebar .hint {
  color: var(--muted);
}

.recording-livebar b {
  color: var(--ink);
}

.recording-livebar .spacer {
  flex: 1;
}

.recording-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.recording-summary .kv strong {
  font-size: 11.5px;
}

.recording-summary .kv > span {
  white-space: nowrap;
}

.recording-summary .kv-wide {
  grid-column: 1 / -1;
}

@keyframes recording-live-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

@media (max-width: 900px) {
  .recording-summary {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
