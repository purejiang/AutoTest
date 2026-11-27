<script setup lang="ts">
// 手动控制 → 文本：向当前焦点输入框发送文本。
import { EditPen } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { ref } from 'vue'

import { useDeviceText } from '@/api/devices'
import type { Device } from '@/types/api'

const props = defineProps<{ serial: string; device: Device | null; busy: boolean }>()

const text = ref('')
const clearFirst = ref(false)
const textMutation = useDeviceText(props.serial)

function sendText(): void {
  if (!text.value.trim()) {
    ElMessage.warning('请输入要发送的文本')
    return
  }
  textMutation.mutate(
    { text: text.value, clear_first: clearFirst.value },
    {
      onSuccess: () => {
        ElMessage.success('文本已发送')
        text.value = ''
      },
    },
  )
}
</script>

<template>
  <section class="manual-page">
    <div class="section-head">
      <h3>
        <el-icon><EditPen /></el-icon>输入文本
      </h3>
    </div>
    <div class="rec-row">
      <el-input
        v-model="text"
        size="small"
        placeholder="发送到当前焦点输入框"
        :disabled="busy"
        @keyup.enter="sendText"
      />
      <el-button
        size="small"
        :loading="textMutation.isPending.value"
        :disabled="busy || !text.trim()"
        @click="sendText"
      >
        发送
      </el-button>
    </div>
    <div class="rec-row" style="margin-top: 7px">
      <el-checkbox v-model="clearFirst" size="small">发送前清空输入框</el-checkbox>
    </div>
    <div class="section-sub">中文输入需要设备端输入法配合；不确定时先用英文/数字验证链路。</div>
    <div v-if="busy" class="inline-warning">设备被占用：{{ device?.busy_reason ?? '运行/录制中' }}</div>
  </section>
</template>
