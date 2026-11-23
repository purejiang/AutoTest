<script setup lang="ts">
import { computed } from 'vue'

import type { MirrorSession } from '../composables/useMirrorSession'
import type { MirrorUiStatus } from '../types'

const props = defineProps<{
  serial: string
  session: MirrorSession
  pendingText?: string
  busy?: boolean
}>()

const STATUS_TEXT: Record<MirrorUiStatus, string> = {
  idle: '未启动',
  checking: '检查中',
  starting: '启动服务',
  connecting: '连接中',
  'waiting-keyframe': '等待关键帧',
  streaming: '镜像中',
  reconnecting: '重连中',
  error: '出错',
}

const STATUS_TONE: Record<MirrorUiStatus, string> = {
  idle: 'idle',
  checking: 'busy',
  starting: 'busy',
  connecting: 'busy',
  'waiting-keyframe': 'warn',
  streaming: 'live',
  reconnecting: 'warn',
  error: 'error',
}

const status = computed(() => props.session.status.value)
const hello = computed(() => props.session.hello.value)
const stats = computed(() => props.session.stats.value)
const decoderMode = computed(() => props.session.decoderMode.value)
const resolved = computed(() => Boolean(hello.value && hello.value.width > 0 && hello.value.height > 0))
const input = computed(() => props.pendingText?.trim())
</script>

<template>
  <div class="mirror-statusline" role="status" aria-live="polite">
    <span class="mirror-dot" :class="`mirror-dot-${STATUS_TONE[status]}`" aria-hidden />
    <span class="mirror-statusline-strong">{{ STATUS_TEXT[status] }}</span>
    <span class="mirror-statusline-sep" aria-hidden />
    <span class="mirror-statusline-item mirror-statusline-serial" :title="serial">{{ serial }}</span>
    <span class="mirror-statusline-item">
      {{ resolved ? `${hello?.width}×${hello?.height}` : '分辨率 —' }}
      <template v-if="resolved && hello?.rotation"> / {{ hello.rotation }}°</template>
    </span>
    <span class="mirror-statusline-item">{{ stats.fps }} fps</span>
    <el-tooltip v-if="(stats.dropped ?? 0) > 0" content="服务端丢帧 + 解码队列主动丢帧">
      <span class="mirror-statusline-item mirror-statusline-warn">丢帧 {{ stats.dropped }}</span>
    </el-tooltip>
    <el-tooltip
      v-if="decoderMode"
      :content="decoderMode === 'annexb' ? 'VideoDecoder 直收 Annex B' : 'description=avcC 回退路径'"
    >
      <span class="mirror-statusline-item mirror-statusline-muted">
        {{ decoderMode === 'annexb' ? 'Annex B' : 'avcC' }}
      </span>
    </el-tooltip>
    <span class="mirror-statusline-spacer" />
    <span v-if="busy" class="mirror-statusline-item mirror-statusline-muted">提交中…</span>
    <el-tooltip v-if="input" :content="`待发送：${input}（回车发送）`">
      <span class="mirror-statusline-item mirror-statusline-input">输入：{{ input }}</span>
    </el-tooltip>
    <span v-else class="mirror-statusline-item mirror-statusline-muted">键盘打字 → 回车发送</span>
  </div>
</template>
