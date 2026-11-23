<script setup lang="ts">
import { CopyDocument, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed } from 'vue'

import { describeApiError } from '@/api/client'
import { useDeviceLogcat } from '@/api/devices'
import { useUiStore } from '@/stores/ui'

const props = defineProps<{
  serial: string | null
  open: boolean
}>()

const emit = defineEmits<{ close: [] }>()
const ui = useUiStore()

const query = useDeviceLogcat(computed(() => props.serial), 200, {
  enabled: computed(() => props.open),
  refetchInterval: computed(() => (ui.logcatAutoRefresh ? 3000 : false)),
})
const lines = computed(() => query.data.value ?? [])

async function copyLogs(): Promise<void> {
  try {
    await navigator.clipboard.writeText(lines.value.join('\n'))
    ElMessage.success('logcat 已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败（浏览器不允许剪贴板）')
  }
}
</script>

<template>
  <el-drawer :model-value="open" size="760px" append-to-body @update:model-value="!$event && emit('close')">
    <template #header>
      <div class="row">
        <strong>logcat</strong>
        <span class="code-text muted">{{ serial ?? '' }}</span>
        <span class="hint">最近 {{ lines.length }} 行</span>
      </div>
    </template>
    <div class="row" style="margin-bottom: 8px; flex-wrap: wrap">
      <el-switch v-model="ui.logcatAutoRefresh" inline-prompt active-text="自动刷新" inactive-text="手动" />
      <span class="spacer" />
      <el-button size="small" :icon="CopyDocument" :disabled="!lines.length" @click="copyLogs">复制</el-button>
      <el-button size="small" :icon="Refresh" :loading="query.isFetching.value" @click="query.refetch()">刷新</el-button>
    </div>
    <el-skeleton v-if="query.isLoading.value" :rows="10" animated />
    <el-alert
      v-else-if="query.isError.value"
      type="error"
      :closable="false"
      show-icon
      title="logcat 获取失败"
      :description="describeApiError(query.error.value)"
    />
    <el-empty v-else-if="lines.length === 0" description="暂无日志" />
    <pre v-else class="logcat-pre">{{ lines.join('\n') }}</pre>
  </el-drawer>
</template>
