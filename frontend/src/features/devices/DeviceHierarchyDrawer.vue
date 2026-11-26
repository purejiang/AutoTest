<script setup lang="ts">
// 「层级树」抽屉：截屏 + 层级叠加的元素检查器（手动控制 → 工具 → 层级树）。
// 内容由 ScreenInspector 提供：悬停看元素信息、树与图联动、点击复制选择器；
// 另保留原来的「原始 XML」开关（调试 uiautomator dump 用）。
import { computed, ref } from 'vue'

import { describeApiError } from '@/api/client'
import { useDeviceSnapshot } from '@/api/devices'

import ScreenInspector from './ScreenInspector.vue'

const props = defineProps<{
  serial: string | null
  open: boolean
}>()

const emit = defineEmits<{ close: [] }>()

const raw = ref(false)
const snapshot = useDeviceSnapshot(
  computed(() => props.serial),
  {
    enabled: computed(() => props.open),
    screenshotEnabled: computed(() => props.open && !raw.value),
    raw,
  },
)
const { imageUrl, hierarchy, isFetching, error } = snapshot

const treeRoot = computed(() => hierarchy.value?.node ?? null)
const rawXml = computed(() => hierarchy.value?.raw ?? null)
const rawUnavailable = computed(() => hierarchy.value?.rawUnavailable ?? null)
const errorText = computed(() => (error.value ? describeApiError(error.value) : null))

const lastRefreshMs = ref<number | null>(null)

async function refresh(): Promise<void> {
  const started = performance.now()
  await snapshot.refetch()
  lastRefreshMs.value = Math.round(performance.now() - started)
}

function onRawChange(value: string | number | boolean): void {
  raw.value = Boolean(value)
}
</script>

<template>
  <el-drawer
    :model-value="open"
    size="min(1400px, 96vw)"
    append-to-body
    @update:model-value="!$event && emit('close')"
  >
    <template #header>
      <div class="row">
        <strong>层级树</strong>
        <span class="code-text muted">{{ serial ?? '' }}</span>
      </div>
    </template>

    <div class="row wrap drawer-toolbar">
      <span class="hint"
        >截屏上叠加当前层级：悬停看元素信息，点击图中元素或树节点可查看详情并复制选择器。</span
      >
      <span class="spacer" />
      <span class="hint">原始 XML</span>
      <el-switch :model-value="raw" size="small" @update:model-value="onRawChange" />
      <el-button size="small" :loading="isFetching" @click="refresh">刷新</el-button>
      <span v-if="lastRefreshMs !== null" class="hint">
        上次刷新 {{ lastRefreshMs }}ms<template v-if="hierarchy?.elapsedMs != null">
          · 层级 {{ hierarchy.elapsedMs }}ms</template
        >
      </span>
    </div>

    <template v-if="raw">
      <pre v-if="rawXml" class="pre-block raw-xml">{{ rawXml }}</pre>
      <el-skeleton v-else-if="isFetching" :rows="10" animated />
      <el-alert
        v-else
        type="info"
        :closable="false"
        show-icon
        title="当前驱动不提供原始 XML"
        :description="rawUnavailable ?? '后端返回 raw_unavailable'"
      />
    </template>

    <ScreenInspector
      v-else
      mode="browse"
      :root="treeRoot"
      :image-url="imageUrl"
      :loading="isFetching"
      :error="errorText"
      :hierarchy-ms="hierarchy?.elapsedMs ?? null"
      @refresh="refresh"
    />
  </el-drawer>
</template>

<style scoped>
.drawer-toolbar {
  gap: 6px;
  margin-bottom: 10px;
}

.raw-xml {
  max-height: 72vh;
}
</style>
