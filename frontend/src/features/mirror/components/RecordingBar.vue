<script setup lang="ts">
import { CaretRight, EditPen, SwitchButton, VideoPause } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import type { Recording } from '@/types/api'
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
        <StatusTag tone="blue" style="margin-left: 4px">操作 → 脚本</StatusTag>
        <StatusTag
          v-if="recording"
          :tone="STATUS_TAG[recording.status]?.tone ?? 'gray'"
          style="margin-left: 3px"
        >
          {{ STATUS_TAG[recording.status]?.text ?? recording.status }}
        </StatusTag>
      </h2>
      <span class="small muted">
        事件 {{ recording?.event_count ?? 0 }}
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
      <div v-if="!recording || recording.status === 'stopped'" class="row wrap">
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
        <span class="hint">只记录点击/滑动等操作并转成脚本，不录制画面；画面录制在设备控制面板的「视频录屏」。</span>
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
        <StatusTag tone="blue" mono>id: {{ recording.id }}</StatusTag>
        <span class="hint">事件数 {{ recording.event_count ?? 0 }} · 每 2s 刷新</span>
      </div>

      <el-alert
        v-if="stoppedId && (!recording || recording.status === 'stopped')"
        type="success"
        :closable="false"
        show-icon
        title="录制已停止"
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
