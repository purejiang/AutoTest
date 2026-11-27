<script setup lang="ts">
import { CopyDocument, Refresh, VideoPause, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, ref, watch } from 'vue'

import { describeApiError } from '@/api/client'
import { useDeviceLogcat } from '@/api/devices'
import { useSettings } from '@/api/settings'
import { useLogcatCaptureStatus, useStartLogcatCapture, useStopLogcatCapture, type LogcatStopResult } from '@/api/logcat'
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

const settingsQuery = useSettings()
// 总开关关闭时脚本与手动采集都不可用；最近日志查看不受影响。
const logcatEnabled = computed(
  () => settingsQuery.data.value?.effective?.['capture.logcat_auto_attach'] === true,
)
const logcatKnown = computed(() => settingsQuery.data.value !== undefined)

const statusQuery = useLogcatCaptureStatus(computed(() => props.serial), {
  enabled: computed(() => props.open),
  refetchInterval: computed(() => (ui.logcatAutoRefresh ? 2000 : false)),
})
const capture = computed(() => statusQuery.data.value ?? null)
const active = computed(() => Boolean(capture.value?.active))
const captureLines = computed(() => (active.value ? capture.value?.lines ?? 0 : 0))
const startMutation = useStartLogcatCapture(computed(() => props.serial))
const stopMutation = useStopLogcatCapture(computed(() => props.serial))

// 停止接口的即时返回值：status 刷新前也先显示刚保存的路径
const lastSaved = ref<LogcatStopResult | null>(null)
watch(
  () => props.serial,
  () => {
    lastSaved.value = null
  },
)

const savedPath = computed(() => {
  if (active.value) return ''
  return (
    lastSaved.value?.path ??
    capture.value?.path ??
    capture.value?.last?.path ??
    ''
  )
})
const savedSize = computed(() => {
  if (active.value) return 0
  return lastSaved.value?.size ?? capture.value?.size ?? capture.value?.last?.size ?? 0
})

async function copyLogs(): Promise<void> {
  try {
    await navigator.clipboard.writeText(lines.value.join('\n'))
    ElMessage.success('logcat 已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败（浏览器不允许剪贴板）')
  }
}

async function copySavedPath(): Promise<void> {
  try {
    await navigator.clipboard.writeText(savedPath.value)
    ElMessage.success('保存路径已复制')
  } catch {
    ElMessage.error('复制失败（浏览器不允许剪贴板）')
  }
}

async function refreshAll(): Promise<void> {
  await Promise.allSettled([query.refetch(), statusQuery.refetch()])
}

async function startCapture(): Promise<void> {
  try {
    const result = await startMutation.mutateAsync({})
    lastSaved.value = null
    ElMessage.success(result.package_filter ? '已开始采集 logcat（按应用 PID 过滤）' : '已开始采集 logcat')
    await statusQuery.refetch()
  } catch (error) {
    ElMessage.error(describeApiError(error))
  }
}

async function stopCapture(): Promise<void> {
  try {
    const result = await stopMutation.mutateAsync()
    lastSaved.value = result
    if (result.path) {
      ElMessage.success(`logcat 已保存（${result.lines ?? 0} 行）`)
    } else {
      ElMessage.warning('采集已停止，但没有生成日志文件')
    }
    // 停止后刷新最近日志（兜底 adb logcat -d 也能看到尾部）
    await Promise.allSettled([statusQuery.refetch(), query.refetch()])
  } catch (error) {
    ElMessage.error(describeApiError(error))
  }
}

function formatSize(size: number): string {
  if (!size) return '0 B'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
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

    <div class="capture-bar">
      <el-tag :type="active ? 'success' : 'info'" size="small" effect="plain">
        {{ active ? '采集中' : '未采集' }}
      </el-tag>
      <span class="hint">
        缓冲 {{ captureLines }} 行<template v-if="active && capture?.max_lines"> · 上限 {{ capture.max_lines }}</template>
      </span>
      <span v-if="active && capture?.package_filter" class="hint">
        仅 {{ (capture.packages ?? []).join('、') || capture.package }} 的 PID
      </span>
      <span v-if="active && capture?.started_at" class="hint">开始于 {{ capture.started_at }}</span>
      <span class="spacer" />
      <el-button
        v-if="!active"
        size="small"
        type="primary"
        :icon="VideoPlay"
        :loading="startMutation.isPending.value"
        :disabled="!serial || !logcatEnabled"
        @click="startCapture"
      >
        开始采集
      </el-button>
      <el-button
        v-else
        size="small"
        type="danger"
        :icon="VideoPause"
        :loading="stopMutation.isPending.value"
        @click="stopCapture"
      >
        停止并保存
      </el-button>
    </div>

    <div v-if="logcatKnown && !logcatEnabled" class="hint" style="margin-bottom: 8px">
      全局未开启 logcat 采集：先到
      <RouterLink to="/settings?tab=execution">设置 → 执行 → 采集</RouterLink>
      打开「允许采集 logcat」。最近日志仍可查看。
    </div>

    <div class="row" style="margin-bottom: 8px; flex-wrap: wrap">
      <el-switch v-model="ui.logcatAutoRefresh" inline-prompt active-text="自动刷新" inactive-text="手动" />
      <span class="spacer" />
      <el-button size="small" :icon="CopyDocument" :disabled="!lines.length" @click="copyLogs">复制</el-button>
      <el-button
        size="small"
        :icon="Refresh"
        :loading="query.isFetching.value || statusQuery.isFetching.value"
        @click="refreshAll"
      >
        刷新
      </el-button>
    </div>

    <div v-if="savedPath" class="saved-box">
      <span class="saved-label">已保存</span>
      <span class="code-text saved-path" :title="savedPath">{{ savedPath }}</span>
      <span class="hint">{{ formatSize(savedSize) }}</span>
      <el-button link type="primary" size="small" @click="copySavedPath">复制路径</el-button>
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

<style scoped>
.capture-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-lighter);
}

.saved-box {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
  padding: 6px 10px;
  border-left: 3px solid var(--el-color-success);
  border-radius: 4px;
  background: var(--el-color-success-light-9);
}

.saved-label {
  flex: 0 0 auto;
  color: var(--el-color-success);
  font-size: 12px;
  font-weight: 600;
}

.saved-path {
  flex: 1 1 260px;
  min-width: 0;
  overflow: hidden;
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
