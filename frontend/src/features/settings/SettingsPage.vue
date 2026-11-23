<script setup lang="ts">
import { Delete, Refresh, RefreshLeft, Setting } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'

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
import {
  formatBytes,
  formatDateTime,
  numberOrNull,
  prettyJson,
  settingSourceMeta,
} from '@/lib/format'
import PageHead from '@/components/PageHead.vue'
import StatusTag from '@/components/StatusTag.vue'

const APP_VERSION = __APP_VERSION__
const APP_COMMIT = __APP_COMMIT__
const APP_BUILD_TIME = __APP_BUILD_TIME__

const settingsQuery = useSettings()
const updateMutation = useUpdateSettings()
const resetMutation = useResetSettings()
const deleteOverrideMutation = useDeleteSettingOverride()
const healthQuery = useHealth()
const statsQuery = useMaintenanceStats()
const cleanupMutation = useCleanup()

const activeTab = ref('settings')
const values = reactive<Record<string, any>>({})
const loaded = ref<Record<string, unknown>>({})
const retentionDays = ref<number | undefined>(undefined)
const dryRun = ref(true)

watch(
  () => settingsQuery.data.value,
  (data) => {
    if (!data) return
    loaded.value = JSON.parse(JSON.stringify(data.effective)) as Record<string, unknown>
    for (const key of Object.keys(values)) delete values[key]
    Object.assign(values, JSON.parse(JSON.stringify(data.effective)) as Record<string, unknown>)
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
const changedKeys = computed(() =>
  Object.keys(values).filter(
    (key) => JSON.stringify(values[key]) !== JSON.stringify(loaded.value[key]),
  ),
)
const health = computed(() => healthQuery.data.value)
const stats = computed(() => statsQuery.data.value)
const cleanupResult = computed(() => cleanupMutation.data.value ?? null)

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

function sourceOf(entry: SettingEntry): 'default' | 'file' | 'db' {
  return settings.value?.sources[entry.key] ?? 'default'
}

async function saveSettings(): Promise<void> {
  if (changedKeys.value.length === 0) {
    ElMessage.info('没有改动需要保存')
    return
  }
  const payload: Record<string, unknown> = {}
  for (const key of changedKeys.value) payload[key] = values[key]
  try {
    await updateMutation.mutateAsync(payload)
    ElMessage.success(`已保存 ${changedKeys.value.length} 项设置`)
  } catch {
    // 全局错误提示
  }
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

async function deleteOverride(entry: SettingEntry): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `删除「${entry.title}」的覆盖，回到默认值？`,
      '删除覆盖',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteOverrideMutation.mutateAsync(entry.key)
    ElMessage.success('已删除覆盖')
  } catch {
    // 全局错误提示
  }
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
    await cleanupMutation.mutateAsync({ retention_days: retentionDays.value, dry_run: dryRun.value })
    ElMessage.success(dryRun.value ? '演练完成（未删除任何数据）' : '清理完成')
  } catch {
    // 全局错误提示
  }
}
</script>

<template>
  <div class="page">
    <PageHead title="设置" subtitle="配置优先级：默认 &lt; 文件 &lt; 数据库 &lt; 环境变量">
      <template #actions>
        <StatusTag v-if="settings && Object.values(settings.sources).some((source) => source !== 'default')" tone="amber">
          存在覆盖项
        </StatusTag>
      </template>
    </PageHead>

    <el-tabs v-model="activeTab" class="settings-tabs">
      <el-tab-pane label="设置" name="settings">
        <div v-if="settingsQuery.isLoading.value" class="card"><div class="card-body"><el-skeleton :rows="8" animated /></div></div>
        <el-result
          v-else-if="settingsQuery.isError.value || !settings"
          icon="warning"
          title="设置加载失败"
          :sub-title="describeApiError(settingsQuery.error.value)"
        >
          <template #extra><el-button type="primary" @click="settingsQuery.refetch()">重试</el-button></template>
        </el-result>
        <template v-else>
          <el-alert
            type="warning"
            :closable="false"
            show-icon
            title="数据目录 / 前端目录修改后需重启后端生效"
            description="SQLite 数据库引擎不会热切换；产物挂载会按新目录动态解析。"
            style="margin-bottom: 10px"
          />
          <div class="card">
            <div class="card-body">
              <div class="settings-grid">
                <div v-for="entry in settings.schema" :key="entry.key" class="setting-field">
                  <div class="field-label">
                    {{ entry.title }}
                    <StatusTag :tone="settingSourceMeta(sourceOf(entry)).tone">
                      {{ settingSourceMeta(sourceOf(entry)).label }}
                    </StatusTag>
                    <el-button
                      v-if="sourceOf(entry) !== 'default'"
                      size="small"
                      text
                      :icon="RefreshLeft"
                      title="删除数据库/文件层覆盖，回到默认值"
                      @click="deleteOverride(entry)"
                    />
                  </div>
                  <el-switch
                    v-if="entry.type === 'bool'"
                    v-model="values[entry.key]"
                    size="small"
                  />
                  <el-select
                    v-else-if="entry.type === 'enum'"
                    v-model="values[entry.key]"
                    size="small"
                    style="width: 100%"
                  >
                    <el-option
                      v-for="option in entry.options ?? []"
                      :key="option.value"
                      :value="option.value"
                      :label="option.label"
                    />
                  </el-select>
                  <el-input-number
                    v-else-if="entry.type === 'int'"
                    v-model="values[entry.key]"
                    size="small"
                    :min="entry.min"
                    :max="entry.max"
                    :step="1"
                    controls-position="right"
                    style="width: 100%"
                    @update:model-value="values[entry.key] = numberOrNull($event)"
                  />
                  <el-input v-else v-model="values[entry.key]" size="small" clearable class="code-text" />
                  <div v-if="entry.desc" class="unit">{{ entry.desc }}</div>
                </div>
              </div>
            </div>
            <div class="settings-savebar">
              <el-button
                type="primary"
                :icon="Refresh"
                :loading="updateMutation.isPending.value"
                @click="saveSettings"
              >
                保存设置
              </el-button>
              <el-button :icon="RefreshLeft" :loading="resetMutation.isPending.value" @click="resetSettings">
                恢复默认
              </el-button>
              <el-button @click="settingsQuery.refetch()">重新加载</el-button>
              <span class="spacer" />
              <span class="hint">只保存改动过的项 · 来源标记仅在覆盖时显示</span>
            </div>
          </div>
        </template>
      </el-tab-pane>

      <el-tab-pane label="工具链" name="tools">
        <div class="tool-grid">
          <section class="card tool-card">
            <div class="card-body">
              <div class="tool-head"><el-icon :size="18"><Setting /></el-icon><strong>ADB</strong>
                <StatusTag :tone="health?.adb ? 'green' : 'gray'">{{ health?.adb ? '正常' : '未知' }}</StatusTag>
              </div>
              <div class="tool-row"><span>路径</span><code>{{ health?.adb?.path ?? '—' }}</code></div>
              <div class="tool-row"><span>版本</span><code>{{ health?.adb?.version ?? '—' }}</code></div>
              <div class="tool-row"><span>在线设备</span><code>{{ health?.adb?.devices ?? '—' }}</code></div>
              <div class="tool-actions">
                <el-button size="small" :loading="healthQuery.isFetching.value" @click="healthQuery.refetch()">重新探测</el-button>
              </div>
            </div>
          </section>
          <section class="card tool-card">
            <div class="card-body">
              <div class="tool-head"><el-icon :size="18"><Setting /></el-icon><strong>uiautomator2</strong>
                <StatusTag tone="green">可用</StatusTag>
              </div>
              <div class="tool-row"><span>驱动</span><code>后端 U2Driver</code></div>
              <div class="tool-row"><span>设备服务</span><code>首次操作时检查/安装</code></div>
              <div class="tool-row"><span>备注</span><code>不在此页触发安装</code></div>
            </div>
          </section>
          <section class="card tool-card">
            <div class="card-body">
              <div class="tool-head"><el-icon :size="18"><Setting /></el-icon><strong>scrcpy-server</strong>
                <StatusTag tone="green">已锁定</StatusTag>
              </div>
              <div class="tool-row"><span>版本</span><code>5.0.1</code></div>
              <div class="tool-row"><span>本地 jar</span><code>vendor/scrcpy-server-v5.0.1.jar</code></div>
              <div class="tool-row"><span>校验</span><code>启动前 SHA256 校验/必要时重推</code></div>
            </div>
          </section>
          <section class="card tool-card">
            <div class="card-body">
              <div class="tool-head"><el-icon :size="18"><Setting /></el-icon><strong>数据与前端</strong>
                <StatusTag tone="amber">部分需重启</StatusTag>
              </div>
              <div class="tool-row"><span>数据目录</span><code>{{ health?.data_dir ?? '—' }}</code></div>
              <div class="tool-row"><span>产物挂载</span><code>/artifacts · 动态解析</code></div>
              <div class="tool-row"><span>前端</span><code>{{ APP_VERSION }} · {{ APP_COMMIT }}</code></div>
              <div class="tool-row"><span>页面构建</span><code>{{ formatDateTime(APP_BUILD_TIME) }}</code></div>
            </div>
          </section>
        </div>
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="排障顺序：ADB → uiautomator2 → scrcpy jar/版本"
          description="工具链状态缓存 5 秒，可手动刷新；健康信息由后端 /api/health 提供。"
          style="margin-top: 12px"
        />
      </el-tab-pane>

      <el-tab-pane label="维护" name="maintenance">
        <div class="card">
          <div class="card-head">
            <h2>存储统计</h2>
            <el-button size="small" :icon="Refresh" :loading="statsQuery.isFetching.value" @click="statsQuery.refetch()">
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
              <div class="settings-grid">
                <div class="kv"><span>DB 体积</span><strong>{{ formatBytes(stats.db_size) }}</strong></div>
                <div class="kv"><span>产物体积</span><strong>{{ formatBytes(stats.artifacts_size) }}</strong></div>
                <div class="kv"><span>运行数</span><strong>{{ stats.runs }}</strong></div>
                <div class="kv"><span>产物数</span><strong>{{ stats.artifacts }}</strong></div>
                <div class="kv"><span>最早运行</span><strong>{{ formatDateTime(stats.oldest_run_at) }}</strong></div>
                <div class="kv"><span>保留天数</span><strong>{{ stats.retention_days }}</strong></div>
              </div>
              <div class="maintenance-actions">
                <span class="small">保留天数</span>
                <el-input-number v-model="retentionDays" size="small" :min="1" :max="3650" controls-position="right" style="width: 120px" />
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
                <el-alert v-if="cleanupSummary" type="info" :closable="false" show-icon :title="cleanupSummary" />
                <pre class="pre-block" style="margin-top: 8px">{{ prettyJson(cleanupResult) }}</pre>
              </div>
            </template>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="健康" name="health">
        <div class="card">
          <div class="card-head">
            <h2>健康信息</h2>
            <el-button size="small" :icon="Refresh" :loading="healthQuery.isFetching.value" @click="healthQuery.refetch()">刷新</el-button>
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
                <StatusTag :tone="health.status === 'ok' ? 'green' : 'amber'">{{ health.status }}</StatusTag>
              </el-descriptions-item>
              <el-descriptions-item label="版本">{{ health.version }}</el-descriptions-item>
              <el-descriptions-item label="数据库"><span class="code-text">{{ health.db }}</span></el-descriptions-item>
              <el-descriptions-item label="adb">
                <span class="code-text">{{ health.adb?.path || '—' }} {{ health.adb?.version ? `· ${health.adb.version}` : '' }} · 设备 {{ health.adb?.devices ?? 0 }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="数据目录"><span class="code-text">{{ health.data_dir }}</span></el-descriptions-item>
              <el-descriptions-item label="后端构建">
                <span class="code-text">{{ health.build?.commit ?? '未知' }}{{ health.build?.dirty ? ' (+未提交改动)' : '' }} · {{ formatDateTime(health.build?.built_at) }}</span>
              </el-descriptions-item>
            </el-descriptions>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
