<script setup lang="ts">
import {
  Cellphone,
  Check,
  Connection,
  Cpu,
  Delete,
  MoreFilled,
  Refresh,
  RefreshLeft,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'

import { describeApiError } from '@/api/client'
import {
  useCleanup,
  useDeleteSettingOverride,
  useHealth,
  useMaintenanceStats,
  useResetSettings,
  useSettings,
  useUpdateSettings,
} from '@/api/settings'
import type { SettingEntry } from '@/types/api'
import { formatBytes, formatDateTime, prettyJson } from '@/lib/format'
import { useTheme, type ThemeChoice } from '@/composables/useTheme'
import StatusTag from '@/components/StatusTag.vue'
import MirrorConfigCard from './MirrorConfigCard.vue'
import RestartHint from './RestartHint.vue'
import SettingField from './SettingField.vue'

const APP_VERSION = __APP_VERSION__
const APP_COMMIT = __APP_COMMIT__
const APP_BUILD_TIME = __APP_BUILD_TIME__

// 与后端 settings_service.scrcpy_options 保持一致；工具栏只展示，不在这里改画质。
const SCRCPY_SERVER_VERSION = '5.0.1'
const SCRCPY_VENDOR_JAR = 'vendor/scrcpy-server-v5.0.1.jar'

const settingsQuery = useSettings()
const updateMutation = useUpdateSettings()
const resetMutation = useResetSettings()
const deleteOverrideMutation = useDeleteSettingOverride()
const healthQuery = useHealth()
const statsQuery = useMaintenanceStats()
const cleanupMutation = useCleanup()

const { theme, setTheme } = useTheme()

type SettingsTab = 'general' | 'toolchain' | 'execution' | 'storage' | 'about'

const TABS: Array<{ key: SettingsTab; label: string }> = [
  { key: 'general', label: '通用' },
  { key: 'toolchain', label: '工具链' },
  { key: 'execution', label: '执行' },
  { key: 'storage', label: '存储' },
  { key: 'about', label: '关于' },
]

const route = useRoute()
const router = useRouter()

function isSettingsTab(value: unknown): value is SettingsTab {
  return typeof value === 'string' && TABS.some((tab) => tab.key === value)
}

// tab 与 URL 同步，便于刷新/深链回测；非法值归一到通用。
const activeTab = computed<SettingsTab>({
  get: () => (isSettingsTab(route.query.tab) ? route.query.tab : 'general'),
  set: (tab) => {
    if (route.query.tab === tab) return
    void router.replace({ path: '/settings', query: { ...route.query, tab } })
  },
})
const language = ref('zh-CN')

const THEME_CHOICES = [
  { value: 'light', label: '浅色', note: '当前工作区风格' },
  { value: 'dark', label: '深色', note: '夜间 / 低光环境' },
  { value: 'system', label: '跟随系统', note: '自动匹配系统外观' },
] as const

const values = reactive<Record<string, any>>({})
const loaded = ref<Record<string, unknown>>({})
const retentionDays = ref<number | undefined>(undefined)
const dryRun = ref(true)

function cloneRecord<T>(input: T): T {
  return JSON.parse(JSON.stringify(input)) as T
}

// 用 loaded 的深拷贝重置本地编辑值，不触发任何后端请求。
function restoreLoadedValues(): void {
  for (const key of Object.keys(values)) delete values[key]
  Object.assign(values, cloneRecord(loaded.value))
}

watch(
  () => settingsQuery.data.value,
  (data) => {
    if (!data) return
    loaded.value = cloneRecord(data.effective) as Record<string, unknown>
    restoreLoadedValues()
  },
  { immediate: true },
)

watch(
  () => statsQuery.data.value,
  (data) => {
    if (data) retentionDays.value = data.retention_days
  },
  { immediate: true },
)

const settings = computed(() => settingsQuery.data.value ?? null)
const sources = computed(() => settings.value?.sources ?? {})

// 每个 key 只出现在一个分组里；只渲染后端 SETTINGS_SCHEMA 里已有的 key。
type KeyGroup = 'adb' | 'scrcpyJar' | 'engine' | 'recording' | 'capture' | 'security' | 'storage'

const GROUP_KEYS: Record<KeyGroup, string[]> = {
  adb: ['adb.path', 'adb.connect_timeout_s'],
  scrcpyJar: ['scrcpy.jar'],
  engine: [
    'engine.poll_interval_ms',
    'engine.default_timeout_ms',
    'engine.default_delay_before_ms',
    'engine.default_delay_after_ms',
    'engine.max_total_ms',
    'engine.max_steps',
  ],
  recording: ['recording.click_dump', 'recording.confirm_before_save', 'recording.target_mode'],
  capture: ['capture.logcat_auto_attach', 'capture.network.mode', 'capture.network.port'],
  security: ['security.token'],
  storage: [
    'runtime.data_dir',
    'retention.days',
    'retention.report_exclude_video',
    'retention.report_max_mb',
  ],
}

const schemaByKey = computed<Record<string, SettingEntry>>(() => {
  const out: Record<string, SettingEntry> = {}
  for (const entry of settings.value?.schema ?? []) out[entry.key] = entry
  return out
})

const schemaKeys = computed(() => new Set(Object.keys(schemaByKey.value)))

function entriesFor(group: KeyGroup): SettingEntry[] {
  const keys = GROUP_KEYS[group]
  return (settings.value?.schema ?? []).filter((entry) => keys.includes(entry.key))
}

// 保存条只统计后端 schema key 的改动；主题 / 语言是纯前端偏好，不产生未保存项。
const changedKeys = computed(() =>
  Object.keys(values)
    .filter((key) => schemaKeys.value.has(key))
    .filter((key) => JSON.stringify(values[key]) !== JSON.stringify(loaded.value[key])),
)

// 只有存在未保存的后端改动时才显示操作条；所有 Tab 都保留保存入口，
// 避免切到「关于」后看不到保存条、改动无法落库。
const showSavebar = computed(() => changedKeys.value.length > 0)

// 干净状态用页头的一个小标签代替常驻保存条，保持轻量。
const showSavedTag = computed(
  () => Boolean(settings.value) && changedKeys.value.length === 0,
)

const health = computed(() => healthQuery.data.value)
const stats = computed(() => statsQuery.data.value)
const cleanupResult = computed(() => cleanupMutation.data.value ?? null)

const overrideCount = computed(
  () => Object.values(sources.value).filter((source) => source !== 'default').length,
)
const hasOverrides = computed(() => overrideCount.value > 0)
const overrideTooltip = computed(() => {
  const counts: Record<string, number> = { db: 0, file: 0, env: 0 }
  for (const source of Object.values(sources.value)) {
    if (source in counts) counts[source] += 1
  }
  return [
    `有 ${overrideCount.value} 项设置不是代码默认值：数据库覆盖 ${counts.db} 项 · 配置文件 ${counts.file} 项 · 环境变量 ${counts.env} 项。`,
    '优先级：默认值 < 配置文件 < 数据库 < 环境变量。',
    '每项标题旁的来源标签显示具体来源；只有「数据库覆盖」可以在页面上点 ↺ 删除，文件/环境变量需要修改配置或重启进程。',
  ].join('\n')
})

const cleanupSummary = computed(() => {
  const data = cleanupResult.value
  if (!data) return ''
  const keys = [
    'dry_run',
    'retention_days',
    'cutoff',
    'runs_matched',
    'runs_deleted',
    'artifacts_matched',
    'artifacts_deleted',
    'files_deleted',
    'freed_bytes',
  ]
  const parts: string[] = []
  for (const key of keys) {
    const value = data[key]
    if (value === undefined || value === null) continue
    const text = key === 'freed_bytes' && typeof value === 'number' ? formatBytes(value) : String(value)
    parts.push(`${key}: ${text}`)
  }
  return parts.join(' · ')
})

function sourceOf(entry: SettingEntry): 'default' | 'file' | 'db' | 'env' {
  return sources.value[entry.key] ?? 'default'
}

function onMirrorChange(key: string, value: unknown): void {
  values[key] = value
}

async function confirmDeleteOverride(key: string, title: string): Promise<void> {
  try {
    await ElMessageBox.confirm(`删除「${title}」的覆盖，回到默认值？`, '删除覆盖', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deleteOverrideMutation.mutateAsync(key)
    ElMessage.success('已删除覆盖')
  } catch {
    // 全局错误提示
  }
}

function deleteOverride(entry: SettingEntry): void {
  void confirmDeleteOverride(entry.key, entry.title)
}

function clearMirrorKey(key: string): void {
  void confirmDeleteOverride(key, schemaByKey.value[key]?.title ?? key)
}

async function saveSettings(): Promise<void> {
  if (changedKeys.value.length === 0) {
    ElMessage.info('没有改动需要保存')
    return
  }
  // 保存成功后 query 数据会刷新并重置 values，这里先快照，避免提示里变成 0 项。
  const count = changedKeys.value.length
  const mirrorTouched = changedKeys.value.some((key) => key.startsWith('scrcpy.'))
  const payload: Record<string, unknown> = {}
  for (const key of changedKeys.value) payload[key] = values[key]
  try {
    await updateMutation.mutateAsync(payload)
    ElMessage.success(`已保存 ${count} 项设置${mirrorTouched ? ' · 镜像参数需停止并重新启动镜像生效' : ''}`)
  } catch {
    // 全局错误提示
  }
}

// 放弃本地编辑：回到最近一次加载/保存后的值，不请求后端。
async function discardChanges(): Promise<void> {
  const count = changedKeys.value.length
  if (count === 0) return
  try {
    await ElMessageBox.confirm(`放弃 ${count} 项未保存的改动，恢复为上次加载的值？`, '放弃更改', {
      type: 'warning',
      confirmButtonText: '放弃更改',
      cancelButtonText: '继续编辑',
    })
  } catch {
    return
  }
  restoreLoadedValues()
  ElMessage.info('已放弃未保存的更改')
}

function onGlobalKeydown(event: KeyboardEvent): void {
  if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === 's') {
    event.preventDefault()
    if (showSavebar.value && !updateMutation.isPending.value) void saveSettings()
  }
}

function onBeforeUnload(event: BeforeUnloadEvent): void {
  if (!showSavebar.value) return
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => {
  window.addEventListener('keydown', onGlobalKeydown)
  window.addEventListener('beforeunload', onBeforeUnload)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKeydown)
  window.removeEventListener('beforeunload', onBeforeUnload)
})

onBeforeRouteLeave(async () => {
  if (!showSavebar.value) return true
  try {
    await ElMessageBox.confirm(
      `有 ${changedKeys.value.length} 项未保存的改动，离开后会丢失。`,
      '离开设置',
      { type: 'warning', confirmButtonText: '离开', cancelButtonText: '继续编辑' },
    )
    return true
  } catch {
    return false
  }
})

function onMoreCommand(command: string): void {
  if (command === 'reset') {
    void resetSettings()
    return
  }
  if (command === 'reload') void reloadSettings()
}

async function resetSettings(): Promise<void> {
  try {
    await ElMessageBox.confirm('清空 DB/文件层覆盖，所有配置回到代码默认值。', '恢复默认设置', {
      type: 'warning',
      confirmButtonText: '恢复默认',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await resetMutation.mutateAsync()
    ElMessage.success('已恢复默认设置')
  } catch {
    // 全局错误提示
  }
}

async function reloadSettings(): Promise<void> {
  if (changedKeys.value.length > 0) {
    try {
      await ElMessageBox.confirm(
        `有 ${changedKeys.value.length} 项未保存的改动，重新加载会丢弃这些改动。`,
        '重新加载设置',
        { type: 'warning', confirmButtonText: '重新加载', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }
  await settingsQuery.refetch()
}

async function runCleanup(): Promise<void> {
  if (!dryRun.value) {
    try {
      await ElMessageBox.confirm('确认执行清理？此操作会删除过期运行、产物与文件。', '执行清理', {
        type: 'warning',
        confirmButtonText: '执行',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
  }
  try {
    await cleanupMutation.mutateAsync({
      retention_days: retentionDays.value,
      dry_run: dryRun.value,
    })
    ElMessage.success(dryRun.value ? '演练完成（未删除任何数据）' : '清理完成')
  } catch {
    // 全局错误提示
  }
}

function applyThemeChoice(choice: ThemeChoice): void {
  setTheme(choice)
}
</script>

<template>
  <div class="page">
    <h1 class="visually-hidden">设置</h1>
    <div class="settings-actions">
      <span class="settings-priority">配置优先级：默认 &lt; 文件 &lt; 数据库 &lt; 环境变量</span>
      <span class="spacer" />
      <StatusTag v-if="showSavedTag" tone="green" dot>已保存</StatusTag>
      <el-tooltip
        v-if="hasOverrides"
        :content="overrideTooltip"
        placement="bottom-end"
        popper-class="settings-override-tooltip"
      >
        <StatusTag tone="blue">{{ overrideCount }} 项使用覆盖值</StatusTag>
      </el-tooltip>
      <el-dropdown trigger="click" placement="bottom-end" @command="onMoreCommand">
        <el-button
          size="small"
          class="page-more"
          :icon="MoreFilled"
          title="更多设置操作"
          aria-label="更多设置操作"
        />
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="reset" :icon="RefreshLeft" :disabled="resetMutation.isPending.value">
              恢复默认
            </el-dropdown-item>
            <el-dropdown-item command="reload" :icon="Refresh">重新加载</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>

    <div v-if="settingsQuery.isLoading.value" class="card">
      <div class="card-body"><el-skeleton :rows="8" animated /></div>
    </div>
    <el-result
      v-else-if="settingsQuery.isError.value || !settings"
      icon="warning"
      title="设置加载失败"
      :sub-title="describeApiError(settingsQuery.error.value)"
    >
      <template #extra>
        <el-button type="primary" @click="settingsQuery.refetch()">重试</el-button>
      </template>
    </el-result>

    <div v-else class="settings-layout">
      <aside class="card settings-nav">
        <button
          v-for="tab in TABS"
          :key="tab.key"
          type="button"
          class="settings-tab"
          :class="{ active: activeTab === tab.key }"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
        </button>
        <div class="settings-note" title="配置优先级：默认 &lt; 文件 &lt; 数据库 &lt; 环境变量">
          外观仅本机 · 其余保存后生效
        </div>
      </aside>

      <div class="settings-main" :class="{ 'has-unsaved': showSavebar }">
        <!-- ============================================================ 通用 -->
        <div v-if="activeTab === 'general'" class="settings-pane">
          <section class="card settings-card">
            <div class="card-head">
              <h2>外观</h2>
              <span class="card-hint" title="切换只影响当前浏览器，不写入后端配置；顶部栏主题按钮与此同步。">
                即时预览
              </span>
            </div>
            <div class="card-body">
              <div class="theme-choices">
                <button
                  v-for="choice in THEME_CHOICES"
                  :key="choice.value"
                  type="button"
                  class="theme-choice"
                  :class="{ active: theme === choice.value }"
                  :data-theme-choice="choice.value"
                  @click="applyThemeChoice(choice.value)"
                >
                  <span class="theme-preview" aria-hidden="true">
                    <span class="tp-rail" />
                    <span class="tp-body" />
                  </span>
                  <strong>{{ choice.label }}</strong>
                  <small>{{ choice.note }}</small>
                </button>
              </div>
            </div>
          </section>

          <section class="card settings-card">
            <div class="card-head">
              <h2>语言</h2>
              <span class="card-hint" title="完整中英双语需要抽取全站文案，放到后续版本；本版先保留入口，不做半汉半英。">
                仅简体中文 · 双语后续版本
              </span>
            </div>
            <div class="card-body">
              <div class="field" style="max-width: 360px">
                <span class="field-label">界面语言</span>
                <el-select v-model="language" size="small" style="width: 100%">
                  <el-option label="简体中文（zh-CN）" value="zh-CN" />
                  <el-option label="English（规划中）" value="en-US" disabled />
                </el-select>
              </div>
            </div>
          </section>
        </div>

        <!-- ============================================================ 工具链 -->
        <div v-else-if="activeTab === 'toolchain'" class="settings-pane">
          <div class="tool-grid">
            <section class="card tool-card">
              <div class="card-head">
                <h2><el-icon :size="15"><Connection /></el-icon> ADB</h2>
                <span class="card-hint" title="设备连接、命令执行与在线设备数。">设备连接与命令</span>
                <StatusTag :tone="health?.adb?.version ? 'green' : health ? 'amber' : 'gray'">
                  {{ health?.adb?.version ? '正常' : health ? '待检查' : '未知' }}
                </StatusTag>
              </div>
              <div class="card-body">
                <div class="tool-row">
                  <span>路径</span><code>{{ health?.adb?.path || '—' }}</code>
                </div>
                <div class="tool-row">
                  <span>版本</span><code>{{ health?.adb?.version || '—' }}</code>
                </div>
                <div class="tool-row">
                  <span>在线设备</span><code>{{ health?.adb?.devices ?? '—' }}</code>
                </div>
                <SettingField
                  v-for="entry in entriesFor('adb')"
                  :key="entry.key"
                  :entry="entry"
                  :model-value="values[entry.key]"
                  :source="sourceOf(entry)"
                  @update:model-value="values[entry.key] = $event"
                  @clear="deleteOverride(entry)"
                />
                <div class="tool-actions">
                  <el-button
                    size="small"
                    :loading="healthQuery.isFetching.value"
                    @click="healthQuery.refetch()"
                  >
                    重新探测
                  </el-button>
                </div>
              </div>
            </section>

            <section class="card tool-card">
              <div class="card-head">
                <h2><el-icon :size="15"><Cpu /></el-icon> uiautomator2</h2>
                <span class="card-hint" title="层级树、元素定位、文本输入都依赖该驱动；设备端服务缺失时由后端在首次操作时提示。">
                  元素定位与输入
                </span>
                <StatusTag :tone="health?.status === 'ok' ? 'green' : health ? 'amber' : 'gray'">
                  {{ health?.status === 'ok' ? '可用' : health ? '降级' : '未知' }}
                </StatusTag>
              </div>
              <div class="card-body">
                <div class="tool-row"><span>驱动</span><code>U2Driver</code></div>
                <div class="tool-row"><span>设备服务</span><code>首次操作时检查/安装</code></div>
                <div class="tool-row"><span>安装入口</span><code>不在此页触发</code></div>
              </div>
            </section>

            <section class="card tool-card">
              <div class="card-head">
                <h2><el-icon :size="15"><Cellphone /></el-icon> scrcpy-server</h2>
                <span class="card-hint" title="镜像画质、帧率和码率在「执行 → 镜像」配置。">
                  镜像与录制服务
                </span>
                <StatusTag tone="green">已锁定</StatusTag>
              </div>
              <div class="card-body">
                <div class="tool-row">
                  <span>版本</span><code>{{ SCRCPY_SERVER_VERSION }}</code>
                </div>
                <div class="tool-row">
                  <span>内置 jar</span><code>{{ SCRCPY_VENDOR_JAR }}</code>
                </div>
                <div class="tool-row"><span>校验</span><code>启动前 SHA256 校验/必要时重推</code></div>
                <SettingField
                  v-for="entry in entriesFor('scrcpyJar')"
                  :key="entry.key"
                  :entry="entry"
                  :model-value="values[entry.key]"
                  :source="sourceOf(entry)"
                  @update:model-value="values[entry.key] = $event"
                  @clear="deleteOverride(entry)"
                />
              </div>
            </section>
          </div>

        </div>

        <!-- ============================================================ 执行 -->
        <div v-else-if="activeTab === 'execution'" class="settings-pane">
          <MirrorConfigCard
            :values="values"
            :sources="sources"
            :schema="schemaByKey"
            :server-version="SCRCPY_SERVER_VERSION"
            @change="onMirrorChange"
            @clear="clearMirrorKey"
          />

          <div class="exec-grid">
            <section class="card exec-card">
              <div class="card-head">
                <h2>运行节奏</h2>
                <span class="card-hint" title="轮询间隔、默认超时/停顿、单次运行上限与最大步数。停顿只作为新建脚本的初值。">轮询 · 超时 · 停顿 · 步数</span>
              </div>
              <div class="card-body">
                <div class="field-row">
                  <SettingField
                    v-for="entry in entriesFor('engine')"
                    :key="entry.key"
                    :entry="entry"
                    :model-value="values[entry.key]"
                    :source="sourceOf(entry)"
                    @update:model-value="values[entry.key] = $event"
                    @clear="deleteOverride(entry)"
                  />
                </div>
              </div>
            </section>

            <section class="card exec-card">
              <div class="card-head">
                <h2>录制与定位</h2>
                <span class="card-hint" title="点击前抓层级、录后确认与录制转脚本的定位方式。">层级抓取 · 定位方式</span>
              </div>
              <div class="card-body">
                <div class="field-row">
                  <SettingField
                    v-for="entry in entriesFor('recording')"
                    :key="entry.key"
                    :entry="entry"
                    :model-value="values[entry.key]"
                    :source="sourceOf(entry)"
                    @update:model-value="values[entry.key] = $event"
                    @clear="deleteOverride(entry)"
                  />
                </div>
              </div>
            </section>

            <section class="card exec-card">
              <div class="card-head">
                <h2>采集</h2>
                <span class="card-hint" title="logcat 进入运行产物；网络抓包只记录域名，并可在失败节点保存证据。">logcat · 域名抓包 · 失败证据</span>
              </div>
              <div class="card-body">
                <div class="field-row">
                  <SettingField
                    v-for="entry in entriesFor('capture')"
                    :key="entry.key"
                    :entry="entry"
                    :model-value="values[entry.key]"
                    :source="sourceOf(entry)"
                    @update:model-value="values[entry.key] = $event"
                    @clear="deleteOverride(entry)"
                  />
                </div>
              </div>
            </section>

            <section class="card exec-card">
              <div class="card-head">
                <h2>访问与安全</h2>
                <RestartHint detail="访问令牌修改后需要重启后端；对外暴露服务时建议设置。" />
              </div>
              <div class="card-body">
                <SettingField
                  v-for="entry in entriesFor('security')"
                  :key="entry.key"
                  :entry="entry"
                  :model-value="values[entry.key]"
                  :source="sourceOf(entry)"
                  @update:model-value="values[entry.key] = $event"
                  @clear="deleteOverride(entry)"
                />
              </div>
            </section>
          </div>

        </div>

        <!-- ============================================================ 存储 -->
        <div v-else-if="activeTab === 'storage'" class="settings-pane">
          <section class="card settings-card">
            <div class="card-head">
              <h2>存储设置</h2>
              <RestartHint detail="修改数据目录/前端目录后需要重启后端；SQLite 不会热切换，产物挂载按新目录动态解析。" />
            </div>
            <div class="card-body">
              <div class="settings-grid">
                <SettingField
                  v-for="entry in entriesFor('storage')"
                  :key="entry.key"
                  :entry="entry"
                  :model-value="values[entry.key]"
                  :source="sourceOf(entry)"
                  @update:model-value="values[entry.key] = $event"
                  @clear="deleteOverride(entry)"
                />
              </div>
            </div>
          </section>

          <section class="card">
            <div class="card-head">
              <h2>存储统计</h2>
              <span class="card-hint" title="统计数据库与产物占用，并清理过期运行。">运行/产物体积 · 清理</span>
              <el-button
                size="small"
                :icon="Refresh"
                :loading="statsQuery.isFetching.value"
                @click="statsQuery.refetch()"
              >
                刷新统计
              </el-button>
            </div>
            <div class="card-body">
              <el-skeleton v-if="statsQuery.isLoading.value" :rows="4" animated />
              <el-alert
                v-else-if="statsQuery.isError.value"
                type="warning"
                :closable="false"
                show-icon
                title="统计加载失败"
                :description="describeApiError(statsQuery.error.value)"
              />
              <template v-else-if="stats">
                <div class="kv-grid">
                  <div class="kv">
                    <span>数据库体积</span><strong>{{ formatBytes(stats.db_size) }}</strong>
                  </div>
                  <div class="kv">
                    <span>产物总大小</span><strong>{{ formatBytes(stats.artifacts_size) }}</strong>
                  </div>
                  <div class="kv">
                    <span>运行记录</span><strong>{{ stats.runs }}</strong>
                  </div>
                  <div class="kv">
                    <span>产物数量</span><strong>{{ stats.artifacts }}</strong>
                  </div>
                  <div class="kv">
                    <span>最早记录</span><strong>{{ formatDateTime(stats.oldest_run_at) }}</strong>
                  </div>
                  <div class="kv">
                    <span>运行保留天数</span><strong>{{ stats.retention_days }}</strong>
                  </div>
                </div>
                <div class="maintenance-actions">
                  <span class="small">运行保留天数</span>
                  <el-input-number
                    v-model="retentionDays"
                    size="small"
                    :min="1"
                    :max="3650"
                    controls-position="right"
                    style="width: 120px"
                  />
                  <el-checkbox v-model="dryRun" size="small">仅演练（dry_run，不删除）</el-checkbox>
                  <el-button
                    :type="dryRun ? 'default' : 'danger'"
                    :icon="Delete"
                    :loading="cleanupMutation.isPending.value"
                    @click="runCleanup"
                  >
                    执行清理
                  </el-button>
                </div>
                <div v-if="cleanupResult" style="margin-top: 12px">
                  <el-alert
                    v-if="cleanupSummary"
                    type="info"
                    :closable="false"
                    show-icon
                    :title="cleanupSummary"
                  />
                  <pre class="pre-block" style="margin-top: 8px">{{ prettyJson(cleanupResult) }}</pre>
                </div>
              </template>
            </div>
          </section>
        </div>

        <!-- ============================================================ 关于 -->
        <div v-else class="settings-pane">
          <section class="card settings-card">
            <div class="card-head">
              <h2>关于 AutoTest</h2>
              <span class="card-hint">单机 Android UI 自动化平台</span>
              <StatusTag :tone="health?.status === 'ok' ? 'green' : 'amber'">
                {{ health?.status === 'ok' ? '正常' : health?.status ?? '未知' }}
              </StatusTag>
            </div>
            <div class="card-body">
              <div class="kv-grid">
                <div class="kv">
                  <span>前端版本</span><strong>{{ APP_VERSION }}</strong>
                </div>
                <div class="kv">
                  <span>前端提交</span><strong class="code-text">{{ APP_COMMIT }}</strong>
                </div>
                <div class="kv">
                  <span>页面构建</span><strong>{{ formatDateTime(APP_BUILD_TIME) }}</strong>
                </div>
                <div class="kv">
                  <span>后端版本</span><strong>{{ health?.version ?? '—' }}</strong>
                </div>
                <div class="kv">
                  <span>数据目录</span><strong class="code-text">{{ health?.data_dir ?? '—' }}</strong>
                </div>
                <div class="kv">
                  <span>配置优先级</span><strong>默认 &lt; 文件 &lt; 数据库 &lt; 环境变量</strong>
                </div>
              </div>
            </div>
          </section>

          <section class="card">
            <div class="card-head">
              <h2>健康信息</h2>
              <span class="card-hint" title="状态来自后端 /api/health，缓存 5 秒。">后端状态 · adb · 目录</span>
              <el-button
                size="small"
                :icon="Refresh"
                :loading="healthQuery.isFetching.value"
                @click="healthQuery.refetch()"
              >
                刷新
              </el-button>
            </div>
            <div class="card-body">
              <el-skeleton v-if="healthQuery.isLoading.value" :rows="3" animated />
              <el-alert
                v-else-if="healthQuery.isError.value || !health"
                type="warning"
                :closable="false"
                show-icon
                title="后端未连接"
                :description="describeApiError(healthQuery.error.value)"
              />
              <el-descriptions v-else :column="1" border size="small">
                <el-descriptions-item label="状态">
                  <StatusTag :tone="health.status === 'ok' ? 'green' : 'amber'">
                    {{ health.status === 'ok' ? '正常' : health.status }}
                  </StatusTag>
                </el-descriptions-item>
                <el-descriptions-item label="版本">{{ health.version }}</el-descriptions-item>
                <el-descriptions-item label="数据库">
                  <span class="code-text">{{ health.db }}</span>
                </el-descriptions-item>
                <el-descriptions-item label="adb">
                  <span class="code-text">
                    {{ health.adb?.path || '—' }}
                    {{ health.adb?.version ? `· ${health.adb.version}` : '' }}
                    · 设备 {{ health.adb?.devices ?? 0 }}
                  </span>
                </el-descriptions-item>
                <el-descriptions-item label="数据目录">
                  <span class="code-text">{{ health.data_dir }}</span>
                </el-descriptions-item>
                <el-descriptions-item label="后端构建">
                  <span class="code-text">
                    {{ health.build?.commit ?? '未知' }}{{ health.build?.dirty ? ' (+未提交改动)' : '' }} ·
                    {{ formatDateTime(health.build?.built_at) }}
                  </span>
                </el-descriptions-item>
              </el-descriptions>
            </div>
          </section>
        </div>

        <!-- ============================================================ 未保存操作条 -->
        <div v-if="showSavebar" class="savebar" role="region" aria-label="未保存的设置更改">
          <div class="savebar-summary">
            <div class="savebar-title">
              <span class="savebar-dot" aria-hidden="true" />
              未保存 {{ changedKeys.length }} 项
            </div>
          </div>
          <div class="savebar-actions">
            <el-button size="small" :disabled="updateMutation.isPending.value" @click="discardChanges">
              放弃更改
            </el-button>
            <el-button
              type="primary"
              size="small"
              :icon="Check"
              :loading="updateMutation.isPending.value"
              title="保存更改（⌘S / Ctrl+S）"
              aria-keyshortcuts="Control+S Meta+S"
              @click="saveSettings"
            >
              保存更改
            </el-button>
            <el-dropdown
              trigger="click"
              placement="top-end"
              :disabled="updateMutation.isPending.value"
              @command="onMoreCommand"
            >
              <el-button
                size="small"
                class="savebar-more"
                :icon="MoreFilled"
                title="更多：恢复默认 / 重新加载"
                aria-label="更多设置操作"
              />
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item
                    command="reset"
                    :icon="RefreshLeft"
                    :disabled="resetMutation.isPending.value"
                  >
                    恢复默认
                  </el-dropdown-item>
                  <el-dropdown-item command="reload" :icon="Refresh">重新加载</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 页头常驻的「更多」入口：无未保存改动时也能恢复默认 / 重新加载。 */
.page-more {
  width: 26px;
  padding: 0;
  margin-left: 2px;
}

.settings-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-height: 30px;
}

.settings-actions .spacer {
  flex: 1;
}

.settings-priority {
  color: var(--muted-2);
  font-size: 10px;
  line-height: 1.4;
}

.settings-layout {
  display: grid;
  grid-template-columns: 184px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.settings-nav {
  position: sticky;
  top: calc(var(--top) + 16px);
  padding: 6px;
}

.settings-tab {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  height: 36px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
}

.settings-tab:hover {
  background: var(--surface-2);
  color: var(--ink);
}

.settings-tab.active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 650;
}

.settings-note {
  margin-top: 8px;
  padding: 8px 9px;
  border-top: 1px solid var(--line-soft);
  color: var(--muted-2);
  font-size: 10px;
  line-height: 1.5;
}

.settings-main {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.settings-pane {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.settings-pane :deep(.el-alert__title) {
  font-size: 11px;
}

.settings-pane :deep(.el-alert__description) {
  font-size: 10.5px;
  line-height: 1.5;
}

.settings-card .card-body {
  padding: 14px;
}

/* ---- 外观 */
.theme-choices {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.theme-choice {
  padding: 9px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}

.theme-choice:hover {
  border-color: var(--accent);
}

.theme-choice.active {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-soft);
}

.theme-preview {
  display: grid;
  grid-template-columns: 16px 1fr;
  height: 44px;
  margin-bottom: 7px;
  border: 1px solid var(--line);
  border-radius: 5px;
  overflow: hidden;
}

.theme-preview .tp-rail {
  background: #f8fafc;
  border-right: 1px solid #e3e8ef;
}

.theme-preview .tp-body {
  padding: 5px;
  background: #f2f4f7;
}

.theme-preview .tp-body::before {
  content: '';
  display: block;
  height: 7px;
  border-radius: 2px;
  background: #fff;
  box-shadow: 0 8px 0 #e3e8ef;
}

.theme-choice[data-theme-choice='dark'] .theme-preview .tp-rail {
  background: #171717;
  border-right-color: #2e2e2e;
}

.theme-choice[data-theme-choice='dark'] .theme-preview .tp-body {
  background: #1f1f1f;
}

.theme-choice[data-theme-choice='dark'] .theme-preview .tp-body::before {
  background: #262626;
  box-shadow: 0 8px 0 #3a3a3a;
}

.theme-choice[data-theme-choice='system'] .theme-preview .tp-rail {
  background: linear-gradient(90deg, #f8fafc 0 50%, #171717 50% 100%);
  border-right-color: #2e2e2e;
}

.theme-choice[data-theme-choice='system'] .theme-preview .tp-body {
  background: linear-gradient(90deg, #f2f4f7 0 50%, #1f1f1f 50% 100%);
}

.theme-choice[data-theme-choice='system'] .theme-preview .tp-body::before {
  background: linear-gradient(90deg, #fff 0 50%, #262626 50% 100%);
  box-shadow: 0 8px 0 #3a3a3a;
}

.theme-choice strong {
  display: block;
  font-size: 11.5px;
  font-weight: 600;
}

.theme-choice small {
  display: block;
  margin-top: 1px;
  color: var(--muted-2);
  font-size: 10px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.field-label {
  color: var(--muted);
  font-size: 11px;
}

/* ---- 工具链 */
.tool-card .card-body {
  padding: 14px;
}

.tool-card .setting-field {
  margin-top: 6px;
}

.tool-row {
  border-bottom-color: var(--line-soft);
}

/* ---- 执行 */
.exec-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.exec-card .card-body {
  padding: 14px;
}

.field-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 10px;
}

/* ---- 存储 / 关于 */
.kv-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.kv-grid .kv {
  border-bottom-color: var(--line-soft);
}

/* ---- 未保存操作条 */
/* 内容较短时也让操作条落到内容列底部，避免改动后要往下找。 */
.settings-main.has-unsaved {
  min-height: calc(100vh - var(--top) - 84px);
}

/* 给 sticky 操作条预留滚动余量，滚到末尾时最后一张卡片/alert 不会被盖住。 */
.settings-main.has-unsaved .settings-pane {
  padding-bottom: 96px;
}

.savebar {
  position: sticky;
  bottom: 12px;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: auto;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface);
  box-shadow: var(--shadow-md);
}

.savebar-summary {
  min-width: 0;
}

.savebar-title {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--amber);
  font-size: 12px;
  font-weight: 650;
  line-height: 1.3;
}

.savebar-dot {
  flex: none;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--amber);
  box-shadow: 0 0 0 3px var(--amber-soft);
}

.savebar-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
  margin-left: auto;
}

.savebar-actions :deep(.el-button) {
  height: 28px;
  padding: 0 11px;
  margin-left: 0;
  font-size: 11.5px;
}

.savebar-actions :deep(.savebar-more) {
  width: 28px;
  padding: 0;
}

@media (max-width: 900px) {
  .settings-layout {
    grid-template-columns: 1fr;
  }

  .settings-nav {
    position: static;
    display: flex;
    gap: 3px;
    overflow-x: auto;
    padding: 5px;
  }

  .settings-tab {
    width: auto;
    flex: none;
    padding: 0 10px;
  }

  .settings-note {
    display: none;
  }

  .exec-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .settings-priority {
    display: none;
  }
  .theme-choices,
  .field-row,
  .kv-grid {
    grid-template-columns: 1fr;
  }

  /* 窄屏操作条可能折成两行，多留一些滚动余量。 */
  .settings-main.has-unsaved .settings-pane {
    padding-bottom: 128px;
  }

  .savebar {
    gap: 8px;
    padding: 10px;
  }
}
</style>
