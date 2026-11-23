<script setup lang="ts">
import { computed } from 'vue'

import type { MirrorSession } from '../composables/useMirrorSession'

const props = defineProps<{
  session: MirrorSession
  tapEffect: boolean
  swipeEffect: boolean
  ripple: boolean
}>()
const emit = defineEmits<{
  'update:tapEffect': [value: boolean]
  'update:swipeEffect': [value: boolean]
  'update:ripple': [value: boolean]
}>()

const busy = computed(() => ['checking', 'starting'].includes(props.session.status.value))
const running = computed(() => !['idle', 'starting'].includes(props.session.status.value))
const isIdle = computed(() => props.session.status.value === 'idle')

function toggleRipple(): void {
  if (!props.tapEffect) return
  emit('update:ripple', !props.ripple)
}
</script>

<template>
  <div class="mirror-toolbar">
    <el-button v-if="isIdle" type="primary" :disabled="!session.isSupported" @click="session.startMirror()">
      启动镜像
    </el-button>
    <el-button v-else type="danger" @click="session.stopMirror()">停止镜像</el-button>
    <el-button :disabled="!session.isSupported || busy" @click="session.reconnect()">重新连接</el-button>
    <el-button :disabled="!running" @click="session.requestKeyframe()">请求关键帧</el-button>
    <el-tooltip content="点击特效：按下触点、长按呼吸环（波纹可在右侧单独关掉）" placement="top">
      <span class="mirror-effect-toggle" @click="emit('update:tapEffect', !tapEffect)">
        <el-switch
          :model-value="tapEffect"
          size="small"
          @click.stop
          @update:model-value="emit('update:tapEffect', Boolean($event))"
        />
        <span>点击特效</span>
      </span>
    </el-tooltip>
    <el-tooltip
      content="只控制点击的扩散波纹；关闭会立即清掉正在扩散的波纹，触点和长按环保留"
      placement="top"
    >
      <span class="mirror-effect-toggle" :class="{ 'is-disabled': !tapEffect }" @click="toggleRipple">
        <el-switch
          :model-value="ripple"
          size="small"
          :disabled="!tapEffect"
          @click.stop
          @update:model-value="emit('update:ripple', Boolean($event))"
        />
        <span>波纹</span>
      </span>
    </el-tooltip>
    <el-tooltip content="滑动特效：拖动时显示整条滑动轨迹和跟随手指的终点圆点" placement="top">
      <span class="mirror-effect-toggle" @click="emit('update:swipeEffect', !swipeEffect)">
        <el-switch
          :model-value="swipeEffect"
          size="small"
          @click.stop
          @update:model-value="emit('update:swipeEffect', Boolean($event))"
        />
        <span>滑动特效</span>
      </span>
    </el-tooltip>
  </div>
</template>
