<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { useDevices } from '@/api/devices'
import { useCreateRecording } from '@/api/recordings'
import type { Recording } from '@/types/api'

const props = withDefaults(
  defineProps<{
    open: boolean
    mode?: 'web' | 'physical'
  }>(),
  { mode: 'physical' },
)

const emit = defineEmits<{
  close: []
  created: [recording: Recording]
}>()

const serial = ref('')
const name = ref('')
const devicesQuery = useDevices({ refetchInterval: 10000, enabled: computed(() => props.open) })
const createMutation = useCreateRecording()

const title = computed(() => (props.mode === 'physical' ? '新建真机录制' : '新建镜像录制'))
const deviceOptions = computed(() =>
  (devicesQuery.data.value ?? []).map((device) => ({
    value: device.serial,
    label: `${device.serial}${device.model ? ` · ${device.model}` : ''}${device.busy ? '（占用中）' : ''}`,
    disabled: device.busy,
  })),
)

watch(
  () => props.open,
  (open) => {
    if (!open) return
    serial.value = ''
    name.value = ''
  },
)

async function handleCreate(): Promise<void> {
  if (!serial.value) {
    ElMessage.warning('请选择要录制的设备')
    return
  }
  try {
    const recording = await createMutation.mutateAsync({
      serial: serial.value,
      mode: props.mode,
      name: name.value.trim() || undefined,
    })
    ElMessage.success('录制已创建')
    emit('created', recording)
    emit('close')
  } catch {
    // 全局错误提示
  }
}
</script>

<template>
  <el-dialog
    :model-value="open"
    :title="title"
    width="520px"
    append-to-body
    @update:model-value="!$event && emit('close')"
    @closed="emit('close')"
  >
    <el-form label-position="top" size="small">
      <el-form-item label="设备" required>
        <el-select v-model="serial" placeholder="选择设备" style="width: 100%" :loading="devicesQuery.isLoading.value">
          <el-option
            v-for="option in deviceOptions"
            :key="option.value"
            :value="option.value"
            :label="option.label"
            :disabled="option.disabled"
          />
        </el-select>
        <div class="hint">
          {{ devicesQuery.isError.value ? '设备列表不可用' : '占用中的设备不可选；真机录制通过 getevent 采集触摸。' }}
        </div>
      </el-form-item>
      <el-form-item label="名称（可选）">
        <el-input v-model="name" placeholder="例如：登录流程录制" />
      </el-form-item>
      <el-alert
        v-if="devicesQuery.isError.value"
        type="warning"
        :closable="false"
        show-icon
        title="设备列表加载失败"
        description="请确认后端与 adb 正常后重试。"
      />
    </el-form>
    <template #footer>
      <el-button @click="emit('close')">取消</el-button>
      <el-button type="primary" :loading="createMutation.isPending.value" @click="handleCreate">开始录制</el-button>
    </template>
  </el-dialog>
</template>
