<script setup lang="ts">
import {
  ArrowDown,
  Back,
  Bell,
  Camera,
  ChatDotRound,
  Check,
  Close,
  Delete,
  Grid,
  HomeFilled,
  Link,
  Menu as MenuIcon,
  MoreFilled,
  Promotion,
  Refresh,
  SwitchButton,
  TurnOff,
  VideoPlay,
} from '@element-plus/icons-vue'
import { onClickOutside } from '@vueuse/core'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useDevices, useDisconnectDevice, useDeviceKey } from '@/api/devices'
import { useSettings } from '@/api/settings'
import type { Device } from '@/types/api'

import { useKeyboardInput } from '../mirror/composables/useKeyboardInput'
import { useMirrorSession } from '../mirror/composables/useMirrorSession'
import { usePointerGestures } from '../mirror/composables/usePointerGestures'
import type { MirrorUiStatus } from '../mirror/types'
import MirrorStatusLine from '../mirror/components/MirrorStatusLine.vue'
import MirrorStatusOverlay from '../mirror/components/MirrorStatusOverlay.vue'
import TouchEffectOverlay from '../mirror/components/TouchEffectOverlay.vue'
import UnsupportedNotice from '../mirror/components/UnsupportedNotice.vue'

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
  }>(),
  { token: undefined },
)

const emit = defineEmits<{
  'select-device': [serial: string]
  'connect-device': []
  'refresh-devices': []
  'disconnect-device': [device: Device]
  status: [status: MirrorUiStatus]
  screenshot: []
  hierarchy: []
  logcat: []
}>()

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

const rootRef = ref<HTMLDivElement | null>(null)
const canvasRef = ref<HTMLCanvasElement | null>(null)
const touchEffectRef = ref<TouchEffectHandle | null>(null)
const deviceMenuRef = ref<HTMLDivElement | null>(null)
const effectMenuRef = ref<HTMLDivElement | null>(null)
const moreMenuRef = ref<HTMLDivElement | null>(null)
const deviceMenuOpen = ref(false)
const effectPopoverOpen = ref(false)
const moreMenuOpen = ref(false)

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
const keyMutation = useDeviceKey(props.serial)
const devicesQuery = useDevices({ refetchInterval: 5000 })
const disconnectMutation = useDisconnectDevice()

watch(
  () => session.status.value,
  (status) => emit('status', status),
  { immediate: true },
)

// 设置变更后当前镜像仍在旧参数上跑：提示并允许一键重启。
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

watch(actionError, (value) => {
  if (errorTimer !== null) clearTimeout(errorTimer)
  if (!value) return
  errorTimer = setTimeout(() => {
    actionError.value = null
    errorTimer = null
  }, 5000)
})

watch(tapEffectEnabled, (value) => writeFlag(TOUCH_EFFECT_STORAGE_KEY, value))
watch(swipeEffectEnabled, (value) => writeFlag(TOUCH_SWIPE_STORAGE_KEY, value))
watch(rippleEnabled, (value) => writeFlag(TOUCH_RIPPLE_STORAGE_KEY, value))

watch(
  () => session.status.value,
  (status) => {
    if (status !== 'streaming') touchEffectRef.value?.clear()
  },
)

onClickOutside(deviceMenuRef, () => {
  deviceMenuOpen.value = false
})
onClickOutside(effectMenuRef, () => {
  effectPopoverOpen.value = false
})
onClickOutside(moreMenuRef, () => {
  moreMenuOpen.value = false
})

onMounted(() => {
  // 首次挂载时把状态同步给父组件（watch immediate 可能早于父组件监听）。
  emit('status', session.status.value)
})

onBeforeUnmount(() => {
  if (errorTimer !== null) clearTimeout(errorTimer)
})

const status = computed(() => session.status.value)
const streaming = computed(() => status.value === 'streaming')
const idle = computed(() => status.value === 'idle')
const starting = computed(() => status.value === 'starting' || status.value === 'checking')
const resolution = computed(() => {
  const hello = session.hello.value
  if (hello && hello.width > 0 && hello.height > 0) return `${hello.width} × ${hello.height}`
  const device = props.selectedDevice
  if (device && device.screen_width > 0 && device.screen_height > 0) {
    return `${device.screen_width} × ${device.screen_height}`
  }
  return '分辨率 —'
})
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
const effectCount = computed(
  () =>
    (tapEffectEnabled.value ? 1 : 0) +
    (tapEffectEnabled.value && rippleEnabled.value ? 1 : 0) +
    (swipeEffectEnabled.value ? 1 : 0),
)
const fps = computed(() => session.stats.value.fps ?? 0)
const showFps = computed(() => status.value !== 'idle')

const MORE_KEYS = [
  { key: 'menu', label: '菜单', icon: MenuIcon },
  { key: 'notification', label: '通知', icon: Bell },
  { key: 'volume_up', label: '音量+', icon: Promotion },
  { key: 'volume_down', label: '音量−', icon: Promotion },
  { key: 'power', label: '电源', icon: TurnOff },
  { key: 'enter', label: '回车', icon: ChatDotRound },
  { key: 'delete', label: '删除', icon: Delete },
]

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

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && (deviceMenuOpen.value || effectPopoverOpen.value || moreMenuOpen.value)) {
    event.preventDefault()
    event.stopPropagation()
    deviceMenuOpen.value = false
    effectPopoverOpen.value = false
    moreMenuOpen.value = false
    return
  }
  keyboard.onKeyDown(event)
}

function toggleDeviceMenu(): void {
  deviceMenuOpen.value = !deviceMenuOpen.value
  effectPopoverOpen.value = false
  moreMenuOpen.value = false
}

function connectFromMenu(): void {
  deviceMenuOpen.value = false
  emit('connect-device')
}

function selectDevice(serial: string): void {
  if (serial === props.serial) {
    deviceMenuOpen.value = false
    return
  }
  deviceMenuOpen.value = false
  emit('select-device', serial)
}

function closeAndRefreshDevices(): void {
  deviceMenuOpen.value = false
  devicesQuery.refetch()
  emit('refresh-devices')
}

async function disconnectCurrent(): Promise<void> {
  deviceMenuOpen.value = false
  const device = props.selectedDevice
  if (!device) return
  try {
    await ElMessageBox.confirm(`断开 ${device.serial}？`, '断开网络设备', {
      type: 'warning',
      confirmButtonText: '断开',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await disconnectMutation.mutateAsync(device.serial)
    ElMessage.success(`已断开 ${device.serial}`)
    emit('disconnect-device', device)
    closeAndRefreshDevices()
  } catch {
    // 全局错误提示
  }
}

function toggleMirror(): void {
  if (idle.value) {
    void session.startMirror()
  } else {
    void session.stopMirror()
  }
}

function refreshMirror(): void {
  closeAndRefreshDevices()
  if (streaming.value) session.requestKeyframe()
  else if (!idle.value) session.reconnect()
}

function sendKey(key: string): void {
  keyMutation.mutate({ key })
  moreMenuOpen.value = false
}

function sendKeySilent(key: string): void {
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
      <header class="mirror-head">
        <div ref="deviceMenuRef" class="device-dropdown">
          <button
            type="button"
            class="device-btn"
            :aria-expanded="deviceMenuOpen"
            aria-haspopup="menu"
            @click="toggleDeviceMenu"
          >
            <span
              class="dot"
              :class="selectedDevice ? (selectedDevice.busy ? 'busy' : 'online') : 'offline'"
            />
            <span class="device-name">{{ deviceName }}</span>
            <span class="device-serial">{{ serial }}</span>
            <el-icon class="chev" :size="12"><ArrowDown /></el-icon>
          </button>
          <div v-if="deviceMenuOpen" class="menu" role="menu">
            <div class="menu-label">当前设备 · {{ deviceStateText }}</div>
            <button
              v-for="device in devices"
              :key="device.serial"
              type="button"
              :class="['device-row', { active: device.serial === serial }]"
              role="menuitem"
              @click="selectDevice(device.serial)"
            >
              <span
                class="dot"
                :class="device.state === 'device' ? (device.busy ? 'busy' : 'online') : 'offline'"
              />
              <span class="device-row-main">
                <strong>{{ [device.brand, device.model].filter(Boolean).join(' ') || device.serial }}</strong>
                <small>{{ device.serial }} · {{ device.screen_width }}×{{ device.screen_height }}</small>
              </span>
              <el-icon v-if="device.serial === serial" class="row-check" :size="13"><Check /></el-icon>
            </button>
            <div v-if="devices.length === 0" class="menu-empty">暂无在线设备</div>
            <div class="menu-sep" />
            <button type="button" class="menu-item" @click="connectFromMenu">
              <el-icon><Link /></el-icon>连接设备…
            </button>
            <button type="button" class="menu-item" @click="closeAndRefreshDevices">
              <el-icon><Refresh /></el-icon>刷新设备
            </button>
            <button
              v-if="serial.includes(':')"
              type="button"
              class="menu-item danger"
              @click="disconnectCurrent"
            >
              <el-icon><Close /></el-icon>断开当前网络设备
            </button>
          </div>
        </div>

        <div class="mirror-meta">
          <span class="mono">{{ resolution }}</span>
          <span v-if="selectedDevice?.rotation" class="hint">{{ selectedDevice.rotation }}°</span>
        </div>

        <span class="spacer" />

        <el-button
          :type="idle ? 'primary' : 'danger'"
          size="small"
          :icon="SwitchButton"
          :loading="starting"
          @click="toggleMirror"
        >
          {{ idle ? '启动镜像' : '停止镜像' }}
        </el-button>
        <el-tooltip content="重新连接镜像并刷新设备状态" placement="bottom">
          <el-button size="small" :icon="Refresh" circle @click="refreshMirror" />
        </el-tooltip>
      </header>

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

      <div v-if="configMismatch" class="mirror-config-alert">
        <span>画质设置已变更（{{ configMismatch }}），当前镜像仍在用旧参数；重启镜像后生效。</span>
        <el-button size="small" type="primary" :loading="restartingMirror" @click="restartMirrorForConfig">
          立即重启镜像
        </el-button>
      </div>

      <div class="stage-wrap">
        <div class="mirror-stage console-stage" @click="focusPanel">
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
            :enabled="(tapEffectEnabled || swipeEffectEnabled) && streaming"
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
          <div v-if="showFps" class="stage-fps" aria-live="off">
            <span class="dot" :class="streaming ? 'live' : 'offline'" />
            <span class="mono">{{ fps }} fps</span>
          </div>
        </div>

        <aside class="action-rail">
          <button type="button" class="rail-btn" title="返回" @click="sendKeySilent('back')">
            <el-icon :size="16"><Back /></el-icon><span>返回</span>
          </button>
          <button type="button" class="rail-btn" title="桌面" @click="sendKeySilent('home')">
            <el-icon :size="16"><HomeFilled /></el-icon><span>桌面</span>
          </button>
          <button type="button" class="rail-btn" title="最近任务" @click="sendKeySilent('recents')">
            <el-icon :size="16"><Grid /></el-icon><span>最近</span>
          </button>
          <button type="button" class="rail-btn accent" title="截图" @click="emit('screenshot')">
            <el-icon :size="16"><Camera /></el-icon><span>截图</span>
          </button>
          <div class="rail-divider" />
          <div ref="moreMenuRef" class="more-wrap">
            <button
              type="button"
              class="rail-btn"
              :aria-expanded="moreMenuOpen"
              title="更多系统按键"
              @click="moreMenuOpen = !moreMenuOpen"
            >
              <el-icon :size="16"><MoreFilled /></el-icon><span>更多</span>
            </button>
            <div v-if="moreMenuOpen" class="more-menu">
              <div class="more-title">系统按键</div>
              <div class="more-grid">
                <button
                  v-for="item in MORE_KEYS"
                  :key="item.key"
                  type="button"
                  class="more-key"
                  @click="sendKey(item.key)"
                >
                  <el-icon :size="15"><component :is="item.icon" /></el-icon>
                  {{ item.label }}
                </button>
              </div>
            </div>
          </div>
        </aside>
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

.mirror-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
  min-height: 46px;
  padding: 0 12px;
  border-bottom: 1px solid var(--line);
}

.device-dropdown {
  position: relative;
  flex: none;
  max-width: min(330px, 48%);
}

.device-btn {
  display: flex;
  align-items: center;
  gap: 7px;
  max-width: 100%;
  height: 30px;
  padding: 0 9px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface);
  color: var(--ink);
  font-size: 12px;
  cursor: pointer;
}

.device-btn:hover {
  background: var(--surface-2);
}

.device-btn .device-name {
  min-width: 0;
  overflow: hidden;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.device-btn .device-serial {
  flex: none;
  color: var(--muted-2);
  font-family: var(--mono);
  font-size: 10.5px;
  white-space: nowrap;
}

.device-btn .chev {
  flex: none;
  color: var(--muted-2);
}

.menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 70;
  width: 310px;
  max-width: min(360px, calc(100vw - 40px));
  padding: 6px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  box-shadow: var(--shadow-md);
}

.menu-label {
  padding: 5px 8px 6px;
  color: var(--muted-2);
  font-size: 10.5px;
}

.device-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  padding: 7px 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}

.device-row:hover {
  background: var(--surface-2);
}

.device-row.active {
  background: var(--accent-soft);
}

.device-row-main {
  flex: 1;
  min-width: 0;
}

.device-row-main strong {
  display: block;
  overflow: hidden;
  font-size: 12px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.device-row-main small {
  display: block;
  overflow: hidden;
  color: var(--muted);
  font-family: var(--mono);
  font-size: 10.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.device-row .row-check {
  flex: none;
  margin-top: 2px;
  color: var(--accent);
  opacity: 0;
}

.device-row.active .row-check {
  opacity: 1;
}

.menu-empty {
  padding: 8px;
  color: var(--muted-2);
  font-size: 11px;
  text-align: center;
}

.menu-sep {
  height: 1px;
  margin: 5px 4px;
  background: var(--line);
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 30px;
  padding: 0 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}

.menu-item:hover {
  background: var(--surface-2);
  color: var(--ink);
}

.menu-item.danger {
  color: var(--red);
}

.menu-item.danger:hover {
  background: var(--red-soft);
  color: var(--red);
}

.mirror-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
  color: var(--muted);
  font-size: 11.5px;
  white-space: nowrap;
}

.mirror-meta .mono {
  color: var(--ink);
}

.mirror-head > .spacer {
  flex: 1;
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

.action-rail {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  overflow: visible;
  padding: 8px 0;
  border-left: 1px solid #232b36;
  background: #111820;
}

.rail-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  width: 46px;
  height: 43px;
  padding: 0;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #8494a8;
  font-size: 9.5px;
  line-height: 1;
  cursor: pointer;
}

.rail-btn:hover {
  background: rgba(255, 255, 255, 0.07);
  color: #f0f6ff;
}

.rail-btn.accent {
  color: #7fb0ff;
}

.rail-divider {
  width: 24px;
  height: 1px;
  margin: 3px 0;
  background: #293342;
}

.more-wrap {
  position: relative;
}

.more-menu {
  position: absolute;
  right: calc(100% + 8px);
  bottom: 0;
  z-index: 45;
  width: 238px;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: var(--surface);
  box-shadow: var(--shadow-md);
}

.more-title {
  padding: 3px 4px 6px;
  color: var(--muted-2);
  font-size: 10px;
}

.more-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 4px;
}

.more-key {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  height: 43px;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface);
  color: var(--muted);
  font-size: 9.5px;
  cursor: pointer;
}

.more-key:hover {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
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

@media (max-width: 900px) {
  .device-dropdown {
    max-width: min(300px, 60%);
  }
}

@media (max-width: 640px) {
  .mirror-head {
    min-height: 0;
    padding: 6px 8px;
    gap: 6px;
    flex-wrap: wrap;
  }

  .device-dropdown {
    max-width: 100%;
  }

  .device-btn {
    height: 28px;
  }

  .menu {
    width: min(310px, calc(100vw - 96px));
  }

  .device-btn .device-serial {
    display: none;
  }

  .mirror-meta {
    order: 2;
    font-size: 10.5px;
  }

  .mirror-head > .spacer {
    display: none;
  }

  .mirror-head > .el-button:first-of-type {
    margin-left: auto;
  }

  .mirror-toolbar {
    padding: 4px 8px;
  }

  .stage-wrap {
    grid-template-columns: minmax(0, 1fr) 52px;
    margin: 6px 6px 0;
  }

  .action-rail {
    padding: 6px 0;
  }

  .rail-btn {
    width: 42px;
    height: 40px;
    font-size: 9px;
  }

  .more-menu {
    right: calc(100% + 5px);
    width: 210px;
  }

  .effect-popover {
    right: -4px;
    width: 236px;
  }
}
</style>
