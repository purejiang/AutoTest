<script setup lang="ts">
// 从当前屏幕截图里拾取「坐标」或「元素」，回填到脚本节点的 Target。
// 弹窗自带设备选择：脚本页没有设备上下文，默认用控制台选中的设备，否则取第一台在线设备。
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { describeApiError } from '@/api/client'
import { useDeviceSnapshot, useDevices } from '@/api/devices'
import { useUiStore } from '@/stores/ui'
import type { Device, Target } from '@/types/api'

import ScreenInspector from './ScreenInspector.vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    /** 执行条件等只允许元素定位的场景传 false。 */
    allowPoint?: boolean
    title?: string
  }>(),
  { allowPoint: true, title: '从截图选择坐标 / 元素' },
)

const emit = defineEmits<{ close: []; pick: [target: Target] }>()

const router = useRouter()
const ui = useUiStore()

const devicesQuery = useDevices({
  enabled: computed(() => props.open),
  refetchInterval: false,
})
const devices = computed<Device[]>(() => {
  const list = [...(devicesQuery.data.value ?? [])]
  return list.sort((a, b) => {
    const rank = (device: Device): number => (device.state === 'device' ? 0 : 1)
    return rank(a) - rank(b) || a.serial.localeCompare(b.serial)
  })
})

const serial = ref<string | null>(null)

function deviceLabel(device: Device): string {
  const name = [device.brand, device.model].filter(Boolean).join(' ') || device.serial
  return device.state === 'device' ? `${name} · ${device.serial}` : `${name} · ${device.serial}（离线）`
}

/** 打开时选一台可用的设备：优先控制台当前设备。 */
function resolveSerial(): void {
  const list = devices.value
  const preferred = ui.selectedSerial
  if (preferred && list.some((device) => device.serial === preferred)) {
    serial.value = preferred
    return
  }
  const online = list.find((device) => device.state === 'device')
  serial.value = online?.serial ?? list[0]?.serial ?? preferred ?? null
}

watch(
  () => props.open,
  (open) => {
    if (open) resolveSerial()
  },
  { immediate: true },
)

watch(devices, () => {
  if (!props.open) return
  const current = serial.value
  if (current && devices.value.some((device) => device.serial === current)) return
  resolveSerial()
})

const snapshot = useDeviceSnapshot(serial, {
  enabled: computed(() => props.open && Boolean(serial.value)),
})
const { imageUrl, hierarchy, isFetching, error } = snapshot

const treeRoot = computed(() => hierarchy.value?.node ?? null)
const errorText = computed(() => (error.value ? describeApiError(error.value) : null))

async function refresh(): Promise<void> {
  await snapshot.refetch()
}

function onPick(target: Target): void {
  emit('pick', target)
  emit('close')
}

function goConsole(): void {
  emit('close')
  void router.push('/console')
}
</script>

<template>
  <el-dialog
    :model-value="open"
    :title="title"
    width="1180px"
    append-to-body
    destroy-on-close
    class="target-picker-dialog"
    @update:model-value="!$event && emit('close')"
  >
    <div class="picker-head">
      <label class="picker-device">
        <span class="picker-label">设备</span>
        <el-select
          :model-value="serial ?? undefined"
          size="small"
          filterable
          placeholder="选择设备"
          style="width: 320px"
          @update:model-value="serial = $event"
        >
          <el-option
            v-for="device in devices"
            :key="device.serial"
            :value="device.serial"
            :label="deviceLabel(device)"
          />
        </el-select>
      </label>
      <span class="spacer" />
      <span class="hint">拾取的是当前屏幕的静态快照；页面已变化时点「刷新」重新截屏。</span>
    </div>

    <div v-if="!serial" class="picker-empty">
      <el-skeleton v-if="devicesQuery.isPending.value" :rows="4" animated />
      <el-empty v-else description="没有可用设备" :image-size="64">
        <p class="hint">先在控制台连接并选中一台设备，再回来从截图拾取坐标 / 元素。</p>
        <el-button type="primary" size="small" @click="goConsole">去控制台</el-button>
      </el-empty>
    </div>

    <ScreenInspector
      v-else
      mode="pick"
      :allow-point="allowPoint"
      :root="treeRoot"
      :image-url="imageUrl"
      :loading="isFetching"
      :error="errorText"
      @refresh="refresh"
      @pick="onPick"
    />
  </el-dialog>
</template>

<style scoped>
.picker-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.picker-device {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.picker-label {
  color: var(--muted);
  font-size: 11px;
  white-space: nowrap;
}

.picker-empty {
  padding: 12px 0;
}

.picker-empty p {
  margin: 0 0 8px;
}

:global(.target-picker-dialog) {
  max-width: 96vw;
}

:global(.target-picker-dialog .el-dialog__body) {
  padding-top: 8px;
  max-height: 76vh;
  overflow: auto;
}

@media (max-width: 720px) {
  .picker-head .hint {
    display: none;
  }

  .picker-device :deep(.el-select) {
    width: 100% !important;
  }
}
</style>
