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
    <span class="mirror-statusline-dot" aria-hidden>·</span>
    <span class="mirror-statusline-item mirror-statusline-serial" :title="serial">{{ serial }}</span>
    <span class="mirror-statusline-dot" aria-hidden>·</span>
    <span class="mirror-statusline-item">
      {{ resolved ? `${hello?.width}×${hello?.height}` : '分辨率 —' }}
      <template v-if="resolved && hello?.rotation"> / {{ hello.rotation }}°</template>
    </span>
    <template v-if="(stats.dropped ?? 0) > 0">
      <span class="mirror-statusline-dot" aria-hidden>·</span>
      <el-tooltip content="服务端丢帧 + 解码队列主动丢帧">
        <span class="mirror-statusline-item mirror-statusline-warn">丢帧 {{ stats.dropped }}</span>
      </el-tooltip>
    </template>
    <template v-if="stats.serverFps !== undefined && status === 'streaming'">
      <span class="mirror-statusline-dot" aria-hidden>·</span>
      <el-tooltip
        :content="
          '后端从设备读到的帧数/秒（编码器实际出帧），与设置页的帧率上限不是一回事。' +
          (stats.serverFps - (stats.fps ?? 0) > 5
            ? '它明显高于左上角的渲染帧率：浏览器解码/绘制跟不上，优先降低缩放比例。'
            : '画面静止时编码器不出新帧，数字变低是正常的。')
        "
      >
        <span
          class="mirror-statusline-item"
          :class="{ 'mirror-statusline-warn': stats.serverFps - (stats.fps ?? 0) > 5 }"
        >
          设备出帧 {{ stats.serverFps }}fps
        </span>
      </el-tooltip>
    </template>
    <template v-if="status === 'streaming' && (stats.drawMs ?? 0) > 0">
      <span class="mirror-statusline-dot" aria-hidden>·</span>
      <el-tooltip
        content="单帧 drawImage 平均耗时：数值高说明卡在“画”这一层（GPU 上传/缩放）；数值低但设备出帧远高于渲染，说明卡在解码"
      >
        <span class="mirror-statusline-item mirror-statusline-muted">
          绘制 {{ (stats.drawMs ?? 0).toFixed(1) }}ms
        </span>
      </el-tooltip>
    </template>
    <template v-if="status === 'streaming' && (stats.queueSize ?? 0) > 2">
      <span class="mirror-statusline-dot" aria-hidden>·</span>
      <el-tooltip content="WebCodecs 解码队列长度：持续 >2 说明解码跟不上，画面会滞后">
        <span class="mirror-statusline-item mirror-statusline-warn">解码队列 {{ stats.queueSize }}</span>
      </el-tooltip>
    </template>
    <template v-if="decoderMode">
      <span class="mirror-statusline-dot" aria-hidden>·</span>
      <el-tooltip
        :content="decoderMode === 'annexb' ? 'VideoDecoder 直收 Annex B' : 'description=avcC 回退路径'"
      >
        <span class="mirror-statusline-item mirror-statusline-muted">
          {{ decoderMode === 'annexb' ? 'Annex B' : 'avcC' }}
        </span>
      </el-tooltip>
    </template>
    <span class="mirror-statusline-spacer" />
    <span v-if="busy" class="mirror-statusline-item mirror-statusline-muted">提交中…</span>
    <el-tooltip v-if="input" :content="`待发送：${input}（回车发送）`">
      <span class="mirror-statusline-item mirror-statusline-input">输入：{{ input }}</span>
    </el-tooltip>
    <span v-else class="mirror-statusline-item mirror-statusline-muted">键盘打字 → 回车发送</span>
  </div>
</template>
