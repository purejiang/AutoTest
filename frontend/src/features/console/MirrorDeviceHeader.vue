<script setup lang="ts">
// 镜像卡头部：设备下拉（含连接/刷新/断开）+ 旋转角 + 启动/停止/重连。
// 菜单状态与 Escape/外部点击关闭都在组件内部，父组件只处理业务动作。
import { ArrowDown, Check, Close, Link, Refresh, SwitchButton } from '@element-plus/icons-vue'
import { onClickOutside, useEventListener } from '@vueuse/core'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, ref } from 'vue'

import { useDisconnectDevice } from '@/api/devices'
import type { Device } from '@/types/api'

const props = defineProps<{
  serial: string
  devices: Device[]
  selectedDevice: Device | null
  idle: boolean
  starting: boolean
}>()

const emit = defineEmits<{
  'select-device': [serial: string]
  'connect-device': []
  'refresh-devices': []
  'disconnect-device': [device: Device]
  'toggle-mirror': []
  'refresh-mirror': []
}>()

const menuRef = ref<HTMLDivElement | null>(null)
const menuOpen = ref(false)
const disconnectMutation = useDisconnectDevice()

const deviceName = computed(() => {
  const device = props.selectedDevice
  if (!device) return '未选择设备'
  return [device.brand, device.model].filter(Boolean).join(' ') || device.serial
})

const deviceStateText = computed(() => {
  const device = props.selectedDevice
  if (!device) return '未选择'
  if (device.busy) return `占用中 · ${device.busy_reason ?? '运行/录制'}`
  if (device.state !== 'device') return device.state
  return '在线'
})

onClickOutside(menuRef, () => {
  menuOpen.value = false
})
useEventListener(window, 'keydown', (event: KeyboardEvent) => {
  if (event.key === 'Escape' && menuOpen.value) {
    event.stopPropagation()
    menuOpen.value = false
  }
})

function toggleMenu(): void {
  menuOpen.value = !menuOpen.value
}

function connectFromMenu(): void {
  menuOpen.value = false
  emit('connect-device')
}

function selectDevice(serial: string): void {
  menuOpen.value = false
  if (serial === props.serial) return
  emit('select-device', serial)
}

function closeAndRefreshDevices(): void {
  menuOpen.value = false
  emit('refresh-devices')
}

async function disconnectCurrent(): Promise<void> {
  menuOpen.value = false
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
    emit('refresh-devices')
  } catch {
    // 全局错误提示
  }
}

function toggleMirror(): void {
  emit('toggle-mirror')
}

function refreshMirror(): void {
  menuOpen.value = false
  emit('refresh-mirror')
}
</script>

<template>
<header class="mirror-head">
  <div ref="menuRef" class="device-dropdown">
    <button
      type="button"
      class="device-btn"
      :aria-expanded="menuOpen"
      aria-haspopup="menu"
      @click="toggleMenu"
    >
      <span
        class="dot"
        :class="selectedDevice ? (selectedDevice.busy ? 'busy' : 'online') : 'offline'"
      />
      <span class="device-name">{{ deviceName }}</span>
      <span class="device-serial">{{ serial }}</span>
      <el-icon class="chev" :size="12"><ArrowDown /></el-icon>
    </button>
    <div v-if="menuOpen" class="menu" role="menu">
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

  <div v-if="selectedDevice?.rotation" class="mirror-meta">
    <span class="hint">{{ selectedDevice.rotation }}°</span>
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
</header></template>

<style scoped>
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
}
</style>
