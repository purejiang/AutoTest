<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { describeApiError } from '@/api/client'
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
const countdown = ref(0)
const error = ref<string | null>(null)
const countdownTimer = ref<number | null>(null)
const devicesQuery = useDevices({
  refetchInterval: 10000,
  enabled: computed(() => props.open),
  // 弹窗内自己展示设备列表错误，避免再弹全局 toast。
  suppressError: true,
})
const createMutation = useCreateRecording()

const isPhysical = computed(() => props.mode === 'physical')
const pending = computed(() => createMutation.isPending.value)
const busy = computed(() => pending.value || countdown.value > 0)
const title = computed(() => (isPhysical.value ? '新建真机录制' : '新建镜像录制'))
const countdownText = computed(() =>
  countdown.value > 0 ? `${countdown.value} 秒后开始…` : '开始录制',
)
const deviceOptions = computed(() =>
  (devicesQuery.data.value ?? []).map((device) => ({
    value: device.serial,
    label: `${device.serial}${device.model ? ` · ${device.model}` : ''}${device.busy ? '（占用中）' : ''}`,
    disabled: device.busy,
  })),
)

function clearCountdown(): void {
  if (countdownTimer.value !== null) {
    window.clearInterval(countdownTimer.value)
    countdownTimer.value = null
  }
  countdown.value = 0
}

function reset(): void {
  clearCountdown()
  serial.value = ''
  name.value = ''
  error.value = null
}

watch(
  () => props.open,
  (open) => {
    if (open) reset()
    else clearCountdown()
  },
)

async function handleCreate(): Promise<void> {
  if (!serial.value || pending.value) return
  clearCountdown()
  error.value = null
  try {
    const recording = await createMutation.mutateAsync({
      serial: serial.value,
      mode: props.mode,
      name: name.value.trim() || undefined,
    })
    ElMessage.success('录制已开始')
    emit('created', recording)
    emit('close')
  } catch (err) {
    error.value = describeApiError(err)
  }
}

function startCountdown(): void {
  if (!serial.value || busy.value) return
  error.value = null
  clearCountdown()
  countdown.value = 3
  countdownTimer.value = window.setInterval(() => {
    countdown.value -= 1
    if (countdown.value <= 0) {
      clearCountdown()
      void handleCreate()
    }
  }, 1000)
}

function handleStart(): void {
  if (!isPhysical.value) {
    void handleCreate()
    return
  }
  startCountdown()
}

function cancel(): void {
  if (pending.value) return
  clearCountdown()
  emit('close')
}

function handleBeforeClose(done: () => void): void {
  if (busy.value) return
  clearCountdown()
  done()
}

function onVisibleChange(value: boolean): void {
  if (value || pending.value) return
  clearCountdown()
  emit('close')
}

function handleClosed(): void {
  clearCountdown()
  emit('close')
}

onBeforeUnmount(clearCountdown)
</script>

<template>
  <el-dialog
    :model-value="open"
    :title="title"
    width="520px"
    append-to-body
    :show-close="!busy"
    :close-on-click-modal="!busy"
    :close-on-press-escape="!busy"
    :before-close="handleBeforeClose"
    @update:model-value="onVisibleChange"
    @closed="handleClosed"
  >
    <el-form label-position="top" size="small" @submit.prevent>
      <el-form-item label="设备" required>
        <el-select
          v-model="serial"
          placeholder="选择设备"
          style="width: 100%"
          :loading="devicesQuery.isLoading.value"
          :disabled="busy"
        >
          <el-option
            v-for="option in deviceOptions"
            :key="option.value"
            :value="option.value"
            :label="option.label"
            :disabled="option.disabled"
          />
        </el-select>
        <div class="hint">占用中的设备不可选。</div>
      </el-form-item>

      <el-form-item label="名称（可选）">
        <el-input v-model="name" placeholder="例如：登录流程录制" :disabled="busy" />
      </el-form-item>

      <el-alert
        v-if="devicesQuery.isError.value"
        type="warning"
        :closable="false"
        show-icon
        title="设备列表加载失败"
        description="请确认后端与 adb 正常后重试。"
      />

      <el-alert
        v-if="isPhysical"
        type="warning"
        :closable="false"
        show-icon
        title="开始前请确认"
        class="start-checklist"
      >
        <ul class="ready-checklist">
          <li>手机已解锁，并停留在要录制的界面。</li>
          <li>录制期间不要同时运行脚本，避免触摸被占用或事件混淆。</li>
          <li>如果坐标不准，先在设备测试清单里完成 2 点标定。</li>
        </ul>
      </el-alert>

      <p v-if="isPhysical" class="ready-countdown">
        点击开始后倒计时 3 秒，随后立即采集手机触摸；倒计时结束前请拿好手机。
      </p>
      <p v-if="countdown > 0" class="ready-count-number">{{ countdown }}</p>

      <el-alert
        v-if="error"
        type="error"
        :closable="false"
        show-icon
        title="创建录制失败"
        :description="error"
        style="margin-top: 8px"
      />
    </el-form>

    <template #footer>
      <el-button :disabled="pending" @click="cancel">取消</el-button>
      <el-button
        type="primary"
        :loading="pending"
        :disabled="!serial || busy"
        @click="handleStart"
      >
        {{ countdownText }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.start-checklist {
  margin-top: 2px;
}

.ready-checklist {
  margin: 0;
  padding-left: 18px;
  line-height: 1.8;
}

.ready-countdown {
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

.ready-count-number {
  margin: 10px 0 0;
  color: var(--accent);
  font-family: var(--mono);
  font-size: 28px;
  font-weight: 650;
  line-height: 1;
  text-align: center;
}
</style>
