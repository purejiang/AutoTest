<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Moon, Sunny } from '@element-plus/icons-vue'

import { setAuthToken } from '@/api/client'
import { useHealth, useSettings } from '@/api/settings'
import { useTheme } from '@/composables/useTheme'
import { formatDateTime } from '@/lib/format'
import { NAV_ITEMS } from '@/navigation'

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

const healthLines = computed<string[]>(() => {
  const data = health.value
  const frontendBuild = (() => {
    try {
      return formatDateTime(__APP_BUILD_TIME__)
    } catch {
      return __APP_BUILD_TIME__
    }
  })()
  if (!data) {
    return ['后端未连接：请确认 127.0.0.1:8787 已启动']
  }
  return [
    `状态：${data.status}`,
    `后端版本：${data.version} · 提交 ${data.build?.commit ?? '未知'}${data.build?.dirty ? ' (+未提交改动)' : ''}`,
    `前端版本：${APP_VERSION} · 提交 ${APP_COMMIT} · 构建 ${frontendBuild}`,
    `存储：${data.db}`,
    `adb：${data.adb?.path ?? '—'} ${data.adb?.version ?? ''}`,
    `配置文件：${data.config_file ?? '—'}`,
    `脚本目录：${data.scripts_dir ?? '—'}`,
    `数据目录：${data.data_dir}`,
  ]
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

function openHealth(): void {
  void router.push({ path: '/settings', query: { tab: 'about' } })
}
</script>

<template>
  <div class="app-shell">
    <aside class="rail">
      <div class="brand">
        <span class="brand-mark">AT</span>
        <span class="brand-text">
          <span class="brand-name">AutoTest</span>
          <span class="brand-version">v{{ APP_VERSION }}</span>
        </span>
      </div>
      <nav class="nav" aria-label="主导航">
        <button
          v-for="item in NAV_ITEMS"
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
        <el-tooltip placement="right-end" popper-class="health-tooltip">
          <template #content>
            <div v-for="(line, index) in healthLines" :key="index" class="health-tooltip-line">
              {{ line }}
            </div>
          </template>
          <button
            type="button"
            class="rail-health"
            :aria-label="healthOk ? '后端正常，查看健康信息' : '后端离线，查看健康信息'"
            @click="openHealth"
          >
            <span class="dot" :class="healthOk ? 'online' : 'offline'" />
            <span class="rail-health-label">{{ healthOk ? '正常' : '后端离线' }}</span>
          </button>
        </el-tooltip>
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
