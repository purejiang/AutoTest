<script setup lang="ts">
// 手动控制 → 应用：launch / stop / clear / restart，包含 package 远程搜索。
import { Link } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, ref } from 'vue'

import { useDeviceAppAction, useDeviceApps } from '@/api/devices'
import { prettyJson } from '@/lib/format'

const props = defineProps<{ serial: string; busy: boolean; active: boolean }>()

const APP_ACTIONS = [
  { value: 'launch', label: '启动 launch' },
  { value: 'stop', label: '停止 stop' },
  { value: 'clear', label: '清除数据 clear' },
  { value: 'restart', label: '重启 restart' },
] as const

const appAction = ref<'launch' | 'stop' | 'clear' | 'restart'>('launch')
const packageName = ref('')
const activity = ref('')
const appKeyword = ref('')
const appResult = ref<string | null>(null)

const appMutation = useDeviceAppAction(props.serial)
const appsQuery = useDeviceApps(computed(() => props.serial), {
  keyword: appKeyword,
  enabled: computed(() => props.active),
})
const appOptions = computed(() =>
  (appsQuery.data.value ?? []).map((app) => ({
    value: app.package,
    label: app.label ? `${app.label} · ${app.package}` : app.package,
  })),
)

function runApp(): void {
  if (!packageName.value.trim()) {
    ElMessage.warning('请输入 package（如 com.android.settings）')
    return
  }
  appMutation.mutate(
    {
      action: appAction.value,
      package: packageName.value.trim(),
      activity: activity.value.trim() || undefined,
    },
    {
      onSuccess: (data) => {
        appResult.value = prettyJson(data)
        ElMessage.success(`${appAction.value} 已执行`)
      },
    },
  )
}
</script>

<template>
  <section class="manual-page">
    <div class="section-head">
      <h3>
        <el-icon><Link /></el-icon>拉起应用
      </h3>
    </div>
    <div class="field-row">
      <label class="field">
        <span>操作</span>
        <el-select v-model="appAction" size="small" style="width: 100%">
          <el-option
            v-for="option in APP_ACTIONS"
            :key="option.value"
            :value="option.value"
            :label="option.label"
          />
        </el-select>
      </label>
      <label class="field">
        <span>package</span>
        <el-select
          v-model="packageName"
          size="small"
          filterable
          remote
          allow-create
          default-first-option
          clearable
          :remote-method="(value: string) => (appKeyword = value)"
          :loading="appsQuery.isFetching.value"
          placeholder="com.android.settings"
          style="width: 100%"
        >
          <el-option
            v-for="option in appOptions"
            :key="option.value"
            :value="option.value"
            :label="option.label"
          />
        </el-select>
      </label>
    </div>
    <label v-if="appAction === 'launch' || appAction === 'restart'" class="field" style="margin-top: 8px">
      <span>activity（可选）</span>
      <el-input v-model="activity" size="small" class="code-text" placeholder=".MainActivity" />
    </label>
    <el-button
      type="primary"
      size="small"
      style="margin-top: 9px"
      :loading="appMutation.isPending.value"
      :disabled="busy"
      @click="runApp"
    >
      执行
    </el-button>
    <div class="section-sub">清数据（clear）会清空应用数据，请谨慎操作。</div>
    <pre v-if="appResult" class="pre-block" style="margin-top: 8px">{{ appResult }}</pre>
  </section>
</template>
