<script setup lang="ts">
import { Delete } from '@element-plus/icons-vue'
import { nextTick, ref, watch } from 'vue'

import type { RunEvent } from '@/types/api'
import { formatTime, runEventLabel, runEventTone, summarizeRunEvent } from '@/lib/format'
import StatusTag from '@/components/StatusTag.vue'

const props = defineProps<{
  events: RunEvent[]
  connected: boolean
  reconnectAttempt: number
  autoScroll: boolean
}>()

const emit = defineEmits<{
  toggleAutoScroll: [value: boolean]
  clear: []
}>()

const listRef = ref<HTMLDivElement | null>(null)

watch(
  () => [props.events.length, props.autoScroll] as const,
  async () => {
    if (!props.autoScroll) return
    await nextTick()
    const element = listRef.value
    if (element) element.scrollTop = element.scrollHeight
  },
)
</script>

<template>
  <div>
    <div class="card-head">
      <h2>
        实时事件流
        <span class="tag blue" style="margin-left: 4px">{{ events.length }}</span>
        <span class="dot" :class="connected ? 'live' : 'offline'" style="margin-left: 6px" />
        <span class="hint" style="margin-left: 3px">
          {{ connected ? 'WS 已连接' : reconnectAttempt > 0 ? `重连中（第 ${reconnectAttempt} 次）` : 'WS 未连接' }}
        </span>
      </h2>
      <div class="head-actions">
        <span class="hint">自动滚底</span>
        <el-switch
          :model-value="autoScroll"
          size="small"
          @update:model-value="emit('toggleAutoScroll', Boolean($event))"
        />
        <el-button size="small" :icon="Delete" :disabled="events.length === 0" @click="emit('clear')">清空</el-button>
      </div>
    </div>
    <div class="card-body">
      <el-empty v-if="events.length === 0" description="暂无事件（运行开始后会实时推送）" :image-size="56" />
      <div v-else ref="listRef" class="event-list">
        <div v-for="event in events" :key="event.seq" class="event-item">
          <span class="event-seq">#{{ event.seq }}</span>
          <StatusTag :tone="runEventTone(event.type, event.payload)" :title="event.type">
            {{ runEventLabel(event.type) }}
          </StatusTag>
          <span class="event-time">{{ formatTime(event.created_at) }}</span>
          <span class="event-payload">{{ summarizeRunEvent(event) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
