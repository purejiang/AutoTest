<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useSettings } from '@/api/settings'

import { useMirrorSession } from './composables/useMirrorSession'
import { usePointerGestures } from './composables/usePointerGestures'
import { useKeyboardInput } from './composables/useKeyboardInput'
import MirrorToolbar from './components/MirrorToolbar.vue'
import MirrorStatusOverlay from './components/MirrorStatusOverlay.vue'
import MirrorStatusLine from './components/MirrorStatusLine.vue'
import RecordingBar from './components/RecordingBar.vue'
import MirrorVideoRecorder from './components/MirrorVideoRecorder.vue'
import TouchEffectOverlay from './components/TouchEffectOverlay.vue'
import UnsupportedNotice from './components/UnsupportedNotice.vue'
import { maybeRunSelfTestInDev } from './dev/selfTest'

const TOUCH_EFFECT_STORAGE_KEY = 'autotest.mirror.touch-effect'
const TOUCH_SWIPE_STORAGE_KEY = 'autotest.mirror.swipe-effect'
const TOUCH_RIPPLE_STORAGE_KEY = 'autotest.mirror.touch-ripple'

interface TouchEffectHandle {
  down: (clientX: number, clientY: number) => void
  move: (clientX: number, clientY: number) => void
  up: (clientX: number, clientY: number) => void
  cancel: () => void
  clear: () => void
  getCanvas: () => HTMLCanvasElement | null
}

function readFlag(key: string): boolean {
  try {
    const stored = window.localStorage.getItem(key)
    if (stored === '0') return false
    if (stored === '1') return true
  } catch {
    /* 隐私模式/禁用 storage 时用默认值 */
  }
  return true
}

function writeFlag(key: string, value: boolean): void {
  try {
    window.localStorage.setItem(key, value ? '1' : '0')
  } catch {
    /* 存不下就只在本次会话生效 */
  }
}

const props = withDefaults(
  defineProps<{
    serial: string
    token?: string
    showRecordingBar?: boolean
  }>(),
  { token: undefined, showRecordingBar: true },
)

const emit = defineEmits<{ 'device-change': [] }>()

const rootRef = ref<HTMLDivElement | null>(null)
const canvasRef = ref<HTMLCanvasElement | null>(null)
const touchEffectRef = ref<TouchEffectHandle | null>(null)
const tapEffectEnabled = ref(readFlag(TOUCH_EFFECT_STORAGE_KEY))
const swipeEffectEnabled = ref(readFlag(TOUCH_SWIPE_STORAGE_KEY))
const rippleEnabled = ref(readFlag(TOUCH_RIPPLE_STORAGE_KEY))
const actionError = ref<string | null>(null)
let errorTimer: ReturnType<typeof setTimeout> | null = null

const session = useMirrorSession({
  serial: computed(() => props.serial),
  token: computed(() => props.token),
  canvasRef,
})

// 会话参数 vs 当前设置：改完设置后“重新连接”不会重启 scrcpy，容易一直跑旧帧率。
const settingsQuery = useSettings()
const restartingMirror = ref(false)
const configMismatch = computed(() => {
  const info = session.hello.value
  const effective = settingsQuery.data.value?.effective
  if (!info || !effective) return null
  const diffs: string[] = []
  const fps = Number(effective['scrcpy.max_fps'])
  const size = Number(effective['scrcpy.max_size'])
  const bitrate = Number(effective['scrcpy.video_bit_rate'])
  if (info.maxFps !== undefined && Number.isFinite(fps) && info.maxFps !== fps) {
    diffs.push(`帧率 ${info.maxFps} → ${fps}`)
  }
  if (info.maxSize !== undefined && Number.isFinite(size) && info.maxSize !== size) {
    diffs.push(`最大边 ${info.maxSize} → ${size}`)
  }
  if (info.videoBitRate !== undefined && Number.isFinite(bitrate) && info.videoBitRate !== bitrate) {
    diffs.push(`码率 ${Math.round(info.videoBitRate / 1e6)}M → ${Math.round(bitrate / 1e6)}M`)
  }
  return diffs.length ? diffs.join('，') : null
})

async function restartMirrorForConfig(): Promise<void> {
  if (restartingMirror.value) return
  restartingMirror.value = true
  try {
    await session.stopMirror()
    await session.startMirror()
  } finally {
    restartingMirror.value = false
  }
}

const gestures = usePointerGestures({
  serial: computed(() => props.serial),
  canvasRef,
  enabled: computed(() => session.status.value === 'streaming'),
  onError: (message) => {
    actionError.value = message
  },
  wsTouch: session.sendTouch,
})

const keyboard = useKeyboardInput({
  serial: computed(() => props.serial),
  enabled: computed(() => session.status.value === 'streaming' && session.isSupported),
  onError: (message) => {
    actionError.value = message
  },
})

watch(
  () => props.serial,
  () => {
    actionError.value = null
  },
)

watch(actionError, (value) => {
  if (errorTimer !== null) clearTimeout(errorTimer)
  if (!value) return
  errorTimer = setTimeout(() => {
    actionError.value = null
    errorTimer = null
  }, 5000)
})

watch(tapEffectEnabled, (value) => {
  writeFlag(TOUCH_EFFECT_STORAGE_KEY, value)
})

watch(swipeEffectEnabled, (value) => {
  writeFlag(TOUCH_SWIPE_STORAGE_KEY, value)
})

watch(rippleEnabled, (value) => {
  writeFlag(TOUCH_RIPPLE_STORAGE_KEY, value)
})

// 断流/等待关键帧时清掉残留的轨迹，避免画面已经停了特效还挂在上面。
watch(
  () => session.status.value,
  (status) => {
    if (status !== 'streaming') touchEffectRef.value?.clear()
  },
)

onMounted(() => {
  maybeRunSelfTestInDev()
})

onBeforeUnmount(() => {
  if (errorTimer !== null) clearTimeout(errorTimer)
})

function focusPanel(): void {
  rootRef.value?.focus({ preventScroll: true })
}

function getOverlayCanvas(): HTMLCanvasElement | null {
  return touchEffectRef.value?.getCanvas() ?? null
}

function handlePointerDown(event: PointerEvent): void {
  focusPanel()
  touchEffectRef.value?.down(event.clientX, event.clientY)
  gestures.handlers.onPointerDown(event)
}

function handlePointerMove(event: PointerEvent): void {
  touchEffectRef.value?.move(event.clientX, event.clientY)
  gestures.handlers.onPointerMove(event)
}

function handlePointerUp(event: PointerEvent): void {
  touchEffectRef.value?.up(event.clientX, event.clientY)
  gestures.handlers.onPointerUp(event)
}

function handlePointerCancel(event: PointerEvent): void {
  touchEffectRef.value?.cancel()
  gestures.handlers.onPointerCancel(event)
}
</script>

<template>
  <div
    ref="rootRef"
    class="mirror-panel"
    tabindex="0"
    :aria-label="`设备 ${serial} 镜像面板`"
    @keydown="keyboard.onKeyDown"
  >
    <UnsupportedNotice v-if="!session.isSupported" />
    <template v-else>
      <div class="mirror-above">
        <MirrorToolbar
          v-model:tap-effect="tapEffectEnabled"
          v-model:swipe-effect="swipeEffectEnabled"
          v-model:ripple="rippleEnabled"
          :session="session"
        />
        <RecordingBar
          v-if="showRecordingBar"
          :serial="serial"
          :disabled="!session.isSupported"
          @device-change="emit('device-change')"
        />
        <MirrorVideoRecorder
          v-if="showRecordingBar"
          :serial="serial"
          :source="canvasRef"
          :overlay="getOverlayCanvas"
          :enabled="session.status.value === 'streaming'"
        />
      </div>

      <div v-if="configMismatch" class="mirror-config-alert">
        <span>画质设置已变更（{{ configMismatch }}），当前镜像仍在用旧参数；重启镜像后生效。</span>
        <el-button size="small" type="primary" :loading="restartingMirror" @click="restartMirrorForConfig">
          立即重启镜像
        </el-button>
      </div>

      <div class="mirror-stage" @click="focusPanel">
        <canvas
          ref="canvasRef"
          class="mirror-canvas"
          @pointerdown="handlePointerDown"
          @pointermove="handlePointerMove"
          @pointerup="handlePointerUp"
          @pointercancel="handlePointerCancel"
        />
        <TouchEffectOverlay
          ref="touchEffectRef"
          :enabled="(tapEffectEnabled || swipeEffectEnabled) && session.status.value === 'streaming'"
          :tap-enabled="tapEffectEnabled"
          :swipe-enabled="swipeEffectEnabled"
          :ripple-enabled="rippleEnabled"
          :target="canvasRef"
        />
        <MirrorStatusOverlay
          :status="session.status.value"
          :error="session.error.value"
          :hello="session.hello.value"
          :has-frame="session.stats.value.decoded > 0"
          @start="session.startMirror()"
          @retry="session.reconnect()"
        />
      </div>

      <MirrorStatusLine
        :serial="serial"
        :session="session"
        :pending-text="keyboard.pendingText.value"
        :busy="gestures.busy.value"
      />
      <div class="mirror-errorline" role="alert">{{ actionError ?? session.error.value ?? '' }}</div>
    </template>
  </div>
</template>
