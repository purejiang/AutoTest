<script setup lang="ts">
// 手动控制 → 视频：浏览器端录制镜像画面（不落库，刷新即丢失）。
import { VideoCamera } from '@element-plus/icons-vue'

import Notice from '@/components/Notice.vue'
import StatusTag from '@/components/StatusTag.vue'
import MirrorVideoRecorder from '@/features/mirror/components/MirrorVideoRecorder.vue'

defineProps<{
  serial: string
  streaming: boolean
  getSource: () => HTMLCanvasElement | null
  getOverlay: () => HTMLCanvasElement | null
}>()
</script>

<template>
  <section class="manual-page">
    <div class="section-head">
      <h3>
        <el-icon><VideoCamera /></el-icon>视频录制
      </h3>
      <StatusTag tone="blue">浏览器画面</StatusTag>
      <StatusTag tone="amber">刷新即丢失</StatusTag>
    </div>
    <Notice severity="warning" variant="compact" title="视频暂不持久化">
      只保存在当前浏览器内存，刷新或关闭页面即丢失；需要保留请停止后立即下载。
    </Notice>
    <MirrorVideoRecorder
      :serial="serial"
      :get-source="getSource"
      :overlay="getOverlay"
      :enabled="streaming"
    />
    <div class="section-sub">
      录制当前镜像画面，支持暂停/继续；停止后可预览、拖动进度和下载，下载后才会保留。
    </div>
  </section>
</template>
