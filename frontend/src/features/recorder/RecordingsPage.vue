<script setup lang="ts">
import { Delete, Plus, Refresh, VideoCamera } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox, type TableInstance } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { describeApiError } from '@/api/client'
import { useBulkDeleteRecordings, useDeleteRecording, useRecordings } from '@/api/recordings'
import type { Recording } from '@/types/api'
import { formatDateTime, recordingModeMeta, recordingStatusMeta } from '@/lib/format'
import { notifyBulkDeleteResult } from '@/lib/bulk'
import PageHead from '@/components/PageHead.vue'
import StatusTag from '@/components/StatusTag.vue'
import CreateRecordingDialog from './CreateRecordingDialog.vue'

const router = useRouter()
const page = ref(1)
const PAGE_SIZE = 20
const recordingsQuery = useRecordings({ limit: 100 }, { refetchInterval: 5000 })
const deleteMutation = useDeleteRecording()
const bulkDeleteMutation = useBulkDeleteRecordings()
const createOpen = ref(false)
const recordingsTableRef = ref<TableInstance>()
const selectedRecordings = ref<Recording[]>([])

const recordings = computed(() => recordingsQuery.data.value?.items ?? [])
const total = computed(() => recordingsQuery.data.value?.total ?? 0)
const paged = computed(() => recordings.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE))

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

watch(page, () => clearSelection())

function onCreated(recording: Recording): void {
  void router.push(`/recorder/${recording.id}`)
}

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
  } catch {
    // 全局错误提示
  }
}
</script>

<template>
  <div class="page">
    <PageHead
      title="录制草稿"
      :count="total"
      subtitle="镜像录制在设备页镜像工具条开始；真机录制作为辅助"
    >
      <template #actions>
        <el-button :icon="Refresh" :loading="recordingsQuery.isFetching.value" @click="refreshRecordings">
          刷新
        </el-button>
        <el-button :icon="VideoCamera" @click="router.push('/devices')">去设备页录制</el-button>
        <el-button type="primary" :icon="Plus" @click="createOpen = true">新建真机录制</el-button>
      </template>
    </PageHead>

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
      <div v-if="selectedRecordings.length > 0" class="batch-bar">
        <strong>已选 {{ selectedRecordings.length }} 条</strong>
        <span class="small">录制中/暂停中的录制不可删除</span>
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
        <el-table-column label="模式" width="96">
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
        <el-table-column label="开始时间" width="150">
          <template #default="{ row }"><span class="small muted">{{ formatDateTime(row.started_at) }}</span></template>
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
                :disabled="row.status === 'recording' || row.status === 'paused'"
                @click="remove(row)"
              />
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有录制草稿。去设备页在镜像上开启录制，或新建真机录制。">
            <div class="row" style="justify-content: center">
              <el-button type="primary" @click="router.push('/devices')">去设备页录制</el-button>
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
          :total="recordings.length"
          layout="prev, pager, next"
          small
          background
        />
      </div>
    </div>

    <CreateRecordingDialog :open="createOpen" mode="physical" @close="createOpen = false" @created="onCreated" />
  </div>
</template>
