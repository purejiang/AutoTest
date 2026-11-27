<script setup lang="ts">
// 设置 → 存储：数据位置、保留策略、存储统计与清理。
import { Delete, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, ref, watch } from 'vue'

import { describeApiError } from '@/api/client'
import { useCleanup, useHealth, useMaintenanceStats } from '@/api/settings'
import { formatBytes, formatDateTime, prettyJson } from '@/lib/format'

import Notice from '@/components/Notice.vue'
import SettingField from '../SettingField.vue'
import { useSettingsDraftContext } from '../useSettingsDraft'

const { values, entriesFor, sourceOf, configFile, setValue, deleteOverride } = useSettingsDraftContext()

const healthQuery = useHealth()
const health = healthQuery.data
const statsQuery = useMaintenanceStats()
const stats = statsQuery.data
const cleanupMutation = useCleanup()
const cleanupResult = cleanupMutation.data

const retentionDays = ref<number | undefined>(undefined)
const dryRun = ref(true)

watch(
  () => stats.value,
  (data) => {
    if (data) retentionDays.value = data.retention_days
  },
  { immediate: true },
)

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
</script>

<template>
  <div class="settings-pane">
    <section class="card settings-card">
      <div class="card-head">
        <h2>数据位置</h2>
        <span class="card-hint" title="配置与脚本都是文件，可直接备份或纳入版本管理；改数据目录后重启生效。">
          配置文件 · 脚本 · 数据
        </span>
      </div>
      <div class="card-body">
        <div class="kv-grid">
          <div class="kv">
            <span>配置文件</span><strong class="code-text">{{ configFile || '—' }}</strong>
          </div>
          <div class="kv">
            <span>脚本目录</span><strong class="code-text">{{ health?.scripts_dir ?? '—' }}</strong>
          </div>
          <div class="kv">
            <span>数据目录</span><strong class="code-text">{{ health?.data_dir ?? '—' }}</strong>
          </div>
        </div>
        <div class="settings-note">
          配置和脚本都是普通文件（可直接备份 / 进 git）。数据目录是启动项：要改请编辑配置文件或设置
          <code class="code-text">AUTOTEST_DATA_DIR</code> 后重启。
        </div>
      </div>
    </section>

    <section class="card settings-card">
      <div class="card-head">
        <h2>保留策略</h2>
        <span class="card-hint" title="运行记录与产物的保留天数、报告体积上限。">保留天数 · 报告体积</span>
      </div>
      <div class="card-body">
        <div class="settings-grid">
          <SettingField
            v-for="entry in entriesFor('storage')"
            :key="entry.key"
            :entry="entry"
            :model-value="values[entry.key]"
            :source="sourceOf(entry)"
            :readonly="entry.layer === 'bootstrap'"
            :config-file="configFile"
            @update:model-value="setValue(entry.key, $event)"
            @clear="deleteOverride(entry)"
          />
        </div>
      </div>
    </section>

    <section class="card">
      <div class="card-head">
        <h2>存储统计</h2>
        <span class="card-hint" title="统计运行记录与产物占用，并清理过期运行。">运行/产物体积 · 清理</span>
        <el-button size="small" :icon="Refresh" :loading="statsQuery.isFetching.value" @click="statsQuery.refetch()">
          刷新统计
        </el-button>
      </div>
      <div class="card-body">
        <el-skeleton v-if="statsQuery.isLoading.value" :rows="4" animated />
        <Notice
          v-else-if="statsQuery.isError.value"
          severity="warning"
          title="统计加载失败"
        >
          {{ describeApiError(statsQuery.error.value) }}
        </Notice>
        <template v-else-if="stats">
          <div class="kv-grid">
            <div class="kv">
              <span>运行记录体积</span><strong>{{ formatBytes(stats.db_size) }}</strong>
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
            <Notice v-if="cleanupSummary" severity="info" variant="compact">{{ cleanupSummary }}</Notice>
            <pre class="pre-block" style="margin-top: 8px">{{ prettyJson(cleanupResult) }}</pre>
          </div>
        </template>
      </div>
    </section>
  </div>
</template>
