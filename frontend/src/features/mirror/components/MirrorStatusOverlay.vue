<script setup lang="ts">
import { Loading } from '@element-plus/icons-vue'
import { computed } from 'vue'

import type { MirrorHelloInfo, MirrorUiStatus } from '../types'

const props = defineProps<{
  status: MirrorUiStatus
  error: string | null
  hello: MirrorHelloInfo | null
  hasFrame: boolean
}>()

const emit = defineEmits<{ start: []; retry: [] }>()

const SPINNING: MirrorUiStatus[] = ['checking', 'starting', 'connecting', 'waiting-keyframe', 'reconnecting']
const spinning = computed(() => SPINNING.includes(props.status))
const showHello = computed(() => Boolean(props.hello && props.hello.width > 0 && props.hello.height > 0))
</script>

<template>
  <div v-if="!hasFrame && status !== 'streaming'" class="mirror-placeholder" :class="{ 'is-busy': spinning }">
    <el-icon v-if="spinning" :size="26" class="is-loading" color="#8b9bb4"><Loading /></el-icon>
    <span class="mirror-placeholder-title">
      {{
        status === 'idle'
          ? '镜像未启动'
          : status === 'error'
            ? '镜像启动失败'
            : status === 'reconnecting'
              ? '连接断开，正在重连…'
              : '正在准备画面…'
      }}
    </span>
    <span v-if="error" class="mirror-placeholder-error">{{ error }}</span>
    <span v-if="!error && showHello && status !== 'idle'" class="mirror-placeholder-sub">
      {{ hello?.width }}×{{ hello?.height }}<template v-if="hello?.codec"> · {{ hello.codec }}</template>
    </span>
    <el-button v-if="status === 'idle'" type="primary" @click="emit('start')">启动镜像</el-button>
    <el-button v-if="status === 'error'" @click="emit('retry')">重试</el-button>
    <span v-if="status === 'idle'" class="mirror-placeholder-hint">
      需要浏览器支持 WebCodecs（Chrome / Edge 新版）。不支持时可用「真机录制」。
    </span>
  </div>
</template>
