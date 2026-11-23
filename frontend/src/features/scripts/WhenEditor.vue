<script setup lang="ts">
import { computed } from 'vue'

import { createDefaultWhen, describeWhen } from '@/lib/condition'
import type { When } from '@/types/api'

import ConditionNodeEditor from './ConditionNodeEditor.vue'

const props = defineProps<{ modelValue: When | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: When | null] }>()

const enabled = computed(() => props.modelValue !== null)
const summary = computed(() => describeWhen(props.modelValue))

function toggle(checked: boolean): void {
  emit('update:modelValue', checked ? createDefaultWhen() : null)
}

function patch(partial: Partial<When>): void {
  if (!props.modelValue) return
  emit('update:modelValue', { ...props.modelValue, ...partial })
}
</script>

<template>
  <div class="condition-root">
    <div class="row wrap">
      <el-switch :model-value="enabled" size="small" @update:model-value="toggle(Boolean($event))" />
      <span class="small">启用执行条件</span>
      <span class="spacer" />
      <span class="hint">{{ enabled ? summary : '默认关闭：节点无条件直接执行' }}</span>
    </div>

    <template v-if="modelValue">
      <div class="condition-inline">
        <el-select
          :model-value="modelValue.mode"
          size="small"
          style="width: 176px"
          @update:model-value="patch({ mode: $event as 'require' | 'optional' })"
        >
          <el-option value="require" label="require 不满足则失败" />
          <el-option value="optional" label="optional 不满足则跳过" />
        </el-select>
        <el-input-number
          :model-value="modelValue.timeout_ms"
          size="small"
          :min="0"
          :max="600000"
          :step="500"
          controls-position="right"
          style="width: 160px"
          @update:model-value="patch({ timeout_ms: Number($event) || 0 })"
        >
          <template #prefix>超时</template>
        </el-input-number>
        <el-input-number
          :model-value="modelValue.poll_interval_ms"
          size="small"
          :min="50"
          :max="5000"
          :step="50"
          controls-position="right"
          style="width: 170px"
          @update:model-value="patch({ poll_interval_ms: Number($event) || 350 })"
        >
          <template #prefix>轮询</template>
        </el-input-number>
        <span class="hint">ms；timeout 0 = 只查一次 · 分组/取反嵌套 ≤4 层</span>
      </div>

      <ConditionNodeEditor
        root
        :node="modelValue.condition"
        @update:node="patch({ condition: $event })"
      />
    </template>
  </div>
</template>
