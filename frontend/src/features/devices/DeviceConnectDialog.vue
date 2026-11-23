<script setup lang="ts">
// 从旧 DevicesPage 抽出的「连接网络设备」弹框；控制台设备下拉复用。
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { useConnectDevice } from '@/api/devices'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: []; connected: [] }>()

const address = ref('')
const connectError = ref('')
const connectMutation = useConnectDevice()

watch(
  () => props.open,
  (open) => {
    if (!open) return
    address.value = ''
    connectError.value = ''
  },
)

function close(): void {
  emit('close')
}

async function connectDevice(): Promise<void> {
  const value = address.value.trim()
  if (!value) {
    connectError.value = '请输入 adb 连接地址'
    return
  }
  if (!/^[\w.-]+:\d{1,5}$/.test(value)) {
    connectError.value = '地址格式应为 host:port，例如 192.168.1.20:5555'
    return
  }
  connectError.value = ''
  try {
    await connectMutation.mutateAsync({ address: value })
    ElMessage.success('已发起连接，请查看设备列表状态')
    emit('connected')
    emit('close')
  } catch {
    // 全局错误提示
  }
}
</script>

<template>
  <el-dialog
    :model-value="open"
    title="连接网络设备"
    width="480px"
    append-to-body
    @update:model-value="!$event && close()"
  >
    <el-form label-position="top" size="small" @submit.prevent="connectDevice">
      <el-form-item label="设备地址" :error="connectError">
        <el-input
          v-model="address"
          placeholder="192.168.1.20:5555"
          class="code-text"
          autofocus
          @keyup.enter="connectDevice"
        />
        <div class="hint">格式 host:port；USB 设备直接连接并在手机上授权调试即可。</div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="connectMutation.isPending.value" @click="connectDevice">连接</el-button>
    </template>
  </el-dialog>
</template>
