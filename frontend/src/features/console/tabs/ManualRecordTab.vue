<script setup lang="ts">
// 手动控制 → 镜像：网页操作录制（mode=web），停止后转成脚本节点。
import { EditPen } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'

import RecordingBar from '@/features/mirror/components/RecordingBar.vue'

defineProps<{ serial: string }>()
const emit = defineEmits<{ refreshDevices: [] }>()

const router = useRouter()

function goPhysicalRecording(): void {
  void router.push('/recorder?new=physical')
}
</script>

<template>
  <section class="manual-page">
    <div class="section-head">
      <h3>
        <el-icon><EditPen /></el-icon>镜像录制
      </h3>
      <button type="button" class="cell-link" @click="goPhysicalRecording">新建真机录制 →</button>
    </div>
    <div class="section-sub">镜像上的点击/滑动会实时出现在下方；停止后转成正式节点。</div>
    <RecordingBar :serial="serial" @device-change="emit('refreshDevices')" />
  </section>
</template>
