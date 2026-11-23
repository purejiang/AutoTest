<script setup lang="ts">
import { Camera, Connection, Document, Link, Refresh, Tickets } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, ref, watch } from 'vue'

import { describeApiError } from '@/api/client'
import { useConnectDevice, useDevices, useDisconnectDevice } from '@/api/devices'
import { useSettings } from '@/api/settings'
import { useUiStore } from '@/stores/ui'
import type { Device } from '@/types/api'
import PageHead from '@/components/PageHead.vue'
import StatusTag from '@/components/StatusTag.vue'
import MirrorPanel from '@/features/mirror/MirrorPanel.vue'

import DeviceControlPanel from './DeviceControlPanel.vue'
import DeviceHierarchyDrawer from './DeviceHierarchyDrawer.vue'
import DeviceLogcatDrawer from './DeviceLogcatDrawer.vue'
import ScreenshotDialog from './ScreenshotDialog.vue'

const ui = useUiStore()
const devicesQuery = useDevices({ refetchInterval: 5000 })
const settingsQuery = useSettings()
const connectMutation = useConnectDevice()
const disconnectMutation = useDisconnectDevice()

const connectOpen = ref(false)
const address = ref('')
const connectError = ref('')

const screenshotSerial = ref<string | null>(null)
const hierarchySerial = ref<string | null>(null)
const logcatSerial = ref<string | null>(null)

const devices = computed(() => devicesQuery.data.value ?? [])
const selectedDevice = computed<Device | null>(() => {
  if (ui.selectedSerial) {
    const matched = devices.value.find((device) => device.serial === ui.selectedSerial)
    if (matched) return matched
  }
  return devices.value[0] ?? null
})

const securityToken = computed(() => {
  const value = settingsQuery.data.value?.effective?.['security.token']
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
})

watch(
  devices,
  (list) => {
    if (list.length === 0) return
    if (!ui.selectedSerial || !list.some((device) => device.serial === ui.selectedSerial)) {
      ui.setSelectedSerial(list[0].serial)
    }
  },
  { immediate: true },
)

function selectDevice(device: Device): void {
  ui.setSelectedSerial(device.serial)
}

function isOnline(device: Device): boolean {
  return device.state === 'device'
}

function stateTone(device: Device): 'green' | 'amber' | 'red' | 'gray' {
  if (!isOnline(device)) return 'red'
  if (device.busy) return 'amber'
  return 'green'
}

function stateLabel(device: Device): string {
  if (!isOnline(device)) return device.state === 'unauthorized' ? '未授权' : '离线'
  if (device.busy) return device.busy_reason ? '占用中' : '占用中'
  return '在线'
}

function openConnect(): void {
  address.value = ''
  connectError.value = ''
  connectOpen.value = true
}

async function connectDevice(): Promise<void> {
  const value = address.value.trim()
  if (!value) {
    connectError.value = '请输入 adb 连接地址'
    return
  }
  if (!/^[\w.-]+:\d{1,5}$/.test(value)) {
    connectError.value = '地址格式应为 host:port，例如 192.168.1.20:5555'
    return
  }
  connectError.value = ''
  try {
    await connectMutation.mutateAsync({ address: value })
    ElMessage.success('已发起连接，请查看设备列表状态')
    connectOpen.value = false
    devicesQuery.refetch()
  } catch {
    // 全局错误提示
  }
}

async function disconnectDevice(device: Device): Promise<void> {
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
  } catch {
    // 全局错误提示
  }
}

function refreshAll(): void {
  devicesQuery.refetch()
  settingsQuery.refetch()
}
</script>

<template>
  <div class="page">
    <PageHead
      title="设备"
      :count="devices.length"
      :subtitle="selectedDevice ? `本地 adb · ${selectedDevice.model || selectedDevice.serial} 已选中 · 镜像与录制同一入口` : '本地 adb · 未选择设备'"
    >
      <template #actions>
        <el-button :icon="Refresh" :loading="devicesQuery.isFetching.value" @click="refreshAll">刷新</el-button>
        <el-button type="primary" :icon="Link" @click="openConnect">连接设备</el-button>
      </template>
    </PageHead>

    <el-alert
      v-if="ui.mirrorRecordHint"
      type="info"
      show-icon
      closable
      title="从镜像录制"
      description="已进入设备页。请在中间镜像面板的「录制工具条」点击「开始镜像录制」，录制期间在镜像上的手动操作会被记录为步骤。"
      @close="ui.setMirrorRecordHint(false)"
    />

    <div v-if="devicesQuery.isLoading.value" class="card"><div class="card-body"><el-skeleton :rows="6" animated /></div></div>

    <el-alert
      v-else-if="devicesQuery.isError.value"
      type="error"
      show-icon
      :closable="false"
      title="设备列表加载失败"
    >
      <template #default>
        <div class="row wrap">
          <span>{{ describeApiError(devicesQuery.error.value) }}</span>
          <el-button size="small" @click="devicesQuery.refetch()">重试</el-button>
        </div>
      </template>
    </el-alert>

    <div v-else class="device-layout">
      <aside class="card device-list">
        <div class="card-head">
          <h2>设备</h2>
          <StatusTag tone="blue">{{ devices.length }} 台</StatusTag>
        </div>
        <div class="device-scroll">
          <div
            v-for="device in devices"
            :key="device.serial"
            :class="['device-item', { selected: device.serial === selectedDevice?.serial }]"
            role="button"
            tabindex="0"
            @click="selectDevice(device)"
            @keyup.enter="selectDevice(device)"
          >
            <div class="grow">
              <div class="dev-top">
                <span class="dot" :class="isOnline(device) ? (device.busy ? 'busy' : 'online') : 'offline'" />
                <span class="dev-name">{{ [device.brand, device.model].filter(Boolean).join(' ') || device.serial }}</span>
              </div>
              <div class="dev-meta code-text">{{ device.serial }} · {{ device.screen_width }}×{{ device.screen_height }}</div>
              <div class="dev-meta">
                {{ isOnline(device) ? `Android ${device.android_version || '—'}` : device.state === 'unauthorized' ? '未授权' : '离线' }}
                · {{ device.busy ? device.busy_reason || '占用中' : '空闲' }}
              </div>
            </div>
            <div class="dev-right">
              <StatusTag :tone="stateTone(device)">{{ stateLabel(device) }}</StatusTag>
              <button
                v-if="device.serial.includes(':')"
                type="button"
                class="icon-btn"
                title="断开网络设备"
                @click.stop="disconnectDevice(device)"
              >
                <el-icon><Connection /></el-icon>
              </button>
            </div>
          </div>
          <el-empty v-if="devices.length === 0" description="未检测到在线设备" :image-size="60">
            <el-button type="primary" :icon="Link" @click="openConnect">连接设备</el-button>
          </el-empty>
        </div>
      </aside>

      <section class="card mirror-card">
        <div class="card-head">
          <h2>
            镜像
            <span v-if="selectedDevice" class="code-text muted" style="font-weight: 400; margin-left: 4px">
              {{ selectedDevice.serial }}
            </span>
          </h2>
          <div class="head-actions">
            <el-tooltip v-if="selectedDevice" content="截图" placement="bottom">
              <el-button size="small" :icon="Camera" @click="screenshotSerial = selectedDevice.serial" />
            </el-tooltip>
            <el-tooltip v-if="selectedDevice" content="层级树" placement="bottom">
              <el-button size="small" :icon="Tickets" @click="hierarchySerial = selectedDevice.serial" />
            </el-tooltip>
            <el-tooltip v-if="selectedDevice" content="logcat" placement="bottom">
              <el-button size="small" :icon="Document" @click="logcatSerial = selectedDevice.serial" />
            </el-tooltip>
          </div>
        </div>
        <div class="card-body">
          <MirrorPanel
            v-if="selectedDevice"
            :key="selectedDevice.serial"
            :serial="selectedDevice.serial"
            :token="securityToken"
            @device-change="devicesQuery.refetch()"
          />
          <el-empty v-else description="选择一台设备后可启动镜像与录制" />
        </div>
      </section>

      <aside class="card control-card">
        <div class="card-head">
          <h2>手动控制</h2>
          <StatusTag v-if="selectedDevice" tone="gray" mono>{{ selectedDevice.model || selectedDevice.serial }}</StatusTag>
        </div>
        <div class="card-body" style="padding: 0">
          <DeviceControlPanel v-if="selectedDevice" :key="selectedDevice.serial" :device="selectedDevice" />
          <el-empty v-else description="未选择设备" :image-size="48" />
        </div>
      </aside>
    </div>

    <el-dialog v-model="connectOpen" title="连接网络设备" width="480px" append-to-body>
      <el-form label-position="top" size="small" @submit.prevent="connectDevice">
        <el-form-item label="设备地址" :error="connectError">
          <el-input v-model="address" placeholder="192.168.1.20:5555" class="code-text" @keyup.enter="connectDevice" />
          <div class="hint">格式 host:port；也可以直接用 USB 连接并在手机上授权调试。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="connectOpen = false">取消</el-button>
        <el-button type="primary" :loading="connectMutation.isPending.value" @click="connectDevice">连接</el-button>
      </template>
    </el-dialog>

    <ScreenshotDialog
      :serial="screenshotSerial"
      :open="Boolean(screenshotSerial)"
      @close="screenshotSerial = null"
    />
    <DeviceHierarchyDrawer
      :serial="hierarchySerial"
      :open="Boolean(hierarchySerial)"
      @close="hierarchySerial = null"
    />
    <DeviceLogcatDrawer
      :serial="logcatSerial"
      :open="Boolean(logcatSerial)"
      @close="logcatSerial = null"
    />
  </div>
</template>
