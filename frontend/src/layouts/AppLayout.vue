<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Document, Monitor, Moon, Setting, Sunny, VideoCamera } from '@element-plus/icons-vue'

import { setAuthToken } from '@/api/client'
import { useHealth, useSettings } from '@/api/settings'
import { useTheme } from '@/composables/useTheme'
import { formatDateTime } from '@/lib/format'

const APP_VERSION = __APP_VERSION__
const APP_COMMIT = __APP_COMMIT__

const route = useRoute()
const router = useRouter()
const { resolvedTheme, toggleTheme } = useTheme()

const settingsQuery = useSettings()
const healthQuery = useHealth()

watch(
  () => settingsQuery.data.value?.effective?.['security.token'],
  (value) => setAuthToken(typeof value === 'string' ? value : ''),
  { immediate: true },
)

const health = computed(() => healthQuery.data.value)
const healthOk = computed(() => Boolean(health.value) && !healthQuery.isError.value)
const deviceCount = computed(() => Number(health.value?.adb?.devices ?? 0))

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
  if (route.path.startsWith('/scripts') || route.path.startsWith('/runs')) return '/scripts'
  if (route.path.startsWith('/recorder')) return '/recorder'
  if (route.path.startsWith('/settings')) return '/settings'
  return '/console'
})

const breadcrumb = computed<string[]>(() => {
  const meta = route.meta.breadcrumb
  const items = Array.isArray(meta) ? meta.map((item) => String(item)) : []
  if (route.name === 'scripts' && route.query.tab === 'runs') return ['脚本', '运行记录']
  if (items.length > 0) return items
  return ['控制台']
})

function navigate(path: string): void {
  if (route.path !== path) void router.push(path)
}
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
          v-for="item in [
            { path: '/console', label: '控制台', icon: Monitor },
            { path: '/scripts', label: '脚本', icon: Document },
            { path: '/recorder', label: '录制', icon: VideoCamera },
            { path: '/settings', label: '设置', icon: Setting },
          ]"
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
          <span class="dot" :class="healthOk ? (deviceCount > 0 ? 'online' : 'offline') : 'offline'" />
          <span>v{{ APP_VERSION }}</span>
          <span class="rail-build">{{ APP_COMMIT }}</span>
        </div>
      </div>
    </aside>

    <div class="work">
      <header class="topbar">
        <div class="crumb">
          <span>AutoTest</span>
          <template v-for="(item, index) in breadcrumb" :key="`${item}-${index}`">
            <span class="crumb-sep">›</span>
            <strong v-if="index === breadcrumb.length - 1">{{ item }}</strong>
            <span v-else>{{ item }}</span>
          </template>
        </div>
        <div class="top-actions">
          <el-tooltip
            :content="resolvedTheme === 'dark' ? '切换到浅色主题' : '切换到深色主题'"
            placement="bottom"
          >
            <button
              type="button"
              class="icon-btn theme-btn"
              :aria-label="resolvedTheme === 'dark' ? '切换到浅色主题' : '切换到深色主题'"
              @click="toggleTheme"
            >
              <el-icon :size="16"><Sunny v-if="resolvedTheme === 'dark'" /><Moon v-else /></el-icon>
            </button>
          </el-tooltip>

          <el-tooltip :content="healthTooltip" placement="bottom-end" raw-content>
            <button type="button" class="status-chip">
              <span class="dot" :class="healthOk ? 'online' : 'offline'" />
              <span>{{ healthOk ? '正常' : '后端离线' }}</span>
            </button>
          </el-tooltip>

          <span class="top-build" title="页面构建信息">{{ APP_VERSION }} · {{ APP_COMMIT }}</span>

          <el-button class="top-settings" size="small" :icon="Setting" title="打开设置" @click="navigate('/settings')">
            设置
          </el-button>
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
