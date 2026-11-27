<script setup lang="ts">
import { Delete, Download, Plus } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, ref } from 'vue'

import { describeApiError } from '@/api/client'
import { downloadReport, useDeleteReport, useGenerateReport, useReports } from '@/api/reports'
import type { Report } from '@/types/api'
import { formatBytes, formatDateTime } from '@/lib/format'
import StatusTag from '@/components/StatusTag.vue'
import Notice from '@/components/Notice.vue'

const props = defineProps<{ runId: number }>()
const reportsQuery = useReports(computed(() => props.runId))
const generateMutation = useGenerateReport()
const deleteMutation = useDeleteReport()
const includeVideo = ref(false)
const reports = computed(() => reportsQuery.data.value ?? [])

async function generate(): Promise<void> {
  try {
    const report = await generateMutation.mutateAsync({ run_id: props.runId, include_video: includeVideo.value })
    ElMessage.success(`报告已生成：${report.id}`)
  } catch {
    // 全局错误提示
  }
}

async function remove(report: Report): Promise<void> {
  try {
    await ElMessageBox.confirm('删除该报告？', '删除报告', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deleteMutation.mutateAsync(report.id)
    ElMessage.success('报告已删除')
  } catch {
    // 全局错误提示
  }
}
</script>

<template>
  <div>
    <div class="row wrap" style="margin-bottom: 8px">
      <el-checkbox v-model="includeVideo" size="small">包含视频</el-checkbox>
      <el-button
        size="small"
        type="primary"
        :icon="Plus"
        :loading="generateMutation.isPending.value"
        @click="generate"
      >
        生成报告
      </el-button>
    </div>

    <Notice
      v-if="reportsQuery.isError.value"
      severity="warning"
      title="报告列表加载失败"
      style="margin-bottom: 8px"
    >
      {{ describeApiError(reportsQuery.error.value) }}
    </Notice>

    <el-empty v-if="reports.length === 0" description="还没有报告" :image-size="48" />
    <el-table v-else :data="reports" row-key="id" size="small">
      <el-table-column label="创建时间" min-width="130">
        <template #default="{ row }"><span class="small">{{ formatDateTime(row.created_at) }}</span></template>
      </el-table-column>
      <el-table-column label="大小" width="78">
        <template #default="{ row }"><span class="small">{{ formatBytes(row.size) }}</span></template>
      </el-table-column>
      <el-table-column label="状态" width="76">
        <template #default="{ row }">
          <StatusTag :tone="row.status === 'ready' ? 'green' : 'gray'">{{ row.status }}</StatusTag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="104" align="right">
        <template #default="{ row }">
          <div class="actions-cell">
            <el-button link type="primary" :icon="Download" @click="downloadReport(row.id, `${row.id}.zip`)">
              下载
            </el-button>
            <el-button link type="danger" :icon="Delete" @click="remove(row)" />
          </div>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>
