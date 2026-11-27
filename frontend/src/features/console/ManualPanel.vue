<script setup lang="ts">
// 手动控制面板容器：顶部 tab（状态在 Pinia）+ 5 个子页。
// 子页共享 features/console/manual-panel.css，容器只管 tab 与页面切换。
import { EditPen, Grid, Link, VideoCamera, VideoPlay } from '@element-plus/icons-vue'
import { computed, type Component } from 'vue'

import type { Device } from '@/types/api'
import { useUiStore, type ManualTab } from '@/stores/ui'
import AppTabs from '@/components/AppTabs.vue'
import type { AppTabItem } from '@/components/appTabs'

import ManualAppTab from './tabs/ManualAppTab.vue'
import ManualRecordTab from './tabs/ManualRecordTab.vue'
import ManualTextTab from './tabs/ManualTextTab.vue'
import ManualToolsTab from './tabs/ManualToolsTab.vue'
import ManualVideoTab from './tabs/ManualVideoTab.vue'
import './manual-panel.css'

const props = defineProps<{
  serial: string
  device: Device | null
  streaming: boolean
  getSource: () => HTMLCanvasElement | null
  getOverlay: () => HTMLCanvasElement | null
}>()

const emit = defineEmits<{
  hierarchy: []
  logcat: []
  network: []
  refreshDevices: []
}>()

const ui = useUiStore()

const MANUAL_TABS: ReadonlyArray<{ value: ManualTab; label: string; icon: Component }> = [
  { value: 'video', label: '视频', icon: VideoCamera },
  { value: 'record', label: '镜像', icon: VideoPlay },
  { value: 'text', label: '文本', icon: EditPen },
  { value: 'app', label: '应用', icon: Link },
  { value: 'tools', label: '工具', icon: Grid },
]

const activeTab = computed({
  get: () => ui.manualTab,
  set: (value) => ui.setManualTab(value),
})

const manualTabs = computed<AppTabItem[]>(() =>
  MANUAL_TABS.map((tab) => ({ key: tab.value, label: tab.label, icon: tab.icon })),
)

function setManualTab(key: string): void {
  const tab = MANUAL_TABS.find((item) => item.value === key)
  if (tab) activeTab.value = tab.value
}

const busy = computed(() => Boolean(props.device?.busy))
</script>

<template>
  <div class="manual-panel">
    <AppTabs
      :model-value="activeTab"
      :items="manualTabs"
      variant="scroll"
      aria-label="手动控制功能"
      @update:model-value="setManualTab"
    />

    <div class="manual-pages">
      <ManualVideoTab
        v-show="activeTab === 'video'"
        :serial="serial"
        :streaming="streaming"
        :get-source="getSource"
        :get-overlay="getOverlay"
      />
      <ManualRecordTab
        v-show="activeTab === 'record'"
        :serial="serial"
        @refresh-devices="emit('refreshDevices')"
      />
      <ManualTextTab v-show="activeTab === 'text'" :serial="serial" :device="device" :busy="busy" />
      <ManualAppTab
        v-show="activeTab === 'app'"
        :serial="serial"
        :busy="busy"
        :active="activeTab === 'app'"
      />
      <ManualToolsTab
        v-show="activeTab === 'tools'"
        :serial="serial"
        :device="device"
        :busy="busy"
        @hierarchy="emit('hierarchy')"
        @logcat="emit('logcat')"
        @network="emit('network')"
      />
    </div>
  </div>
</template>

<style scoped>
.manual-panel {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

.manual-pages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
</style>
