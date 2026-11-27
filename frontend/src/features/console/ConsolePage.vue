<script setup lang="ts">
import { Link, MagicStick, Monitor, Refresh, Tools } from '@element-plus/icons-vue'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQuery } from 'vue-router'

import { useDevices } from '@/api/devices'
import { useSettings } from '@/api/settings'
import type { Device } from '@/types/api'
import { useUiStore } from '@/stores/ui'
import AppTabs from '@/components/AppTabs.vue'
import EmptyState from '@/components/EmptyState.vue'
import type { AppTabItem } from '@/components/appTabs'

import DeviceConnectDialog from '@/features/devices/DeviceConnectDialog.vue'
import DeviceHierarchyDrawer from '@/features/devices/DeviceHierarchyDrawer.vue'
import DeviceLogcatDrawer from '@/features/devices/DeviceLogcatDrawer.vue'
import DeviceNetworkDrawer from '@/features/devices/DeviceNetworkDrawer.vue'
import ScreenshotDialog from '@/features/devices/ScreenshotDialog.vue'
import type { MirrorUiStatus } from '@/features/mirror/types'

import AutoPanel from './AutoPanel.vue'
import ManualPanel from './ManualPanel.vue'
import MirrorStage from './MirrorStage.vue'

interface MirrorStageHandle {
  stopMirror: () => Promise<void>
  getCanvas: () => HTMLCanvasElement | null
  getOverlayCanvas: () => HTMLCanvasElement | null
}

const route = useRoute()
const router = useRouter()
const ui = useUiStore()

const devicesQuery = useDevices({ refetchInterval: 5000 })
const settingsQuery = useSettings()

const devices = computed<Device[]>(() => devicesQuery.data.value ?? [])

const token = computed(() => {
  const value = settingsQuery.data.value?.effective?.['security.token']
  return typeof value === 'string' ? value : ''
})

const selectedSerial = computed(() => ui.selectedSerial)
const selectedDevice = computed<Device | null>(
  () => devices.value.find((device) => device.serial === ui.selectedSerial) ?? null,
)

const mirrorRef = ref<MirrorStageHandle | null>(null)
const mirrorStatus = ref<MirrorUiStatus>('idle')
const switchingDevice = ref(false)
const streaming = computed(() => mirrorStatus.value === 'streaming')

const connectOpen = ref(false)
const screenshotOpen = ref(false)
const hierarchyOpen = ref(false)
const logcatOpen = ref(false)
const networkOpen = ref(false)

const autoRunning = ref(false)
const autoPaused = ref(false)

const consoleTabs = computed<AppTabItem[]>(() => [
  { key: 'manual', label: '手动控制', icon: Tools },
  {
    key: 'auto',
    label: '自动控制',
    icon: MagicStick,
    badge: autoRunning.value
      ? { text: autoPaused.value ? '已暂停' : '运行中', tone: autoPaused.value ? 'amber' : 'green' }
      : undefined,
  },
])

function setConsoleTab(key: string): void {
  if (key === 'manual' || key === 'auto') ui.setConsoleTab(key)
}

/** 设备列表变化时保持当前选择；失效或首次加载时落到第一台设备。 */
watch(
  devices,
  (list) => {
    if (list.length === 0) {
      if (ui.selectedSerial !== null) ui.setSelectedSerial(null)
      return
    }
    if (!ui.selectedSerial || !list.some((device) => device.serial === ui.selectedSerial)) {
      ui.setSelectedSerial(list[0].serial)
    }
  },
  { immediate: true },
)

const QUERY_KEYS = ['tab', 'manual', 'script'] as const

function applyQueryState(query: LocationQuery): boolean {
  let consumed = false
  const tab = query.tab
  if (tab === 'manual' || tab === 'auto') {
    ui.setConsoleTab(tab)
    consumed = true
  }
  const manual = query.manual
  if (
    manual === 'video' ||
    manual === 'record' ||
    manual === 'text' ||
    manual === 'app' ||
    manual === 'tools'
  ) {
    ui.setManualTab(manual)
    consumed = true
  }
  const script = query.script
  if (typeof script === 'string' && script.trim()) {
    ui.setPendingRunScriptId(script)
    if (tab !== 'manual') ui.setConsoleTab('auto')
    consumed = true
  }
  return consumed
}

/** 读取脚本页/旧链接带来的 query，然后从地址栏清掉，避免刷新后重复触发。 */
watch(
  () => route.query,
  (query) => {
    if (!applyQueryState(query)) return
    const rest: LocationQuery = { ...query }
    for (const key of QUERY_KEYS) delete rest[key]
    void router.replace({ path: route.path, query: rest, hash: route.hash })
  },
  { immediate: true },
)

watch(selectedDevice, (device) => {
  if (device) return
  screenshotOpen.value = false
  hierarchyOpen.value = false
  logcatOpen.value = false
  networkOpen.value = false
})

async function selectDevice(serial: string): Promise<void> {
  if (serial === ui.selectedSerial || switchingDevice.value) return
  switchingDevice.value = true
  try {
    // 先停在旧设备上的镜像，再切 serial；MirrorStage 用 serial 作 key，会重新挂载。
    // 不只看 streaming：重连/出错状态下后端可能仍占着旧设备的 scrcpy，也要收尾。
    if (mirrorStatus.value !== 'idle') {
      try {
        await mirrorRef.value?.stopMirror()
      } catch {
        // 停止失败不阻塞切换，父组件卸载时 useMirrorSession 仍会清理会话。
      }
    }
    ui.setSelectedSerial(serial)
    mirrorStatus.value = 'idle'
  } finally {
    switchingDevice.value = false
  }
}

function refreshDevices(): void {
  void devicesQuery.refetch()
}

function handleMirrorStatus(status: MirrorUiStatus): void {
  mirrorStatus.value = status
}

function handleAutoRunningChange(active: boolean, paused: boolean): void {
  autoRunning.value = active
  autoPaused.value = paused
}

function getMirrorCanvas(): HTMLCanvasElement | null {
  return mirrorRef.value?.getCanvas() ?? null
}

function getMirrorOverlayCanvas(): HTMLCanvasElement | null {
  return mirrorRef.value?.getOverlayCanvas() ?? null
}

function openScreenshot(): void {
  if (!selectedDevice.value) return
  screenshotOpen.value = true
}

function openHierarchy(): void {
  if (!selectedDevice.value) return
  hierarchyOpen.value = true
}

function openLogcat(): void {
  if (!selectedDevice.value) return
  logcatOpen.value = true
}

function openNetwork(): void {
  if (!selectedDevice.value) return
  networkOpen.value = true
}

function openConnect(): void {
  connectOpen.value = true
}
</script>

<template>
  <div class="console-page">
    <div class="console-grid">
      <section class="card console-mirror-card">
        <MirrorStage
          v-if="selectedDevice"
          ref="mirrorRef"
          :key="selectedDevice.serial"
          :serial="selectedDevice.serial"
          :token="token"
          :devices="devices"
          :selected-device="selectedDevice"
          :run-locked="autoRunning && !autoPaused"
          :run-paused="autoPaused"
          @select-device="selectDevice"
          @connect-device="openConnect"
          @refresh-devices="refreshDevices"
          @disconnect-device="refreshDevices"
          @status="handleMirrorStatus"
          @screenshot="openScreenshot"
        />

        <div v-else-if="devicesQuery.isPending.value" class="mirror-loading">
          <el-skeleton :rows="5" animated style="width: min(420px, 100%)" />
        </div>

        <EmptyState
          v-else
          :icon="Monitor"
          title="还没有可用的设备"
          description="用 USB 连接并授权调试，或连接一台网络设备后开始镜像与手动控制。"
        >
          <template #extra>
            <p class="empty-note">手机已连接但仍看不到设备？请在「设置 → 工具链」检查 adb 路径与版本。</p>
            <span v-if="devicesQuery.isError.value" class="empty-error">
              设备列表加载失败，请确认后端服务已启动。
            </span>
          </template>
          <template #actions>
            <el-button type="primary" :icon="Link" @click="openConnect">连接设备</el-button>
            <el-button :icon="Refresh" :loading="devicesQuery.isFetching.value" @click="refreshDevices">
              刷新设备
            </el-button>
          </template>
        </EmptyState>
      </section>

      <aside class="card dock">
        <AppTabs
          :model-value="ui.consoleTab"
          :items="consoleTabs"
          variant="dock"
          aria-label="控制台功能"
          @update:model-value="setConsoleTab"
        />

        <div class="dock-body">
          <div v-show="ui.consoleTab === 'manual'" class="dock-pane">
            <ManualPanel
              v-if="selectedDevice"
              :key="selectedDevice.serial"
              :serial="selectedDevice.serial"
              :device="selectedDevice"
              :streaming="streaming"
              :get-source="getMirrorCanvas"
              :get-overlay="getMirrorOverlayCanvas"
              @hierarchy="openHierarchy"
              @logcat="openLogcat"
              @network="openNetwork"
              @refresh-devices="refreshDevices"
            />
            <EmptyState
              v-else
              compact
              :icon="Tools"
              title="手动控制不可用"
              description="选择或连接一台设备后，可在这里录制、输入文本和拉起应用。"
            >
              <template #actions>
                <el-button size="small" :icon="Link" @click="openConnect">连接设备</el-button>
              </template>
            </EmptyState>
          </div>

          <div v-show="ui.consoleTab === 'auto'" class="dock-pane">
            <AutoPanel
              :serial="selectedDevice?.serial ?? null"
              :device="selectedDevice"
              :devices-ready="!devicesQuery.isPending.value"
              @running-change="handleAutoRunningChange"
            />
          </div>
        </div>
      </aside>
    </div>

    <DeviceConnectDialog :open="connectOpen" @close="connectOpen = false" @connected="refreshDevices" />
    <ScreenshotDialog
      :serial="selectedDevice?.serial ?? null"
      :open="screenshotOpen"
      @close="screenshotOpen = false"
    />
    <DeviceHierarchyDrawer
      :serial="selectedDevice?.serial ?? null"
      :open="hierarchyOpen"
      @close="hierarchyOpen = false"
    />
    <DeviceLogcatDrawer
      :serial="selectedDevice?.serial ?? null"
      :open="logcatOpen"
      @close="logcatOpen = false"
    />
    <DeviceNetworkDrawer
      :serial="selectedDevice?.serial ?? null"
      :open="networkOpen"
      @close="networkOpen = false"
    />
  </div>
</template>

<style scoped>
.console-page {
  min-width: 0;
}

.console-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 384px;
  gap: 12px;
  align-items: stretch;
  height: calc(100vh - var(--top) - 40px);
  min-height: 620px;
}

.console-mirror-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.mirror-loading {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  min-height: 0;
  padding: 28px;
}

.empty-note {
  max-width: 360px;
  margin: 0;
  color: var(--muted);
  font-size: 11.5px;
  line-height: 1.55;
}

.empty-error {
  color: var(--red);
  font-size: 10.5px;
}

.dock {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.dock-body {
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.dock-pane {
  display: flex;
  flex: 1;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
}

@media (max-width: 1200px) {
  .console-grid {
    grid-template-columns: minmax(0, 1fr);
    height: auto;
    min-height: 0;
  }

  .console-mirror-card {
    height: 620px;
  }

  .dock {
    height: 620px;
    max-height: none;
  }

  .dock-body {
    max-height: none;
  }
}

@media (max-width: 900px) {
  .console-mirror-card {
    height: 580px;
  }

  .dock {
    height: 560px;
  }

  .dock-body {
    max-height: none;
  }

  .dock-pane {
    min-height: 0;
  }
}

@media (max-width: 640px) {
  .console-grid {
    gap: 8px;
  }

  .console-mirror-card {
    height: 540px;
  }

  .dock {
    height: 520px;
  }
}
</style>
