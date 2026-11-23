<script setup lang="ts">
import { Delete, Refresh, Search } from '@element-plus/icons-vue'
import { useDebounceFn } from '@vueuse/core'
import { ElMessage, ElMessageBox, type TableInstance } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { describeApiError } from '@/api/client'
import { useBulkDeleteRuns, useDeleteRun, useRuns } from '@/api/runs'
import type { Run } from '@/types/api'
import {
  RUN_STATUS_OPTIONS,
  formatDateTime,
  formatDuration,
  isActiveRunStatus,
  runDurationMs,
  runStatusMeta,
} from '@/lib/format'
import { notifyBulkDeleteResult } from '@/lib/bulk'
import StatusTag from '@/components/StatusTag.vue'

const emit = defineEmits<{ browseLibrary: [] }>()

const router = useRouter()
const PAGE_SIZE = 20
const status = ref('')
const page = ref(1)
const searchInput = ref('')
const search = ref('')

// 无搜索时服务端分页；有脚本/设备搜索时拉最近 100 条做客户端过滤（后端未提供搜索参数）。
const runsQuery = useRuns(
  computed(() =>
    search.value
      ? { status: status.value || undefined, limit: 100, offset: 0 }
      : {
          status: status.value || undefined,
          limit: PAGE_SIZE,
          offset: (page.value - 1) * PAGE_SIZE,
        },
  ),
  { refetchInterval: 5000 },
)
const deleteMutation = useDeleteRun()
const bulkDeleteMutation = useBulkDeleteRuns()
const runsTableRef = ref<TableInstance>()
const selectedRuns = ref<Run[]>([])

const runs = computed(() => runsQuery.data.value?.items ?? [])
const total = computed(() => runsQuery.data.value?.total ?? 0)

// 脚本 / 设备搜索是客户端过滤；分页只在过滤后的结果上切片。
const filteredRuns = computed(() => {
  const keyword = search.value
  if (!keyword) return runs.value
  return runs.value.filter((run) =>
    [run.script_name, run.script_id, run.device_serial]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(keyword)),
  )
})
const pagedRuns = computed(() =>
  search.value
    ? filteredRuns.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE)
    : runs.value,
)
const paginationTotal = computed(() => (search.value ? filteredRuns.value.length : total.value))

const updateSearch = useDebounceFn((value: string) => {
  search.value = value.trim().toLowerCase()
  page.value = 1
  clearSelection()
}, 200)

function onSearchInput(value: string): void {
  searchInput.value = value
  void updateSearch(value)
}

function isSelectableRun(run: Run): boolean {
  return !isActiveRunStatus(run.status)
}

function handleSelectionChange(rows: Run[]): void {
  selectedRuns.value = rows
}

function clearSelection(): void {
  runsTableRef.value?.clearSelection()
  selectedRuns.value = []
}

async function refreshRuns(): Promise<void> {
  clearSelection()
  await runsQuery.refetch()
}

async function bulkDeleteRuns(): Promise<void> {
  const ids = selectedRuns.value.map((run) => run.id)
  if (ids.length === 0) return
  try {
    await ElMessageBox.confirm(
      `删除选中的 ${ids.length} 条运行？运行记录、步骤、事件与产物记录会一并删除；单条失败不会阻塞其他条。`,
      '批量删除运行',
      { type: 'warning', confirmButtonText: '删除选中', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const result = await bulkDeleteMutation.mutateAsync(ids)
    notifyBulkDeleteResult(result, '运行')
    clearSelection()
    await runsQuery.refetch()
  } catch {
    // 全局错误提示
  }
}

watch([page, status], () => clearSelection())

function progressOf(run: Run): number {
  const totalSteps = run.total_steps || 0
  if (totalSteps <= 0) return 0
  return Math.min(100, Math.round(((run.step_index ?? 0) / totalSteps) * 100))
}

function progressTone(run: Run): string {
  const meta = runStatusMeta(run.status)
  if (meta.tone === 'green') return 'green'
  if (meta.tone === 'red') return 'red'
  if (meta.tone === 'amber') return 'amber'
  return ''
}

function pushRun(run: Run): void {
  void router.push(`/runs/${run.id}`)
}

function onRowClick(row: Run, column?: { type?: string }): void {
  if (column?.type === 'selection') return
  pushRun(row)
}

async function removeRun(run: Run): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `删除运行 #${run.id}？运行记录、步骤、事件与产物记录会被删除。`,
      '删除运行',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteMutation.mutateAsync(run.id)
    ElMessage.success(`已删除运行 #${run.id}`)
    clearSelection()
  } catch {
    // 全局错误提示
  }
}

function handleStatusChange(): void {
  page.value = 1
  clearSelection()
}
</script>

<template>
  <div class="run-history-panel">
    <el-alert
      v-if="runsQuery.isError.value"
      type="error"
      show-icon
      :closable="false"
      title="运行记录加载失败"
    >
      <template #default>
        <div class="row wrap">
          <span>{{ describeApiError(runsQuery.error.value) }}</span>
          <el-button size="small" @click="refreshRuns">重试</el-button>
        </div>
      </template>
    </el-alert>

    <div class="card table-card">
      <div class="panel-toolbar">
        <el-select v-model="status" clearable placeholder="全部状态" style="width: 140px" @change="handleStatusChange">
          <el-option v-for="option in RUN_STATUS_OPTIONS" :key="option.value" :value="option.value" :label="option.label" />
        </el-select>
        <el-input
          :model-value="searchInput"
          placeholder="搜索脚本 / 设备"
          clearable
          :prefix-icon="Search"
          style="width: 220px"
          @update:model-value="onSearchInput"
        />
        <span class="spacer" />
        <span class="hint">运行记录保留脚本快照，脚本修改后仍可回看。</span>
        <el-tooltip content="刷新运行记录" placement="bottom">
          <el-button :icon="Refresh" :loading="runsQuery.isFetching.value" @click="refreshRuns" />
        </el-tooltip>
      </div>

      <div v-if="selectedRuns.length > 0" class="batch-bar">
        <strong>已选 {{ selectedRuns.length }} 条</strong>
        <span class="small">运行中/暂停中的运行不可删除</span>
        <span class="spacer" />
        <el-button
          size="small"
          type="danger"
          :loading="bulkDeleteMutation.isPending.value"
          @click="bulkDeleteRuns"
        >
          删除选中
        </el-button>
        <el-button size="small" @click="clearSelection">取消</el-button>
      </div>

      <el-table
        ref="runsTableRef"
        v-loading="runsQuery.isLoading.value"
        :data="pagedRuns"
        row-key="id"
        @selection-change="handleSelectionChange"
        @row-click="onRowClick"
      >
        <el-table-column type="selection" width="36" :selectable="isSelectableRun" />
        <el-table-column label="ID" width="72">
          <template #default="{ row }">
            <button type="button" class="cell-link code-text" @click.stop="pushRun(row)">#{{ row.id }}</button>
          </template>
        </el-table-column>
        <el-table-column label="脚本" min-width="200">
          <template #default="{ row }">
            <div class="cell-main">{{ row.script_name || row.script_id || '—' }}</div>
            <div class="cell-sub">{{ row.script_id ?? '' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="设备" width="140">
          <template #default="{ row }"><span class="code-text small">{{ row.device_serial || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="94">
          <template #default="{ row }">
            <StatusTag :tone="runStatusMeta(row.status).tone">{{ runStatusMeta(row.status).label }}</StatusTag>
          </template>
        </el-table-column>
        <el-table-column label="进度" width="150">
          <template #default="{ row }">
            <div class="bar-row">
              <div class="progress" :class="progressTone(row)"><span :style="{ width: `${progressOf(row)}%` }" /></div>
              <span class="small">{{ row.step_index ?? 0 }}/{{ row.total_steps ?? 0 }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="开始时间" width="150">
          <template #default="{ row }"><span class="small muted">{{ formatDateTime(row.started_at) }}</span></template>
        </el-table-column>
        <el-table-column label="耗时" width="90">
          <template #default="{ row }"><span class="small">{{ formatDuration(runDurationMs(row)) }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="134" align="right">
          <template #default="{ row }">
            <div class="actions-cell" @click.stop>
              <el-button v-if="isActiveRunStatus(row.status)" link type="primary" @click="pushRun(row)">实时</el-button>
              <el-button link type="primary" @click="pushRun(row)">详情</el-button>
              <el-button
                link
                type="danger"
                :icon="Delete"
                :disabled="isActiveRunStatus(row.status)"
                title="删除运行"
                @click="removeRun(row)"
              />
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :description="status || search ? '没有匹配的运行记录' : '还没有运行记录'">
            <el-button type="primary" @click="emit('browseLibrary')">去脚本库发起运行</el-button>
          </el-empty>
        </template>
      </el-table>

      <div class="table-foot">
        <span>
          共 {{ total }} 条运行
          <span v-if="search || status"> · 当前筛选 {{ filteredRuns.length }} 条</span>
        </span>
        <el-pagination
          v-model:current-page="page"
          :page-size="PAGE_SIZE"
          :total="paginationTotal"
          layout="prev, pager, next"
          small
          background
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.run-history-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.panel-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
}

.panel-toolbar .spacer {
  flex: 1;
}
</style>
