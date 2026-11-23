<script setup lang="ts">
import {
  ArrowLeft,
  Delete,
  Document,
  Refresh,
  SwitchButton,
  VideoPause,
  VideoPlay,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox, type TableInstance } from 'element-plus'
import { computed, ref, watch } from 'vue'
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

const route = useRoute()
const router = useRouter()
const id = computed(() => String(route.params.id ?? ''))

const recordingQuery = useRecording(id)
const recording = computed(() => recordingQuery.data.value)
const eventsQuery = useRecordingEvents(id, {
  limit: 1000,
  refetchInterval: computed(() => (recording.value?.status === 'stopped' ? false : 4000)),
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

const convertOpen = ref(false)
const convertName = ref('')
const convertTargetMode = ref<'point' | 'element'>('point')
const autoConvertHandled = ref(false)

const events = computed(() => eventsQuery.data.value?.items ?? [])
const statusMeta = computed(() => recordingStatusMeta(recording.value?.status))

function handleEventSelectionChange(rows: RecordingEvent[]): void {
  selectedEvents.value = rows
}

function clearEventSelection(): void {
  eventsTableRef.value?.clearSelection()
  selectedEvents.value = []
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
    clearEventSelection()
    await Promise.all([eventsQuery.refetch(), recordingQuery.refetch()])
  } catch {
    // 全局错误提示
  }
}
const modeMeta = computed(() => recordingModeMeta(recording.value?.mode))
const isRecording = computed(() => recording.value?.status === 'recording')
const isPaused = computed(() => recording.value?.status === 'paused')
const hasScript = computed(() => Boolean(recording.value?.script_id))
const convertLabel = computed(() => (hasScript.value ? '重新生成' : '转脚本'))

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
  stopMutation.mutate(undefined, { onSuccess: () => ElMessage.success('录制已停止') })
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
      title="录制草稿"
      :subtitle="`${recording.name || recording.id} · ${recording.serial} · ${recording.event_count ?? 0} 个事件`"
      @back="router.push('/recorder')"
    >
      <template #back><el-icon><ArrowLeft /></el-icon></template>
      <template #title-extra>
        <StatusTag :tone="statusMeta.tone">{{ statusMeta.label }}</StatusTag>
        <StatusTag :tone="modeMeta.tone">{{ modeMeta.label }}</StatusTag>
        <StatusTag v-if="recording.script_id" tone="green">已转脚本</StatusTag>
      </template>
      <template #actions>
        <el-button v-if="recording.script_id" type="primary" :icon="Document" @click="openScript">打开脚本</el-button>
        <el-button :type="recording.script_id ? 'default' : 'primary'" :icon="Document" @click="primaryConvert">{{ convertLabel }}</el-button>
        <el-button v-if="isRecording" :icon="VideoPause" :loading="pauseMutation.isPending.value" @click="pauseRecording">
          暂停
        </el-button>
        <el-button v-if="isPaused" :icon="VideoPlay" :loading="resumeMutation.isPending.value" @click="resumeRecording">
          继续
        </el-button>
        <el-button
          v-if="isRecording || isPaused"
          type="danger"
          :icon="SwitchButton"
          :loading="stopMutation.isPending.value"
          @click="stopRecording"
        >
          停止
        </el-button>
        <el-button :icon="Refresh" :loading="recordingQuery.isFetching.value || eventsQuery.isFetching.value" @click="refreshAll">
          刷新
        </el-button>
        <el-button type="danger" plain :icon="Delete" @click="removeRecording">删除</el-button>
      </template>
    </PageHead>

    <div class="recording-grid">
      <section class="card">
        <div class="card-head">
          <h2>
            事件清单
            <span class="tag blue" style="margin-left: 4px">{{ events.length }}</span>
            <span v-if="isRecording" class="tag red" style="margin-left: 4px">录制中，实时刷新</span>
          </h2>
          <span class="hint">低置信度事件会高亮；删除后不参与转脚本</span>
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
        <el-alert
          v-if="eventsQuery.isError.value"
          type="error"
          show-icon
          :closable="false"
          title="事件加载失败"
          :description="describeApiError(eventsQuery.error.value)"
          style="margin: 8px"
        />
        <el-table
          ref="eventsTableRef"
          v-loading="eventsQuery.isLoading.value"
          :data="events"
          row-key="seq"
          :row-class-name="rowClassName"
          max-height="620"
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
          <el-table-column label="坐标" width="170">
            <template #default="{ row }"><span class="code-text small">{{ coordText(row) }}</span></template>
          </el-table-column>
          <el-table-column label="目标" min-width="180">
            <template #default="{ row }"><span class="code-text small">{{ targetText(row) }}</span></template>
          </el-table-column>
          <el-table-column label="时长" width="80">
            <template #default="{ row }">
              <span class="small">{{ row.payload.duration_ms !== undefined ? formatDuration(row.payload.duration_ms) : '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="置信度" width="80">
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
              <el-button link type="danger" :icon="Delete" @click="removeEvent(row.seq)" />
            </template>
          </el-table-column>
          <template #empty>
            <el-empty description="暂无事件：去设备页在镜像上操作并开启录制">
              <el-button type="primary" @click="router.push('/devices')">去设备页录制</el-button>
            </el-empty>
          </template>
        </el-table>
        <div class="table-foot"><span>共 {{ events.length }} 个事件</span></div>
      </section>

      <div class="stack">
        <section class="card">
          <div class="card-head"><h2>录制摘要</h2></div>
          <div class="card-body">
            <div class="kv"><span>设备</span><strong class="code-text">{{ recording.serial }}</strong></div>
            <div class="kv"><span>模式</span><strong>{{ modeMeta.label }}</strong></div>
            <div class="kv"><span>状态</span><strong>{{ statusMeta.label }}</strong></div>
            <div class="kv"><span>开始</span><strong>{{ formatDateTime(recording.started_at) }}</strong></div>
            <div class="kv"><span>结束</span><strong>{{ formatDateTime(recording.ended_at) }}</strong></div>
            <div class="kv"><span>事件</span><strong>{{ recording.event_count ?? 0 }}</strong></div>
            <div class="kv"><span>关联脚本</span><strong>{{ recording.script_name || recording.script_id || '尚未生成' }}</strong></div>
          </div>
        </section>

        <section class="card">
          <div class="card-head">
            <h2>转脚本</h2>
            <StatusTag tone="blue">{{ hasScript ? '已生成' : '推荐' }}</StatusTag>
          </div>
          <div class="card-body">
            <div class="small muted" style="line-height: 1.6">
              把事件按手势分类转换为 12 类节点；低置信度事件会给出 warning。已删除的事件不会参与转换。
            </div>
            <el-alert
              v-if="hasScript"
              type="success"
              :closable="false"
              show-icon
              :title="`已关联脚本「${recording.script_name || recording.script_id}」`"
              description="重新生成前会再次确认，已删除的事件不会参与生成。"
              style="margin-top: 8px"
            />
            <div class="row" style="margin-top: 10px">
              <el-button v-if="hasScript" type="primary" :icon="Document" @click="openScript">打开脚本</el-button>
              <el-button :type="hasScript ? 'default' : 'primary'" :icon="Document" @click="primaryConvert">{{ convertLabel }}</el-button>
            </div>
            <div class="hint" style="margin-top: 7px">生成后跳转到脚本编辑器，再确认保存。</div>
          </div>
        </section>
      </div>
    </div>

    <el-dialog v-model="convertOpen" :title="hasScript ? '重新生成脚本' : '转脚本'" width="540px" append-to-body>
      <el-form label-position="top" size="small">
        <el-form-item label="脚本名称">
          <el-input v-model="convertName" placeholder="留空由后端自动命名" maxlength="200" />
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
