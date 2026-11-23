<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowDown,
  Document,
  Monitor,
  Refresh,
  Setting,
  VideoCamera,
  VideoPlay,
} from '@element-plus/icons-vue'

import { setAuthToken } from '@/api/client'
import { useDevices } from '@/api/devices'
import { useHealth, useSettings } from '@/api/settings'
import { useUiStore } from '@/stores/ui'
import { formatDateTime } from '@/lib/format'

const APP_VERSION = __APP_VERSION__
const APP_COMMIT = __APP_COMMIT__

const route = useRoute()
const router = useRouter()
const ui = useUiStore()

const settingsQuery = useSettings()
const healthQuery = useHealth()
const devicesQuery = useDevices({ refetchInterval: 5000 })

watch(
  () => settingsQuery.data.value?.effective?.['security.token'],
  (value) => setAuthToken(typeof value === 'string' ? value : ''),
  { immediate: true },
)

const devices = computed(() => devicesQuery.data.value ?? [])
const selectedDevice = computed(() => {
  if (ui.selectedSerial) {
    const matched = devices.value.find((device) => device.serial === ui.selectedSerial)
    if (matched) return matched
  }
  return devices.value[0] ?? null
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

const health = computed(() => healthQuery.data.value)
const healthOk = computed(() => Boolean(health.value) && !healthQuery.isError.value)
const healthTooltip = computed(() => {
  const data = health.value
  const frontendBuild = (() => {
    try {
      return formatDateTime(__APP_BUILD_TIME__)
    } catch {
      return __APP_BUILD_TIME__
    }
  })()
  if (!data) {
    return '后端未连接：请确认 127.0.0.1:8787 已启动'
  }
  return [
    `状态：${data.status}`,
    `后端版本：${data.version} · 提交 ${data.build?.commit ?? '未知'}${data.build?.dirty ? ' (+未提交改动)' : ''}`,
    `前端版本：${APP_VERSION} · 提交 ${APP_COMMIT} · 构建 ${frontendBuild}`,
    `数据库：${data.db}`,
    `adb：${data.adb?.path ?? '—'} ${data.adb?.version ?? ''}`,
    `数据目录：${data.data_dir}`,
  ].join('\n')
})

const activeNav = computed(() => {
  const matched = route.path.match(/^\/(devices|scripts|runs|recorder|settings)/)
  return matched ? `/${matched[1]}` : '/devices'
})

const breadcrumb = computed(() => {
  const section = typeof route.meta.section === 'string' ? route.meta.section : '设备'
  const page = typeof route.meta.page === 'string' ? route.meta.page : '工作台'
  return { section, page }
})

const deviceName = computed(() => {
  const device = selectedDevice.value
  if (!device) return '无在线设备'
  const name = [device.brand, device.model].filter(Boolean).join(' ') || device.serial
  return `${name} · ${device.serial}`
})

const deviceStateText = computed(() => {
  const device = selectedDevice.value
  if (!device) return '离线'
  if (device.state !== 'device') return device.state === 'unauthorized' ? '未授权' : '离线'
  if (device.busy) return device.busy_reason || '占用中'
  return '在线'
})

function navigate(path: string): void {
  if (route.path !== path) void router.push(path)
}

function pickDevice(command: string): void {
  if (command === '__open-devices') {
    navigate('/devices')
    return
  }
  ui.setSelectedSerial(command)
  navigate('/devices')
}

const navItems = [
  { path: '/devices', label: '设备', icon: Monitor },
  { path: '/scripts', label: '脚本', icon: Document },
  { path: '/runs', label: '运行', icon: VideoPlay },
  { path: '/recorder', label: '录制', icon: VideoCamera },
  { path: '/settings', label: '设置', icon: Setting },
]
</script>

<template>
  <div class="app-shell">
    <aside class="rail">
      <div class="brand">
        <span class="brand-mark">AT</span>
        <span class="brand-name">AutoTest</span>
      </div>
      <nav class="nav" aria-label="主导航">
        <button
          v-for="item in navItems"
          :key="item.path"
          type="button"
          :class="['nav-item', { active: activeNav === item.path }]"
          @click="navigate(item.path)"
        >
          <el-icon :size="15"><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </button>
      </nav>
      <div class="rail-foot">
        <div class="rail-version" :title="`页面 ${APP_VERSION} · ${APP_COMMIT}`">
          <span class="dot" :class="selectedDevice?.busy ? 'busy' : selectedDevice ? 'online' : 'offline'" />
          <span>v{{ APP_VERSION }}</span>
          <span class="rail-build">{{ APP_COMMIT }}</span>
        </div>
      </div>
    </aside>

    <div class="work">
      <header class="topbar">
        <div class="crumb">
          <span>{{ breadcrumb.section }}</span>
          <span class="crumb-sep">›</span>
          <strong>{{ breadcrumb.page }}</strong>
        </div>
        <div class="top-actions">
          <el-dropdown trigger="click" @command="pickDevice">
            <button type="button" class="device-chip" title="选择当前设备">
              <span
                class="dot"
                :class="selectedDevice ? (selectedDevice.busy ? 'busy' : 'online') : 'offline'"
              />
              <span class="device-name">{{ deviceName }}</span>
              <el-icon><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu class="device-menu">
                <el-dropdown-item disabled>
                  <span class="menu-label">当前设备 · {{ deviceStateText }}</span>
                </el-dropdown-item>
                <el-dropdown-item v-for="device in devices" :key="device.serial" :command="device.serial">
                  <span class="device-menu-row">
                    <span class="dot" :class="device.state === 'device' ? (device.busy ? 'busy' : 'online') : 'offline'" />
                    <span>
                      <strong>{{ [device.brand, device.model].filter(Boolean).join(' ') || device.serial }}</strong>
                      <small>{{ device.serial }} · {{ device.screen_width }}×{{ device.screen_height }}</small>
                    </span>
                  </span>
                </el-dropdown-item>
                <el-dropdown-item v-if="devices.length === 0" disabled>暂无在线设备</el-dropdown-item>
                <el-dropdown-item divided command="__open-devices">打开设备页</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <el-tooltip :content="healthTooltip" placement="bottom-end" raw-content>
            <button type="button" class="status-chip">
              <span class="dot" :class="healthOk ? 'online' : 'offline'" />
              <span>{{ healthOk ? '正常' : '后端离线' }}</span>
            </button>
          </el-tooltip>

          <el-tooltip content="刷新后端状态与设备列表" placement="bottom">
            <button
              type="button"
              class="icon-btn"
              :class="{ spinning: healthQuery.isFetching.value }"
              @click="healthQuery.refetch(); devicesQuery.refetch()"
            >
              <el-icon><Refresh /></el-icon>
            </button>
          </el-tooltip>

          <span class="top-build" title="页面构建信息">
            {{ APP_VERSION }}·{{ APP_COMMIT }}
            <span v-if="health?.adb">adb {{ health.adb.devices }}</span>
          </span>
        </div>
      </header>

      <main class="content">
        <RouterView v-slot="{ Component }">
          <component :is="Component" :key="route.path" />
        </RouterView>
      </main>
    </div>
  </div>
</template>
