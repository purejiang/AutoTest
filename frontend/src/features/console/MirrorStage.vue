<script setup lang="ts">
import { Lock, VideoPause, VideoPlay } from '@element-plus/icons-vue'
import { onClickOutside } from '@vueuse/core'
import { ElMessage } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useDevices, useDeviceKey } from '@/api/devices'
import { useSettings } from '@/api/settings'
import { usePersistentFlag } from '@/composables/usePersistentFlag'
import type { Device } from '@/types/api'

import { useKeyboardInput } from '../mirror/composables/useKeyboardInput'
import { useMirrorSession } from '../mirror/composables/useMirrorSession'
import { usePointerGestures } from '../mirror/composables/usePointerGestures'
import { describeConfigMismatch } from '../mirror/lib/configMismatch'
import type { MirrorUiStatus } from '../mirror/types'
import MirrorStatusLine from '../mirror/components/MirrorStatusLine.vue'
import MirrorStatusOverlay from '../mirror/components/MirrorStatusOverlay.vue'
import TouchEffectOverlay from '../mirror/components/TouchEffectOverlay.vue'
import UnsupportedNotice from '../mirror/components/UnsupportedNotice.vue'
import '../mirror/mirror.css'
import MirrorActionRail from './MirrorActionRail.vue'
import MirrorDeviceHeader from './MirrorDeviceHeader.vue'

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

const props = withDefaults(
  defineProps<{
    serial: string
    token?: string
    devices: Device[]
    selectedDevice: Device | null
    /** 脚本运行中（未暂停）：镜像锁定，手动注入全部禁用（后端同样 409）。 */
    runLocked?: boolean
    /** 脚本已暂停：干预模式开启，手动操作允许并留痕。 */
    runPaused?: boolean
  }>(),
  { token: undefined, runLocked: false, runPaused: false },
)

const emit = defineEmits<{
  'select-device': [serial: string]
  'connect-device': []
  'refresh-devices': []
  'disconnect-device': [device: Device]
  status: [status: MirrorUiStatus]
  screenshot: []
}>()

const rootRef = ref<HTMLDivElement | null>(null)
const canvasRef = ref<HTMLCanvasElement | null>(null)
const touchEffectRef = ref<TouchEffectHandle | null>(null)
const effectMenuRef = ref<HTMLDivElement | null>(null)
const effectPopoverOpen = ref(false)

const tapEffectEnabled = usePersistentFlag(TOUCH_EFFECT_STORAGE_KEY)
const swipeEffectEnabled = usePersistentFlag(TOUCH_SWIPE_STORAGE_KEY)
const rippleEnabled = usePersistentFlag(TOUCH_RIPPLE_STORAGE_KEY)
const actionError = ref<string | null>(null)
let errorTimer: ReturnType<typeof setTimeout> | null = null

const session = useMirrorSession({
  serial: computed(() => props.serial),
  token: computed(() => props.token),
  canvasRef,
})
const keyMutation = useDeviceKey(props.serial)
const devicesQuery = useDevices({ refetchInterval: 5000 })

watch(
  () => session.status.value,
  (status) => emit('status', status),
  { immediate: true },
)

// 设置变更后当前镜像仍在旧参数上跑：提示并允许一键重启。
// 注意 hello.max_size 是**算过缩放后的实际长边**，不是设置页的上限——比较逻辑见
// ../mirror/lib/configMismatch.ts（历史上这里直接拿两者比较，导致常驻一条假提示）。
const settingsQuery = useSettings()
const restartingMirror = ref(false)
const configMismatch = computed(() =>
  describeConfigMismatch(session.hello.value, settingsQuery.data.value?.effective),
)
// 用户点“忽略”后不再打扰；设置又变了（summary 变化）或换了设备时重新提示。
const dismissedMismatch = ref<string | null>(null)
const visibleMismatch = computed(() =>
  configMismatch.value && configMismatch.value.summary !== dismissedMismatch.value
    ? configMismatch.value
    : null,
)
watch(
  () => props.serial,
  () => {
    dismissedMismatch.value = null
  },
)

function dismissMismatch(): void {
  dismissedMismatch.value = configMismatch.value?.summary ?? null
}

const mismatchText = computed(() => {
  const mismatch = visibleMismatch.value
  if (!mismatch) return ''
  const running = mismatch.runningLongEdge > 0 ? `（长边 ${mismatch.runningLongEdge}px）` : ''
  return `画质设置已变更（${mismatch.summary}），当前镜像仍按旧参数运行${running}，重启镜像后生效。`
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
  enabled: computed(() => session.status.value === 'streaming' && !props.runLocked),
  onError: (message) => {
    actionError.value = message
  },
  wsTouch: session.sendTouch,
})

const keyboard = useKeyboardInput({
  serial: computed(() => props.serial),
  enabled: computed(
    () => session.status.value === 'streaming' && session.isSupported && !props.runLocked,
  ),
  onError: (message) => {
    actionError.value = message
  },
})

// 脚本开始运行 -> 立即锁定：把可能悬着的手势补 UP、清掉触点特效、收起菜单。
watch(
  () => props.runLocked,
  (locked) => {
    if (!locked) return
    gestures.abortGesture()
    touchEffectRef.value?.clear()
    effectPopoverOpen.value = false
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

watch(
  () => session.status.value,
  (status) => {
    if (status !== 'streaming') touchEffectRef.value?.clear()
  },
)

onClickOutside(effectMenuRef, () => {
  effectPopoverOpen.value = false
})

onMounted(() => {
  // 首次挂载时把状态同步给父组件（watch immediate 可能早于父组件监听）。
  emit('status', session.status.value)
  // 捕获阶段兜底：即使画布的指针捕获被丢掉、或松手点落在黑色区域（不是画布）上，
  // 也能收到 pointerup/pointercancel 并补发 UP。
  window.addEventListener('pointerup', handleWindowPointerEnd, true)
  window.addEventListener('pointercancel', handleWindowPointerEnd, true)
  window.addEventListener('blur', handleWindowBlur)
  document.addEventListener('visibilitychange', handleVisibilityChange)
})

onBeforeUnmount(() => {
  if (errorTimer !== null) clearTimeout(errorTimer)
  canvasRef.value?.removeEventListener('pointerrawupdate', handlePointerRawUpdate)
  window.removeEventListener('pointerup', handleWindowPointerEnd, true)
  window.removeEventListener('pointercancel', handleWindowPointerEnd, true)
  window.removeEventListener('blur', handleWindowBlur)
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  releaseOnFocusLoss()
})

const status = computed(() => session.status.value)
const streaming = computed(() => status.value === 'streaming')
const idle = computed(() => status.value === 'idle')
const starting = computed(() => status.value === 'starting' || status.value === 'checking')
const deviceName = computed(() => {
  const device = props.selectedDevice
  if (!device) return '未选择设备'
  return [device.brand, device.model].filter(Boolean).join(' ') || device.serial
})
const deviceStateText = computed(() => {
  const device = props.selectedDevice
  if (!device) return '离线'
  if (device.state !== 'device') return device.state === 'unauthorized' ? '未授权' : '离线'
  if (device.busy) return device.busy_reason || '占用中'
  return '在线'
})
const fps = computed(() => session.stats.value.fps ?? 0)
// 两个帧率的关系：这里是“画布实际画了多少帧”，状态行的“设备出帧”是“后端从设备读到多少帧”。
const fpsHint = computed(() => {
  const serverFps = session.stats.value.serverFps
  const head = '画布实际渲染帧率（浏览器解码 + 绘制，受屏幕刷新率与解码能力限制）。'
  const tail = '画面静止时设备不出新帧，两个数字都会变低，属正常。'
  return serverFps === undefined ? `${head}${tail}` : `${head}设备出帧 ${serverFps}fps。${tail}`
})
const showFps = computed(() => status.value !== 'idle')
const effectCount = computed(
  () =>
    (tapEffectEnabled.value ? 1 : 0) +
    (tapEffectEnabled.value && rippleEnabled.value ? 1 : 0) +
    (swipeEffectEnabled.value ? 1 : 0),
)

function focusPanel(): void {
  rootRef.value?.focus({ preventScroll: true })
}

function getOverlayCanvas(): HTMLCanvasElement | null {
  return touchEffectRef.value?.getCanvas() ?? null
}

function getCanvas(): HTMLCanvasElement | null {
  return canvasRef.value
}

function handlePointerDown(event: PointerEvent): void {
  if (props.runLocked) return
  focusPanel()
  touchEffectRef.value?.down(event.clientX, event.clientY)
  gestures.handlers.onPointerDown(event)
}

function handlePointerMove(event: PointerEvent): void {
  if (props.runLocked) return
  // 鼠标/笔在“没按键”的情况下还在移动 = 上一次 pointerup 丢了（在手机区域外松手、
  // 浏览器丢掉了指针捕获、或在窗口外松开）：先用当前位置补一条 UP 收尾，
  // 否则设备端会一直保持按压、触点特效也停不下来。
  if (gestures.isDragging(event.pointerId) && event.pointerType !== 'touch' && event.buttons === 0) {
    handlePointerUp(event)
    return
  }
  touchEffectRef.value?.move(event.clientX, event.clientY)
  gestures.handlers.onPointerMove(event)
}

function handlePointerUp(event: PointerEvent): void {
  if (props.runLocked) return
  touchEffectRef.value?.up(event.clientX, event.clientY)
  gestures.handlers.onPointerUp(event)
}

function handlePointerCancel(event: PointerEvent): void {
  if (props.runLocked) return
  touchEffectRef.value?.cancel()
  gestures.handlers.onPointerCancel(event)
}

// pointerrawupdate（Chrome）不在 Vue 模板的事件类型里，手动绑定：拖动样本不对齐 rAF、即时下发。
// 没有 rawupdate 的浏览器不会触发，手势仍走 pointermove。用 passive：这里不做 preventDefault。
function handlePointerRawUpdate(event: Event): void {
  const pointerEvent = event as PointerEvent
  // 与 handlePointerMove 同一条兜底：按键已经抬起却还在移动 → 上一次 UP 丢了。
  if (
    gestures.isDragging(pointerEvent.pointerId) &&
    pointerEvent.pointerType !== 'touch' &&
    pointerEvent.buttons === 0
  ) {
    handlePointerUp(pointerEvent)
    return
  }
  gestures.handlers.onPointerRawUpdate(pointerEvent)
}

// 指针捕获被浏览器丢掉时，pointerup 会落到别的元素（黑色区域）上、画布收不到：
// 在 window 上再兜一层，保证每段手势都有 UP 收尾，设备端不会悬空按压。
function handleWindowPointerEnd(event: PointerEvent): void {
  if (event.type === 'pointercancel') handlePointerCancel(event)
  else handlePointerUp(event)
}

// 拖动中窗口失焦 / 页面切到后台（切应用、系统弹窗、Cmd-Tab）时浏览器可能再也不发 pointerup：
// 用最后位置补 UP，避免设备端停在按下状态。
function releaseOnFocusLoss(): void {
  if (!gestures.isDragging()) return
  touchEffectRef.value?.cancel()
  gestures.abortGesture()
}

function handleWindowBlur(): void {
  releaseOnFocusLoss()
}

function handleVisibilityChange(): void {
  if (document.hidden) releaseOnFocusLoss()
}

watch(canvasRef, (canvas, previous) => {
  previous?.removeEventListener('pointerrawupdate', handlePointerRawUpdate)
  canvas?.addEventListener('pointerrawupdate', handlePointerRawUpdate, { passive: true })
})

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && effectPopoverOpen.value) {
    event.preventDefault()
    event.stopPropagation()
    effectPopoverOpen.value = false
    return
  }
  keyboard.onKeyDown(event)
}

function refreshDevices(): void {
  devicesQuery.refetch()
  emit('refresh-devices')
}

function toggleMirror(): void {
  if (idle.value) {
    void session.startMirror()
  } else {
    void session.stopMirror()
  }
}

function refreshMirror(): void {
  refreshDevices()
  if (streaming.value) session.requestKeyframe()
  else if (!idle.value) session.reconnect()
}

function sendKeySilent(key: string): void {
  if (props.runLocked) return
  keyMutation.mutate({ key })
}

function toggleAllEffects(value: boolean): void {
  tapEffectEnabled.value = value
  swipeEffectEnabled.value = value
  rippleEnabled.value = value
}

defineExpose({
  getCanvas,
  getOverlayCanvas,
  startMirror: () => session.startMirror(),
  stopMirror: () => session.stopMirror(),
  reconnect: () => session.reconnect(),
  status: session.status,
  stats: session.stats,
  hello: session.hello,
})
</script>

<template>
  <div ref="rootRef" class="console-mirror" tabindex="0" @keydown="handleKeydown">
    <UnsupportedNotice v-if="!session.isSupported" />
    <template v-else>
      <MirrorDeviceHeader
        :serial="serial"
        :devices="devices"
        :selected-device="selectedDevice"
        :idle="idle"
        :starting="starting"
        @select-device="emit('select-device', $event)"
        @connect-device="emit('connect-device')"
        @refresh-devices="refreshDevices"
        @disconnect-device="emit('disconnect-device', $event)"
        @toggle-mirror="toggleMirror"
        @refresh-mirror="refreshMirror"
      />

      <div class="mirror-toolbar">
        <span class="spacer" />
        <div ref="effectMenuRef" class="effect-menu-wrap">
          <el-button
            size="small"
            text
            :aria-expanded="effectPopoverOpen"
            @click="effectPopoverOpen = !effectPopoverOpen"
          >
            特效
            <span class="count" :class="{ on: effectCount > 0 }">{{ effectCount }}</span>
          </el-button>
          <div v-if="effectPopoverOpen" class="effect-popover" role="dialog" aria-label="触控特效">
            <div class="effect-head">
              <strong>触控特效</strong>
              <small>仅影响镜像上的视觉反馈，不改变实际点击</small>
            </div>
            <div class="effect-row">
              <span class="effect-label"><strong>点击特效</strong><small>按下触点和长按呼吸环</small></span>
              <el-switch v-model="tapEffectEnabled" size="small" />
            </div>
            <div class="effect-row">
              <span class="effect-label"
                ><strong>波纹</strong><small>点击扩散水波，需开启点击特效</small></span
              >
              <el-switch v-model="rippleEnabled" size="small" :disabled="!tapEffectEnabled" />
            </div>
            <div class="effect-row">
              <span class="effect-label"><strong>滑动特效</strong><small>拖动轨迹和跟随终点圆点</small></span>
              <el-switch v-model="swipeEffectEnabled" size="small" />
            </div>
            <div class="effect-actions">
              <el-button size="small" text @click="toggleAllEffects(true)">全开</el-button>
              <el-button size="small" text @click="toggleAllEffects(false)">全关</el-button>
            </div>
          </div>
        </div>
        <el-tooltip content="请求编码器立即输出一个关键帧，用于恢复卡住的画面" placement="bottom">
          <el-button
            size="small"
            text
            :icon="VideoPlay"
            :disabled="!streaming"
            @click="session.requestKeyframe()"
          >
            请求关键帧
          </el-button>
        </el-tooltip>
      </div>

      <div v-if="visibleMismatch" class="mirror-config-alert">
        <span>{{ mismatchText }}</span>
        <span class="mirror-config-alert-actions">
          <el-button size="small" type="primary" :loading="restartingMirror" @click="restartMirrorForConfig">
            立即重启镜像
          </el-button>
          <el-button size="small" text @click="dismissMismatch">忽略</el-button>
        </span>
      </div>

      <div class="stage-wrap">
        <div class="mirror-stage console-stage" @click="focusPanel">
          <canvas
            ref="canvasRef"
            class="mirror-canvas"
            :class="{ 'is-locked': runLocked }"
            @pointerdown="handlePointerDown"
            @pointermove="handlePointerMove"
            @pointerup="handlePointerUp"
            @pointercancel="handlePointerCancel"
          />
          <TouchEffectOverlay
            ref="touchEffectRef"
            :enabled="(tapEffectEnabled || swipeEffectEnabled) && streaming && !runLocked"
            :tap-enabled="tapEffectEnabled"
            :swipe-enabled="swipeEffectEnabled"
            :ripple-enabled="rippleEnabled"
            :target="canvasRef"
          />
          <MirrorStatusOverlay
            :status="status"
            :error="session.error.value"
            :hello="session.hello.value"
            :has-frame="session.stats.value.decoded > 0"
            @start="session.startMirror()"
            @retry="session.reconnect()"
          />
          <div v-if="showFps" class="stage-fps" aria-live="off" :title="fpsHint">
            <span class="dot" :class="streaming ? 'live' : 'offline'" />
            <span class="mono">{{ fps }} fps</span>
          </div>
          <div
            v-if="runLocked || runPaused"
            class="stage-run-hint"
            :class="{ 'is-paused': runPaused && !runLocked }"
            role="status"
          >
            <el-icon :size="12">
              <Lock v-if="runLocked" />
              <VideoPause v-else />
            </el-icon>
            <span v-if="runLocked">脚本运行中 · 镜像已锁定，暂停后可手动干预</span>
            <span v-else>脚本已暂停 · 手动干预开启（操作会留痕）</span>
          </div>
        </div>

        <MirrorActionRail :locked="runLocked" @key="sendKeySilent" @screenshot="emit('screenshot')" />
      </div>

      <MirrorStatusLine
        :session="session"
        :pending-text="keyboard.pendingText.value"
        :busy="gestures.busy.value"
      />
      <div class="inline-error mirror-errorline" role="alert">{{ actionError ?? session.error.value ?? '' }}</div>
    </template>
  </div>
</template>

<style scoped>
/* 控制台镜像卡：头部 / 工具栏 / 舞台 / 动作栏 / 状态行，密度对齐 v0.2.5 视觉稿。 */
.console-mirror {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
  outline: none;
}

.console-mirror:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.mirror-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
  flex-wrap: wrap;
  min-height: 34px;
  padding: 4px 12px;
  border-bottom: 1px solid var(--line);
  color: var(--muted);
  font-size: 11px;
}

.effect-menu-wrap {
  position: relative;
  flex: none;
}

.effect-menu-wrap .count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 16px;
  margin-left: 4px;
  padding: 0 4px;
  border-radius: 8px;
  background: var(--surface-3);
  color: var(--muted);
  font-family: var(--mono);
  font-size: 9px;
  font-weight: 600;
}

.effect-menu-wrap .count.on {
  background: var(--accent-soft);
  color: var(--accent);
}

.effect-popover {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 45;
  width: 248px;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: var(--surface);
  box-shadow: var(--shadow-md);
}

.effect-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 7px;
}

.effect-head strong {
  color: var(--ink);
  font-size: 11.5px;
  font-weight: 650;
}

.effect-head small {
  color: var(--muted-2);
  font-size: 9.5px;
}

.effect-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 0;
  border-bottom: 1px solid var(--line-soft);
}

.effect-row:last-of-type {
  border-bottom: 0;
}

.effect-label {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.effect-label strong {
  color: var(--ink);
  font-size: 11px;
  font-weight: 550;
}

.effect-label small {
  color: var(--muted-2);
  font-size: 9px;
  line-height: 1.4;
}

.effect-actions {
  display: flex;
  gap: 5px;
  margin-top: 7px;
}

.effect-actions .el-button {
  flex: 1;
}

.mirror-config-alert {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex: none;
  flex-wrap: wrap;
  margin: 8px 8px 0;
  padding: 7px 9px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--amber-soft);
  color: var(--amber);
  font-size: 11px;
}

.mirror-config-alert-actions {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex: none;
}

/* 说明文案允许换行，按钮不被挤走 */
.mirror-config-alert > span:first-child {
  flex: 1;
  min-width: 180px;
}

.stage-wrap {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 58px;
  grid-template-rows: minmax(0, 1fr);
  flex: 1;
  min-height: 0;
  margin: 8px 8px 0;
  overflow: hidden;
  border: 1px solid #232b36;
  border-radius: 12px;
  background: #0a0c10;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.015);
}

.console-stage {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border: 0;
  border-radius: 0;
  background: radial-gradient(ellipse at 50% 20%, rgba(61, 78, 101, 0.22), transparent 58%), #0a0c10;
  box-shadow: none;
}

.mirror-canvas {
  max-width: 100%;
  max-height: 100%;
}

/* 脚本运行中：镜像锁定，鼠标显示禁止。 */
.mirror-canvas.is-locked {
  cursor: not-allowed;
}

.stage-run-hint {
  position: absolute;
  bottom: 12px;
  left: 50%;
  z-index: 5;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 12px;
  background: rgba(10, 14, 20, 0.94);
  color: #f8fafc;
  font-size: 10.5px;
  white-space: nowrap;
  pointer-events: none;
  transform: translateX(-50%);
}

.stage-run-hint.is-paused {
  border-color: var(--amber);
  color: var(--amber);
}

.stage-fps {
  position: absolute;
  top: 10px;
  left: 10px;
  z-index: 4;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  padding: 0 10px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 12px;
  background: rgba(10, 14, 20, 0.94);
  color: #f8fafc;
  font-family: var(--mono);
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.2px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
  backdrop-filter: blur(6px);
  pointer-events: none;
}

.stage-fps .dot {
  width: 6px;
  height: 6px;
}

.stage-fps .dot.live {
  background: #4ade80;
  box-shadow: 0 0 0 3px rgba(74, 222, 128, 0.16);
}

.stage-fps .mono {
  color: #f8fafc;
  font-size: 10.5px;
  font-weight: 600;
}

.console-mirror :deep(.mirror-statusline) {
  flex: none;
  height: auto;
  min-height: 28px;
  padding: 0 12px;
  color: var(--muted);
  font-size: 10.5px;
}

.console-mirror :deep(.mirror-errorline) {
  flex: none;
  padding: 0 12px;
  font-size: 10.5px;
}

.console-mirror :deep(.mirror-placeholder-title) {
  font-size: 14px;
}

.console-mirror :deep(.mirror-placeholder-hint) {
  max-width: 330px;
  font-size: 11.5px;
}

@media (max-width: 640px) {
  .mirror-toolbar {
    padding: 4px 8px;
  }

  .stage-wrap {
    grid-template-columns: minmax(0, 1fr) 52px;
    margin: 6px 6px 0;
  }

  .effect-popover {
    right: -4px;
    width: 236px;
  }
}

</style>
