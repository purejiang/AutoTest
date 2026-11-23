<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()

function openChrome(): void {
  window.open('https://www.google.com/chrome/', '_blank', 'noopener')
}
</script>

<template>
  <el-result
    icon="warning"
    title="当前浏览器不支持 WebCodecs 镜像"
    sub-title="AutoTest 的实时镜像依赖 WebCodecs（VideoDecoder）。本浏览器未提供该能力，因此不提供截图轮询降级。"
  >
    <template #extra>
      <div class="stack" style="align-items: center">
        <span class="hint">
          请使用新版 Chrome / Edge，或改用「真机录制」模式（getevent 采集触摸，不依赖 WebCodecs）。
        </span>
        <div class="row">
          <el-button type="primary" @click="router.push('/recorder')">前往真机录制</el-button>
          <el-button @click="openChrome">
            下载 Chrome
          </el-button>
        </div>
      </div>
    </template>
  </el-result>
</template>
