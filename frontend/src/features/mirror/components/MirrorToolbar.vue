<script setup lang="ts">
import { computed } from 'vue'

import type { MirrorSession } from '../composables/useMirrorSession'

const props = defineProps<{ session: MirrorSession }>()

const busy = computed(() => ['checking', 'starting'].includes(props.session.status.value))
const running = computed(() => !['idle', 'starting'].includes(props.session.status.value))
const isIdle = computed(() => props.session.status.value === 'idle')
</script>

<template>
  <div class="mirror-toolbar">
    <el-button v-if="isIdle" type="primary" :disabled="!session.isSupported" @click="session.startMirror()">
      启动镜像
    </el-button>
    <el-button v-else type="danger" @click="session.stopMirror()">停止镜像</el-button>
    <el-button :disabled="!session.isSupported || busy" @click="session.reconnect()">重新连接</el-button>
    <el-button :disabled="!running" @click="session.requestKeyframe()">请求关键帧</el-button>
  </div>
</template>
