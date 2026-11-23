<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { useDevices } from '@/api/devices'
import { useStartRun } from '@/api/runs'
import { safeParseJson } from '@/lib/format'

const props = defineProps<{
  open: boolean
  scriptId: string
  scriptName?: string
}>()

const emit = defineEmits<{ close: [] }>()
const router = useRouter()
const serial = ref('')
const variablesText = ref('')
const submitting = ref(false)

const devicesQuery = useDevices({ refetchInterval: 8000, enabled: computed(() => props.open) })
const startMutation = useStartRun()

const deviceOptions = computed(() =>
  (devicesQuery.data.value ?? []).map((device) => ({
    value: device.serial,
    label: `${device.serial}${device.model ? ` · ${device.model}` : ''}${device.busy ? '（占用中）' : ''}`,
  })),
)

watch(
  () => props.open,
  (open) => {
    if (!open) return
    serial.value = ''
    variablesText.value = ''
  },
)

function parseVariables(text: string): Record<string, string> | null {
  const trimmed = text.trim()
  if (!trimmed) return {}
  const parsed = safeParseJson<unknown>(trimmed)
  if (!parsed.ok) return null
  const value = parsed.value
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const result: Record<string, string> = {}
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    result[key] = typeof item === 'string' ? item : String(item)
  }
  return result
}

async function handleOk(): Promise<void> {
  const variables = parseVariables(variablesText.value)
  if (variables === null) {
    ElMessage.error('variables 必须是 JSON 对象，例如 {"user":"alice"}')
    return
  }
  submitting.value = true
  try {
    const run = await startMutation.mutateAsync({
      script_id: props.scriptId,
      serial: serial.value || undefined,
      variables,
    })
    ElMessage.success(`已创建运行 #${run.id}`)
    emit('close')
    await router.push(`/runs/${run.id}`)
  } catch {
    // 全局错误提示
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="open"
    :title="`运行脚本${scriptName ? ` · ${scriptName}` : ''}`"
    width="560px"
    append-to-body
    @update:model-value="!$event && emit('close')"
    @closed="emit('close')"
  >
    <el-form label-position="top" size="small">
      <el-form-item label="目标设备">
        <el-select v-model="serial" clearable placeholder="自动（唯一在线设备）" style="width: 100%">
          <el-option value="" label="自动（唯一在线设备）" />
          <el-option
            v-for="option in deviceOptions"
            :key="option.value"
            :value="option.value"
            :label="option.label"
          />
        </el-select>
        <div class="hint">留空则由后端选择唯一在线设备。</div>
      </el-form-item>
      <el-form-item label="变量覆盖（可选）">
        <el-input
          v-model="variablesText"
          type="textarea"
          :rows="4"
          placeholder='{"user": "alice"}'
          class="code-text"
        />
        <div class="hint">JSON 对象，会覆盖脚本 variables。</div>
      </el-form-item>
      <el-alert
        v-if="devicesQuery.isError.value"
        type="warning"
        :closable="false"
        show-icon
        title="设备列表加载失败"
        style="margin-bottom: 8px"
      />
      <div class="hint">运行创建后可在「运行详情」实时查看步骤与事件流。</div>
    </el-form>
    <template #footer>
      <el-button @click="emit('close')">取消</el-button>
      <el-button type="primary" :loading="submitting || startMutation.isPending.value" @click="handleOk">
        创建运行
      </el-button>
    </template>
  </el-dialog>
</template>
