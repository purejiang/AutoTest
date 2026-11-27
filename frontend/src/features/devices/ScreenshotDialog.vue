<script setup lang="ts">
import { Download, Refresh } from '@element-plus/icons-vue'
import { onBeforeUnmount, computed, ref, watch } from 'vue'

import { describeApiError } from '@/api/client'
import { useDeviceScreenshot } from '@/api/devices'
import Notice from '@/components/Notice.vue'

const props = defineProps<{
  serial: string | null
  open: boolean
}>()

const emit = defineEmits<{ close: [] }>()
const nonce = ref(0)
const objectUrl = ref<string | null>(null)

const query = useDeviceScreenshot(computed(() => props.serial), {
  enabled: computed(() => props.open),
  nonce,
})

watch(
  () => query.data.value,
  (blob) => {
    if (objectUrl.value) {
      URL.revokeObjectURL(objectUrl.value)
      objectUrl.value = null
    }
    if (blob) objectUrl.value = URL.createObjectURL(blob)
  },
)

onBeforeUnmount(() => {
  if (objectUrl.value) URL.revokeObjectURL(objectUrl.value)
})

function download(): void {
  if (!objectUrl.value) return
  const anchor = document.createElement('a')
  anchor.href = objectUrl.value
  anchor.download = `screenshot-${(props.serial ?? 'device').replace(/[^\w.-]+/g, '_')}.png`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}
</script>

<template>
  <el-dialog
    :model-value="open"
    :title="`截图 · ${serial ?? ''}`"
    width="780px"
    append-to-body
    @update:model-value="!$event && emit('close')"
  >
    <Notice v-if="query.isError.value" severity="danger" title="截图失败">
      {{ describeApiError(query.error.value) }}
    </Notice>
    <el-skeleton v-else-if="!objectUrl" :rows="6" animated />
    <div v-else style="text-align: center; background: var(--surface-2); border-radius: 8px; padding: 8px">
      <img :src="objectUrl" :alt="`${serial} 截图`" style="max-width: 100%; max-height: 70vh" />
    </div>
    <template #footer>
      <el-button :icon="Refresh" :loading="query.isFetching.value" @click="nonce += 1">刷新</el-button>
      <el-button type="primary" :icon="Download" :disabled="!objectUrl" @click="download">下载</el-button>
    </template>
  </el-dialog>
</template>
