<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useMirrorSession } from './composables/useMirrorSession'
import { usePointerGestures } from './composables/usePointerGestures'
import { useKeyboardInput } from './composables/useKeyboardInput'
import MirrorToolbar from './components/MirrorToolbar.vue'
import MirrorStatusOverlay from './components/MirrorStatusOverlay.vue'
import MirrorStatusLine from './components/MirrorStatusLine.vue'
import RecordingBar from './components/RecordingBar.vue'
import UnsupportedNotice from './components/UnsupportedNotice.vue'
import { maybeRunSelfTestInDev } from './dev/selfTest'

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
const actionError = ref<string | null>(null)
let errorTimer: ReturnType<typeof setTimeout> | null = null

const session = useMirrorSession({
  serial: computed(() => props.serial),
  token: computed(() => props.token),
  canvasRef,
})

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

onMounted(() => {
  maybeRunSelfTestInDev()
})

onBeforeUnmount(() => {
  if (errorTimer !== null) clearTimeout(errorTimer)
})

function focusPanel(): void {
  rootRef.value?.focus({ preventScroll: true })
}

function handlePointerDown(event: PointerEvent): void {
  focusPanel()
  gestures.handlers.onPointerDown(event)
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
        <MirrorToolbar :session="session" />
        <RecordingBar
          v-if="showRecordingBar"
          :serial="serial"
          :disabled="!session.isSupported"
          @device-change="emit('device-change')"
        />
      </div>

      <div class="mirror-stage" @click="focusPanel">
        <canvas
          ref="canvasRef"
          class="mirror-canvas"
          @pointerdown="handlePointerDown"
          @pointermove="gestures.handlers.onPointerMove"
          @pointerup="gestures.handlers.onPointerUp"
          @pointercancel="gestures.handlers.onPointerCancel"
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
