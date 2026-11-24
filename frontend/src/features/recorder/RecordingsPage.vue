<script setup lang="ts">
import { Delete, InfoFilled, Plus, Refresh, VideoCamera } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox, type TableInstance } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { describeApiError } from '@/api/client'
import { useBulkDeleteRecordings, useDeleteRecording, useRecordings } from '@/api/recordings'
import type { Recording, RecordingMode, RecordingStatus } from '@/types/api'
import {
  formatShortDateTime,
  recordingModeMeta,
  recordingStatusMeta,
} from '@/lib/format'
import { notifyBulkDeleteResult } from '@/lib/bulk'
import StatusTag from '@/components/StatusTag.vue'
import CreateRecordingDialog from './CreateRecordingDialog.vue'

type ModeFilter = 'all' | RecordingMode
type StatusFilter = 'all' | RecordingStatus

const router = useRouter()
const route = useRoute()
const page = ref(1)
const PAGE_SIZE = 20

const modeFilter = ref<ModeFilter>('all')
const statusFilter = ref<StatusFilter>('all')

// 模式 / 状态都下推到后端，分页按接口 total 做服务端 offset。
const listParams = computed(() => ({
  status: statusFilter.value === 'all' ? undefined : statusFilter.value,
  mode: modeFilter.value === 'all' ? undefined : modeFilter.value,
  limit: PAGE_SIZE,
  offset: (page.value - 1) * PAGE_SIZE,
}))
const recordingsQuery = useRecordings(listParams, { refetchInterval: 5000 })
const deleteMutation = useDeleteRecording()
const bulkDeleteMutation = useBulkDeleteRecordings()
const createOpen = ref(false)
const recordingsTableRef = ref<TableInstance>()
const selectedRecordings = ref<Recording[]>([])

const recordings = computed(() => recordingsQuery.data.value?.items ?? [])
/** 接口 total（带当前筛选），不要用 items.length 当数量。 */
const total = computed(() => recordingsQuery.data.value?.total ?? 0)
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const paged = computed(() => recordings.value)
const emptyDescription = computed(() =>
  modeFilter.value === 'all' && statusFilter.value === 'all'
    ? '还没有录制草稿。去控制台在镜像上开启录制，或新建真机录制。'
    : '没有匹配的录制草稿，调整筛选或新建真机录制。',
)

function isSelectableRecording(recording: Recording): boolean {
  return recording.status === 'stopped'
}

function handleSelectionChange(rows: Recording[]): void {
  selectedRecordings.value = rows
}

function clearSelection(): void {
  recordingsTableRef.value?.clearSelection()
  selectedRecordings.value = []
}

async function refreshRecordings(): Promise<void> {
  clearSelection()
  await recordingsQuery.refetch()
}

function goConsoleRecord(): void {
  void router.push({ path: '/console', query: { tab: 'manual', manual: 'record' } })
}

async function bulkDeleteRecordings(): Promise<void> {
  const ids = selectedRecordings.value.map((recording) => recording.id)
  if (ids.length === 0) return
  try {
    await ElMessageBox.confirm(
      `删除选中的 ${ids.length} 条录制？录制事件会一并删除；单条失败不会阻塞其他条。`,
      '批量删除录制',
      { type: 'warning', confirmButtonText: '删除选中', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const result = await bulkDeleteMutation.mutateAsync(ids)
    notifyBulkDeleteResult(result, '录制')
    clearSelection()
    await recordingsQuery.refetch()
  } catch {
    // 全局错误提示
  }
}

watch([modeFilter, statusFilter], () => {
  page.value = 1
  clearSelection()
})

watch(page, () => clearSelection())

watch(pageCount, (maxPage) => {
  if (page.value > maxPage) page.value = maxPage
})

function onCreated(recording: Recording): void {
  void router.push(`/recorder/${recording.id}`)
}

function openPhysicalFromQuery(): void {
  if (route.query.new !== 'physical') return
  createOpen.value = true
  const nextQuery = { ...route.query }
  delete nextQuery.new
  void router.replace({ path: route.path, query: nextQuery })
}

watch(() => route.query.new, () => openPhysicalFromQuery(), { immediate: true })

async function remove(recording: Recording): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `删除录制「${recording.name || recording.id}」？录制事件会一并删除。`,
      '删除录制',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteMutation.mutateAsync(recording.id)
    ElMessage.success('录制已删除')
    clearSelection()
    await recordingsQuery.refetch()
  } catch {
    // 全局错误提示
  }
}
</script>

<template>
  <div class="page">
    <h1 class="visually-hidden">录制草稿</h1>
    <div class="recording-notice">
      <el-icon><InfoFilled /></el-icon>
      <span>
        本页只收录<strong>操作录制草稿</strong>（镜像录制 / 真机录制）。视频录制与设备录屏不进入此列表，
        <strong>视频刷新即丢失</strong>。
      </span>
      <span class="spacer" />
      <el-button
        size="small"
        :icon="Refresh"
        :loading="recordingsQuery.isFetching.value"
        title="刷新"
        aria-label="刷新"
        @click="refreshRecordings"
      />
      <el-button size="small" type="primary" :icon="Plus" @click="createOpen = true">新建真机录制</el-button>
      <el-button size="small" :icon="VideoCamera" @click="goConsoleRecord">去控制台录制</el-button>
    </div>

    <el-alert
      v-if="recordingsQuery.isError.value"
      type="error"
      show-icon
      :closable="false"
      title="录制列表加载失败"
    >
      <template #default>
        <div class="row wrap">
          <span>{{ describeApiError(recordingsQuery.error.value) }}</span>
          <el-button size="small" @click="refreshRecordings">重试</el-button>
        </div>
      </template>
    </el-alert>

    <div class="card table-card">
      <div class="recording-toolbar">
        <el-select v-model="modeFilter" size="small" style="width: 140px">
          <el-option label="全部模式" value="all" />
          <el-option label="镜像录制" value="web" />
          <el-option label="真机录制" value="physical" />
        </el-select>
        <el-select v-model="statusFilter" size="small" style="width: 140px">
          <el-option label="全部状态" value="all" />
          <el-option label="录制中" value="recording" />
          <el-option label="已暂停" value="paused" />
          <el-option label="已停止" value="stopped" />
        </el-select>
        <span class="spacer" />
        <span class="hint">录制停止后可查看事件并转成脚本。</span>
      </div>

      <div v-if="selectedRecordings.length > 0" class="batch-bar">
        <strong>已选 {{ selectedRecordings.length }} 条</strong>
        <span class="small">录制中 / 暂停中的录制不可删除</span>
        <span class="spacer" />
        <el-button
          size="small"
          type="danger"
          :loading="bulkDeleteMutation.isPending.value"
          @click="bulkDeleteRecordings"
        >
          删除选中
        </el-button>
        <el-button size="small" @click="clearSelection">取消</el-button>
      </div>

      <el-table
        ref="recordingsTableRef"
        v-loading="recordingsQuery.isLoading.value"
        :data="paged"
        row-key="id"
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="36" :selectable="isSelectableRecording" />
        <el-table-column label="名称 / ID" min-width="220">
          <template #default="{ row }">
            <button type="button" class="cell-link" @click="router.push(`/recorder/${row.id}`)">
              {{ row.name || row.id }}
            </button>
            <div class="cell-sub">{{ row.id }}</div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="88">
          <template #default="{ row }">
            <StatusTag :tone="recordingStatusMeta(row.status).tone">{{ recordingStatusMeta(row.status).label }}</StatusTag>
          </template>
        </el-table-column>
        <el-table-column label="模式" width="100">
          <template #default="{ row }">
            <StatusTag :tone="recordingModeMeta(row.mode).tone">{{ recordingModeMeta(row.mode).label }}</StatusTag>
          </template>
        </el-table-column>
        <el-table-column label="设备" width="130">
          <template #default="{ row }"><span class="code-text small">{{ row.serial || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="事件" width="70">
          <template #default="{ row }">{{ row.event_count ?? 0 }}</template>
        </el-table-column>
        <el-table-column label="开始时间" width="130">
          <template #default="{ row }"><span class="small muted">{{ formatShortDateTime(row.started_at) }}</span></template>
        </el-table-column>
        <el-table-column label="关联脚本" min-width="170">
          <template #default="{ row }">
            <button
              v-if="row.script_id"
              type="button"
              class="cell-link"
              style="font-size: 11.5px"
              @click="router.push(`/scripts/${row.script_id}`)"
            >
              {{ row.script_name || row.script_id }}
            </button>
            <span v-else class="muted small">尚未生成</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="112" align="right">
          <template #default="{ row }">
            <div class="actions-cell">
              <el-button link type="primary" @click="router.push(`/recorder/${row.id}`)">详情</el-button>
              <el-button
                link
                type="danger"
                :icon="Delete"
                title="删除录制"
                :disabled="row.status === 'recording' || row.status === 'paused'"
                @click="remove(row)"
              />
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :description="emptyDescription">
            <div class="row" style="justify-content: center">
              <el-button type="primary" :icon="VideoCamera" @click="goConsoleRecord">去控制台录制</el-button>
              <el-button :icon="Plus" @click="createOpen = true">新建真机录制</el-button>
            </div>
          </el-empty>
        </template>
      </el-table>
      <div class="table-foot">
        <span>共 {{ total }} 条录制</span>
        <el-pagination
          v-model:current-page="page"
          :page-size="PAGE_SIZE"
          :total="total"
          layout="prev, pager, next"
          small
          background
        />
      </div>
    </div>

    <CreateRecordingDialog :open="createOpen" mode="physical" @close="createOpen = false" @created="onCreated" />
  </div>
</template>

<style scoped>
.recording-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 9px 11px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 11px;
}

.recording-notice .spacer {
  flex: 1;
}

.recording-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 10px;
  border-bottom: 1px solid var(--line);
}

.recording-toolbar .spacer {
  flex: 1;
}

@media (max-width: 900px) {
  .recording-notice {
    align-items: flex-start;
  }
}
</style>
