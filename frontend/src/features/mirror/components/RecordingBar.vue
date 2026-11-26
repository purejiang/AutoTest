<script setup lang="ts">
import { CaretRight, EditPen, SwitchButton, VideoPause } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { useRecordingEventsWs } from '@/api/useRecordingEventsWs'
import type { Recording, RecordingEvent } from '@/types/api'
import { get, post } from '../lib/api'
import { errorMessage } from '../lib/api'
import StatusTag from '@/components/StatusTag.vue'

const props = withDefaults(
  defineProps<{
    serial: string
    disabled?: boolean
  }>(),
  { disabled: false },
)

const emit = defineEmits<{ 'device-change': [] }>()
const router = useRouter()

const name = ref('')
const recording = ref<Recording | null>(null)
const busy = ref<'start' | 'pause' | 'resume' | 'stop' | null>(null)
const error = ref<string | null>(null)
const stoppedId = ref<string | null>(null)
const elapsedText = ref('00:00')
let pollTimer: ReturnType<typeof setInterval> | null = null
let elapsedTimer: ReturnType<typeof setInterval> | null = null

const STATUS_TAG: Record<string, { tone: 'red' | 'amber' | 'gray'; text: string }> = {
  recording: { tone: 'red', text: '录制中' },
  paused: { tone: 'amber', text: '已暂停' },
  stopped: { tone: 'gray', text: '已停止' },
}

const EVENT_KIND_META: Record<string, { label: string; tone: 'blue' | 'violet' | 'cyan' | 'amber' | 'gray' }> = {
  tap: { label: '点击', tone: 'blue' },
  long_click: { label: '长按', tone: 'violet' },
  long_press: { label: '长按', tone: 'violet' },
  swipe: { label: '滑动', tone: 'cyan' },
  drag: { label: '拖拽', tone: 'violet' },
  input: { label: '输入', tone: 'amber' },
  key: { label: '按键', tone: 'amber' },
  app: { label: '应用', tone: 'violet' },
}

const recordingId = computed(() => recording.value?.id ?? null)
const isLive = computed(() => recording.value?.status === 'recording' || recording.value?.status === 'paused')
const recordingWs = useRecordingEventsWs(recordingId, { enabled: isLive, maxEvents: 200 })
// 最新事件在上，避免新事件把用户正在看的内容顶走；列表自身滚动看历史。
const liveEvents = computed(() => recordingWs.events.value.slice().reverse())
const headerEventCount = computed(() => {
  if (recording.value?.status === 'stopped') return recording.value.event_count ?? 0
  return Math.max(recording.value?.event_count ?? 0, recordingWs.eventCount.value, recordingWs.events.value.length)
})

function kindMeta(kind: string): { label: string; tone: 'blue' | 'violet' | 'cyan' | 'amber' | 'gray' } {
  return EVENT_KIND_META[kind] ?? { label: kind, tone: 'gray' }
}

function pointText(point?: { x?: number; y?: number }): string {
  if (!point) return '—'
  return `(${point.x ?? '?'}, ${point.y ?? '?'})`
}

function eventDetail(event: RecordingEvent): string {
  const payload = event.payload
  if (payload.from || payload.to) return `${pointText(payload.from)} → ${pointText(payload.to)}`
  const target = payload.node
  if (target) {
    if (target.by === 'point') return `(${target.x ?? '?'}, ${target.y ?? '?'})`
    return `${target.by} = ${target.value ?? ''}`
  }
  if (payload.text) {
    const text = String(payload.text)
    return text.length > 32 ? `${text.slice(0, 32)}…` : text
  }
  if (payload.key) return String(payload.key)
  if (payload.x !== undefined || payload.y !== undefined) return `(${payload.x ?? '?'}, ${payload.y ?? '?'})`
  return ''
}

function stopPolling(): void {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function tickElapsed(): void {
  const startedAt = recording.value?.started_at
  const startedMs = startedAt ? Date.parse(startedAt) : Number.NaN
  if (!Number.isFinite(startedMs)) {
    elapsedText.value = '00:00'
    return
  }
  const total = Math.max(0, Math.floor((Date.now() - startedMs) / 1000))
  const minutes = Math.floor(total / 60)
  elapsedText.value = `${String(minutes).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

function startPolling(): void {
  stopPolling()
  const id = recording.value?.id
  const status = recording.value?.status
  if (!id || status === 'stopped') return
  pollTimer = setInterval(async () => {
    try {
      const fresh = await get<Recording>(`/recordings/${encodeURIComponent(id)}`)
      if (recording.value && recording.value.id === id) {
        recording.value = { ...recording.value, ...fresh }
      }
    } catch {
      // 轮询失败静默，下一轮再试
    }
  }, 2000)
}

/** 刷新/重进页面时恢复该设备上仍在进行的录制，避免误显示“开始录制”后点出 409。 */
async function recoverActive(serial: string): Promise<void> {
  try {
    const raw = await get<{ items?: Recording[] } | Recording[]>('/recordings', { query: { limit: 50 } })
    const items = Array.isArray(raw) ? raw : (raw.items ?? [])
    if (serial !== props.serial) return
    const active = items.find(
      (item) => item.serial === serial && (item.status === 'recording' || item.status === 'paused'),
    )
    if (active) {
      stoppedId.value = null
      recording.value = active
    }
  } catch {
    // 恢复失败不阻塞手动开始；后端仍会用 409 兜底。
  }
}

watch(
  () => props.serial,
  (serial) => {
    stopPolling()
    recording.value = null
    stoppedId.value = null
    error.value = null
    busy.value = null
    void recoverActive(serial)
  },
)

watch(
  [() => recording.value?.id, () => recording.value?.status],
  () => startPolling(),
)

onMounted(() => {
  void recoverActive(props.serial)
  elapsedTimer = setInterval(tickElapsed, 1000)
  tickElapsed()
})
onBeforeUnmount(() => {
  stopPolling()
  if (elapsedTimer !== null) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
})

async function startRecording(): Promise<void> {
  busy.value = 'start'
  error.value = null
  try {
    const body: Record<string, unknown> = { serial: props.serial, mode: 'web' }
    if (name.value.trim()) body.name = name.value.trim()
    const created = await post<Recording>('/recordings', body)
    stoppedId.value = null
    recording.value = created
    name.value = ''
  } catch (err) {
    await recoverActive(props.serial)
    if (recording.value) {
      error.value = '该设备已有进行中的录制，已切换到当前录制'
    } else {
      error.value = `开始录制失败：${errorMessage(err)}`
    }
  } finally {
    busy.value = null
  }
}

async function control(action: 'pause' | 'resume' | 'stop'): Promise<void> {
  if (!recording.value) return
  const id = recording.value.id
  busy.value = action
  error.value = null
  try {
    const updated = await post<Recording>(`/recordings/${encodeURIComponent(id)}/${action}`)
    const nextStatus = action === 'stop' ? 'stopped' : action === 'pause' ? 'paused' : 'recording'
    if (recording.value && recording.value.id === id) {
      recording.value = {
        ...recording.value,
        ...(updated && typeof updated === 'object' ? updated : {}),
        status: nextStatus,
      }
    }
    if (action === 'stop') {
      stoppedId.value = id
      stopPolling()
      emit('device-change')
      ElMessage.success('录制已停止')
    }
  } catch (err) {
    const label = action === 'pause' ? '暂停录制' : action === 'resume' ? '继续录制' : '停止录制'
    error.value = `${label}失败：${errorMessage(err)}`
  } finally {
    busy.value = null
  }
}

function reset(): void {
  stopPolling()
  recording.value = null
  stoppedId.value = null
  error.value = null
}

function openRecording(withConvert = false): void {
  if (!stoppedId.value) return
  const query = withConvert ? '?action=to_script' : ''
  void router.push(`/recorder/${encodeURIComponent(stoppedId.value)}${query}`)
}
</script>

<template>
  <div class="card mirror-recording">
    <div class="card-head">
      <h2>
        <el-icon style="margin-right: 4px; vertical-align: -2px"><EditPen /></el-icon>
        脚本录制
        <StatusTag
          v-if="recording"
          :tone="STATUS_TAG[recording.status]?.tone ?? 'gray'"
          style="margin-left: 3px"
        >
          {{ STATUS_TAG[recording.status]?.text ?? recording.status }}
        </StatusTag>
      </h2>
      <span class="small muted">
        事件 {{ headerEventCount }}
        <template v-if="recording && recording.status !== 'stopped'"> · 已进行 {{ elapsedText }}</template>
      </span>
    </div>
    <div class="card-body">
      <el-alert
        v-if="error"
        type="error"
        :closable="true"
        show-icon
        :title="error"
        style="margin-bottom: 8px"
        @close="error = null"
      />
      <div v-if="!recording || recording.status === 'stopped'" class="recording-start">
        <div class="row wrap">
          <el-input
            v-model="name"
            size="small"
            placeholder="录制名称（可选）"
            style="width: 210px"
            :disabled="disabled || busy === 'start'"
            @keyup.enter="startRecording"
          />
          <el-button
            type="primary"
            size="small"
            :icon="EditPen"
            :loading="busy === 'start'"
            :disabled="disabled"
            @click="startRecording"
          >
            开始脚本录制
          </el-button>
        </div>
        <div class="hint recording-start-hint">
          只记录点击/滑动等操作并转成脚本，不录制画面；画面录制在设备控制面板的「视频录屏」。
        </div>
      </div>
      <div v-else class="row wrap">
        <el-button
          v-if="recording.status === 'recording'"
          size="small"
          :icon="VideoPause"
          :loading="busy === 'pause'"
          @click="control('pause')"
        >
          暂停
        </el-button>
        <el-button v-else size="small" :icon="CaretRight" :loading="busy === 'resume'" @click="control('resume')">
          继续
        </el-button>
        <el-button size="small" type="danger" :icon="SwitchButton" :loading="busy === 'stop'" @click="control('stop')">
          停止
        </el-button>
        <span class="hint">自动刷新</span>
      </div>

      <div v-if="recording && recording.status !== 'stopped'" class="rec-live">
        <div class="rec-live-head">
          <StatusTag :tone="STATUS_TAG[recording.status]?.tone ?? 'gray'">
            {{ STATUS_TAG[recording.status]?.text ?? recording.status }}
          </StatusTag>
          <strong>实时事件</strong>
          <span class="hint">
            {{ headerEventCount }} 条 · {{ recordingWs.connected.value ? '实时' : '连接中…' }}
          </span>
        </div>
        <template v-if="liveEvents.length > 0">
          <div class="rec-live-cols" aria-hidden>
            <span>#</span>
            <span>类型</span>
            <span>目标 / 坐标</span>
          </div>
          <div class="rec-live-list">
            <div v-for="event in liveEvents" :key="event.seq" class="rec-live-row">
              <span class="rec-live-seq code-text">#{{ event.seq }}</span>
              <StatusTag :tone="kindMeta(event.kind).tone">{{ kindMeta(event.kind).label }}</StatusTag>
              <span class="rec-live-detail" :title="eventDetail(event)">{{ eventDetail(event) || '—' }}</span>
            </div>
          </div>
        </template>
        <div v-else class="hint rec-live-empty">暂无事件，操作镜像后会实时出现在这里。</div>
      </div>

      <el-alert
        v-if="stoppedId && (!recording || recording.status === 'stopped')"
        type="success"
        :closable="false"
        show-icon
        title="录制完成，可查看或转脚本"
        style="margin-top: 8px"
      >
        <div class="row wrap">
          <el-button size="small" type="primary" @click="openRecording()">查看录制</el-button>
          <el-button size="small" @click="openRecording(true)">转脚本</el-button>
          <el-button size="small" @click="reset">再次录制</el-button>
        </div>
      </el-alert>
    </div>
  </div>
</template>

<style scoped>
.recording-start-hint {
  margin-top: 6px;
}

.rec-live {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--line);
}

.rec-live-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}

.rec-live-cols {
  display: grid;
  grid-template-columns: 26px 40px minmax(0, 1fr);
  gap: 6px;
  padding: 4px 4px 3px;
  color: var(--muted-2);
  font-size: 10px;
}

.rec-live-list {
  display: flex;
  flex-direction: column;
  max-height: min(56vh, 560px);
  overflow-y: auto;
  border-top: 1px solid var(--line);
}

.rec-live-row {
  display: grid;
  grid-template-columns: 26px 40px minmax(0, 1fr);
  align-items: center;
  gap: 6px;
  min-height: 26px;
  padding: 3px 4px;
  border-bottom: 1px solid var(--line);
}

.rec-live-seq {
  color: var(--muted-2);
  font-size: 10px;
}

.rec-live-detail {
  min-width: 0;
  overflow: hidden;
  color: var(--muted);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rec-live-empty {
  margin: 0;
}
</style>
