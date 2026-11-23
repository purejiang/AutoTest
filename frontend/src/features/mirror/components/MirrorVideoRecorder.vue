<script setup lang="ts">
// 浏览器端视频录制：录制当前镜像 canvas（可叠加触控特效层）。
//
// 与设备端 screenrecord 的区别：
// - 不依赖手机录制进程，录的是“你看到的镜像画面”；
// - MediaRecorder 原生支持 pause/resume，暂停期间不写入视频、计时也暂停；
// - 停止后立即可用 <video controls> 预览/拖动/下载，无需等 adb pull。
//
// 实现：镜像 canvas + 触控特效 canvas 合成到离屏 canvas -> captureStream -> MediaRecorder。
// 输出优先 MP4（浏览器支持时），否则 WebM；WebM 的 duration 缺失会在 loadedmetadata 时兜底修正。

import { CaretRight, RefreshLeft, SwitchButton, VideoCamera, VideoPause } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { formatBytes } from '@/lib/format'

const props = withDefaults(
  defineProps<{
    serial: string
    /** 镜像 canvas（CanvasRenderer 的输出）；与 getSource 二选一 */
    source?: HTMLCanvasElement | null
    /** 延迟取镜像 canvas（控制台拆分后 canvas 在子组件里，录制时再取） */
    getSource?: (() => HTMLCanvasElement | null) | null
    /** 取触控特效覆盖层 canvas；关闭特效/未挂载时返回 null */
    overlay?: (() => HTMLCanvasElement | null) | null
    /** 镜像是否正在播放 */
    enabled?: boolean
  }>(),
  { source: null, getSource: null, overlay: null, enabled: true },
)

type RecorderPhase = 'idle' | 'recording' | 'paused' | 'stopped'

const BITRATE_30FPS = 8_000_000
const BITRATE_60FPS = 12_000_000
const MIME_CANDIDATES = [
  'video/mp4;codecs=avc1.42E01E',
  'video/mp4',
  'video/webm;codecs=vp9',
  'video/webm;codecs=vp8',
  'video/webm',
]

const supported =
  typeof window !== 'undefined' &&
  typeof MediaRecorder !== 'undefined' &&
  typeof HTMLCanvasElement !== 'undefined' &&
  'captureStream' in HTMLCanvasElement.prototype
const phase = ref<RecorderPhase>('idle')
const name = ref('')
const fps = ref(30)
const recordedFps = ref(30)
const includeEffects = ref(true)
const durationMs = ref(0)
const sizeBytes = ref(0)
const videoUrl = ref<string | null>(null)
const fileName = ref('')
const mimeType = ref('')
const error = ref<string | null>(null)
const stopping = ref(false)

const recording = computed(() => phase.value === 'recording' || phase.value === 'paused')
const elapsedText = computed(() => formatClock(durationMs.value / 1000))
const formatLabel = computed(() => (mimeType.value.includes('mp4') ? 'MP4' : 'WebM'))

let recorder: MediaRecorder | null = null
let stream: MediaStream | null = null
let chunks: Blob[] = []
let composite: HTMLCanvasElement | null = null
let compositeCtx: CanvasRenderingContext2D | null = null
let rafId = 0
let timerId: ReturnType<typeof setInterval> | null = null
let segmentStartedAt = 0
let accumulatedMs = 0
let disposed = false

function formatClock(seconds: number): string {
  const total = Math.max(0, Math.floor(Number(seconds) || 0))
  const minutes = Math.floor(total / 60)
  return `${String(minutes).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

function ensureEven(value: number): number {
  const even = Math.round(value)
  return Math.max(2, even % 2 === 0 ? even : even - 1)
}

function pickMimeType(): string {
  for (const type of MIME_CANDIDATES) {
    try {
      if (MediaRecorder.isTypeSupported(type)) return type
    } catch {
      /* 某些实现会抛 */
    }
  }
  return ''
}

function overlayCanvas(): HTMLCanvasElement | null {
  try {
    return props.overlay?.() ?? null
  } catch {
    return null
  }
}

function mirrorCanvas(): HTMLCanvasElement | null {
  if (props.source) return props.source
  try {
    return props.getSource?.() ?? null
  } catch {
    return null
  }
}

function drawComposite(): void {
  rafId = requestAnimationFrame(drawComposite)
  const canvas = composite
  const ctx = compositeCtx
  if (!canvas || !ctx) return
  const width = canvas.width
  const height = canvas.height
  ctx.fillStyle = '#000000'
  ctx.fillRect(0, 0, width, height)
  const source = mirrorCanvas()
  if (source && source.width > 0 && source.height > 0) {
    try {
      ctx.drawImage(source, 0, 0, width, height)
    } catch {
      /* 单帧绘制失败不影响录制 */
    }
  }
  if (includeEffects.value) {
    const effects = overlayCanvas()
    if (effects && effects.width > 0 && effects.height > 0) {
      try {
        ctx.drawImage(effects, 0, 0, width, height)
      } catch {
        /* 特效层绘制失败时只录镜像画面 */
      }
    }
  }
}

function startTimer(): void {
  stopTimer()
  timerId = setInterval(() => {
    if (phase.value !== 'recording') return
    durationMs.value = accumulatedMs + (performance.now() - segmentStartedAt)
  }, 200)
}

function stopTimer(): void {
  if (timerId !== null) {
    clearInterval(timerId)
    timerId = null
  }
}

function stopDrawing(): void {
  if (rafId) {
    cancelAnimationFrame(rafId)
    rafId = 0
  }
}

function revokeUrl(): void {
  if (videoUrl.value) {
    URL.revokeObjectURL(videoUrl.value)
    videoUrl.value = null
  }
}

function cleanupCapture(): void {
  stopDrawing()
  stopTimer()
  for (const track of stream?.getTracks() ?? []) {
    try {
      track.stop()
    } catch {
      /* noop */
    }
  }
  stream = null
  recorder = null
  if (composite?.parentNode) composite.parentNode.removeChild(composite)
  composite = null
  compositeCtx = null
  stopping.value = false
}

function buildFileName(type: string): string {
  const base = (name.value.trim() || 'mirror').replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 60)
  const serial = props.serial.replace(/[^\w.-]+/g, '_')
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  const ext = type.includes('mp4') ? 'mp4' : 'webm'
  return `${base}-${serial}-${stamp}.${ext}`
}

function finalize(): void {
  const type = recorder?.mimeType || mimeType.value || 'video/webm'
  mimeType.value = type
  const blob = new Blob(chunks, { type })
  chunks = []
  cleanupCapture()
  if (disposed) {
    if (videoUrl.value) URL.revokeObjectURL(videoUrl.value)
    videoUrl.value = null
    return
  }
  if (blob.size <= 0) {
    phase.value = 'idle'
    error.value = '录制没有产出数据，请确认镜像正在播放后重试。'
    return
  }
  revokeUrl()
  videoUrl.value = URL.createObjectURL(blob)
  sizeBytes.value = blob.size
  fileName.value = buildFileName(type)
  phase.value = 'stopped'
  durationMs.value = accumulatedMs
  ElMessage.success('视频录制完成，可在下方预览或下载')
}

function start(): void {
  error.value = null
  if (recorder) return
  if (!supported) {
    error.value = '当前浏览器不支持 MediaRecorder，无法使用浏览器端录制。'
    return
  }
  if (!props.enabled) {
    error.value = '镜像尚未开始播放，无法录制。'
    return
  }
  const source = mirrorCanvas()
  if (!source || source.width < 2 || source.height < 2) {
    error.value = '镜像画面尚未就绪，等第一帧出来后再试。'
    return
  }
  const mime = pickMimeType()
  if (!mime) {
    error.value = '当前浏览器没有可用的视频编码格式（MP4/WebM 都不支持）。'
    return
  }

  revokeUrl()
  sizeBytes.value = 0
  durationMs.value = 0
  accumulatedMs = 0
  chunks = []
  mimeType.value = mime
  error.value = null
  phase.value = 'recording'
  stopping.value = false

  composite = document.createElement('canvas')
  composite.width = ensureEven(source.width)
  composite.height = ensureEven(source.height)
  // 个别浏览器对“未挂到 DOM 的 canvas”不产帧：挂到视口外但保持渲染。
  composite.style.position = 'fixed'
  composite.style.left = '-10000px'
  composite.style.top = '0'
  composite.style.width = '1px'
  composite.style.height = '1px'
  composite.style.pointerEvents = 'none'
  document.body.appendChild(composite)
  compositeCtx = composite.getContext('2d', { alpha: false })
  if (!compositeCtx) {
    cleanupCapture()
    phase.value = 'idle'
    error.value = '无法创建录制画布（2D context 不可用）。'
    return
  }
  drawComposite()

  recordedFps.value = fps.value
  try {
    stream = composite.captureStream(fps.value)
    recorder = new MediaRecorder(stream, {
      mimeType: mime,
      videoBitsPerSecond: fps.value >= 60 ? BITRATE_60FPS : BITRATE_30FPS,
    })
  } catch (err) {
    cleanupCapture()
    phase.value = 'idle'
    error.value = `创建录制器失败：${err instanceof Error ? err.message : String(err)}`
    return
  }

  recorder.ondataavailable = (event: BlobEvent) => {
    if (event.data && event.data.size > 0) chunks.push(event.data)
  }
  recorder.onstop = () => finalize()
  recorder.onerror = () => {
    cleanupCapture()
    phase.value = 'idle'
    error.value = '录制过程中发生错误，已停止。'
  }

  segmentStartedAt = performance.now()
  startTimer()
  try {
    recorder.start(1000)
  } catch (err) {
    cleanupCapture()
    phase.value = 'idle'
    error.value = `开始录制失败：${err instanceof Error ? err.message : String(err)}`
  }
}

function togglePause(): void {
  const rec = recorder
  if (!rec || stopping.value) return
  if (phase.value === 'recording') {
    accumulatedMs += performance.now() - segmentStartedAt
    durationMs.value = accumulatedMs
    rec.pause()
    phase.value = 'paused'
    return
  }
  if (phase.value === 'paused') {
    segmentStartedAt = performance.now()
    rec.resume()
    phase.value = 'recording'
  }
}

function stop(): void {
  const rec = recorder
  if (!rec || stopping.value) return
  if (phase.value === 'recording') {
    accumulatedMs += performance.now() - segmentStartedAt
    durationMs.value = accumulatedMs
  }
  stopping.value = true
  try {
    rec.stop()
  } catch {
    finalize()
  }
}

function reset(): void {
  cleanupCapture()
  revokeUrl()
  phase.value = 'idle'
  durationMs.value = 0
  sizeBytes.value = 0
  fileName.value = ''
  mimeType.value = ''
  error.value = null
}

/** MediaRecorder 产出的 WebM 常缺 duration：强制浏览器算一次总时长，保证可拖动进度。 */
function fixDuration(event: Event): void {
  const video = event.target as HTMLVideoElement
  if (Number.isFinite(video.duration)) return
  try {
    video.currentTime = Number.MAX_SAFE_INTEGER
  } catch {
    return
  }
  const resetTime = () => {
    video.removeEventListener('timeupdate', resetTime)
    video.currentTime = 0
  }
  video.addEventListener('timeupdate', resetTime)
}

watch(
  () => props.enabled,
  (enabled) => {
    if (!enabled && recording.value && !stopping.value) stop()
  },
)

onBeforeUnmount(() => {
  disposed = true
  if (recording.value) {
    try {
      recorder?.stop()
    } catch {
      /* noop */
    }
  }
  cleanupCapture()
  revokeUrl()
})
</script>

<template>
  <div class="card mirror-recording mirror-video-recorder">
    <div class="card-head">
      <h2>
        <el-icon style="margin-right: 4px; vertical-align: -2px"><VideoCamera /></el-icon>
        视频录制
        <StatusTag tone="blue" style="margin-left: 4px">浏览器画面</StatusTag>
        <StatusTag v-if="phase === 'recording'" tone="red" style="margin-left: 3px">录制中</StatusTag>
        <StatusTag v-else-if="phase === 'paused'" tone="amber" style="margin-left: 3px">已暂停</StatusTag>
        <StatusTag v-else-if="phase === 'stopped'" tone="gray" style="margin-left: 3px">已完成</StatusTag>
      </h2>
      <span v-if="recording || phase === 'stopped'" class="small muted">
        {{ elapsedText }}<template v-if="sizeBytes > 0"> · {{ formatBytes(sizeBytes) }}</template>
      </span>
    </div>

    <div class="card-body">
      <el-alert
        v-if="!supported"
        type="warning"
        :closable="false"
        show-icon
        title="当前浏览器不支持浏览器端录制"
        description="需要支持 MediaRecorder 与 canvas.captureStream 的 Chrome/Edge。"
      />
      <el-alert
        v-else-if="error"
        type="error"
        :closable="true"
        show-icon
        :title="error"
        style="margin-bottom: 8px"
        @close="error = null"
      />

      <template v-if="supported && phase === 'idle'">
        <div class="row wrap">
          <el-input
            v-model="name"
            size="small"
            placeholder="视频名称（可选）"
            style="width: 210px"
            :disabled="!enabled"
          />
          <el-select v-model="fps" size="small" style="width: 92px" :disabled="!enabled">
            <el-option :value="30" label="30fps" />
            <el-option :value="60" label="60fps" />
          </el-select>
          <el-button type="primary" size="small" :icon="VideoCamera" :disabled="!enabled" @click="start">
            开始录制
          </el-button>
          <el-checkbox v-model="includeEffects" size="small">包含点击/滑动特效</el-checkbox>
        </div>
        <div class="hint" style="margin-top: 6px">
          录制当前镜像画面（可叠加点击特效），支持暂停/继续；停止后可直接预览、拖动进度和下载。
        </div>
      </template>

      <template v-else-if="supported && recording">
        <div class="row wrap">
          <span class="rec-dot" :class="{ 'is-live': phase === 'recording' }" aria-hidden />
          <span class="rec-clock">{{ elapsedText }}</span>
          <el-button
            v-if="phase === 'recording'"
            size="small"
            :icon="VideoPause"
            :disabled="stopping"
            @click="togglePause"
          >
            暂停
          </el-button>
          <el-button v-else size="small" :icon="CaretRight" :disabled="stopping" @click="togglePause">
            继续
          </el-button>
          <el-button type="danger" size="small" :icon="SwitchButton" :loading="stopping" @click="stop">
            停止并保存
          </el-button>
          <span class="hint">暂停期间不写入视频，计时也暂停。</span>
        </div>
        <div class="hint" style="margin-top: 6px">
          {{ includeEffects ? '正在录制：镜像画面 + 点击/滑动特效。' : '正在录制：仅镜像画面，不含点击/滑动特效。' }} 录制期间请保持本页在前台，切到后台画面会停更。
        </div>
      </template>

      <template v-else-if="supported && phase === 'stopped' && videoUrl">
        <video
          class="mirror-video-preview"
          :src="videoUrl"
          controls
          preload="metadata"
          @loadedmetadata="fixDuration"
        />
        <div class="mirror-video-meta">
          <span class="mirror-video-name" :title="fileName">{{ fileName }}</span>
          <span class="hint">{{ formatBytes(sizeBytes) }} · {{ elapsedText }} · {{ recordedFps }}fps · {{ formatLabel }}</span>
          <a class="mirror-video-download" :href="videoUrl" :download="fileName">下载视频</a>
          <el-button size="small" text :icon="RefreshLeft" @click="reset">重新录制</el-button>
        </div>
      </template>
    </div>
  </div>
</template>
