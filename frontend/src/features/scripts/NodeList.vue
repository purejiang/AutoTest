<script setup lang="ts">
import { ArrowDown, ArrowUp, Delete, Plus, Search } from '@element-plus/icons-vue'
import { computed, ref } from 'vue'

import { useDebounceFn } from '@vueuse/core'

import type { NodeType, StepNode } from '@/types/api'
import { NODE_TYPE_META, NODE_TYPES } from './nodeDefaults'
import StatusTag from '@/components/StatusTag.vue'

const props = defineProps<{
  steps: StepNode[]
  selectedId: string | null
}>()

const emit = defineEmits<{
  select: [id: string]
  add: [type: NodeType]
  move: [id: string, direction: -1 | 1]
  delete: [id: string]
  toggleEnabled: [id: string, enabled: boolean]
}>()

const search = ref('')
const filteredSearch = ref('')
const updateFilter = useDebounceFn((value: string) => {
  filteredSearch.value = value.trim().toLowerCase()
}, 180)

function onSearchInput(value: string): void {
  search.value = value
  void updateFilter(value)
}

const visibleSteps = computed(() => {
  if (!filteredSearch.value) return props.steps
  return props.steps.filter((step) => {
    const meta = NODE_TYPE_META[step.type]
    const text = [step.id, step.name, step.type, meta.label, step.description].join(' ').toLowerCase()
    return text.includes(filteredSearch.value)
  })
})

function addNode(type: string): void {
  emit('add', type as NodeType)
}
</script>

<template>
  <div class="editor-col">
    <div class="editor-col-head">
      <span class="title">节点 <span class="tag blue">{{ steps.length }}</span></span>
      <el-dropdown trigger="click" @command="addNode">
        <el-button size="small" type="primary" :icon="Plus">新增节点</el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-for="type in NODE_TYPES" :key="type" :command="type">
              {{ NODE_TYPE_META[type].icon }} {{ NODE_TYPE_META[type].label }}（{{ type }}）
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>

    <div class="node-search">
      <el-input
        :model-value="search"
        size="small"
        placeholder="搜索节点"
        clearable
        :prefix-icon="Search"
        @update:model-value="onSearchInput"
      />
    </div>

    <div class="editor-col-body">
      <div
        v-for="(step, index) in visibleSteps"
        :key="step.id"
        :class="['node-row', { selected: step.id === selectedId, disabled: !step.enabled }]"
        :title="`${step.id} · ${step.type}${step.enabled ? '' : '（已禁用）'}`"
        @click="emit('select', step.id)"
      >
        <span class="node-num">{{ steps.findIndex((item) => item.id === step.id) + 1 }}</span>
        <span class="node-ic">{{ NODE_TYPE_META[step.type].icon }}</span>
        <span class="node-copy">
          <span class="node-name">{{ step.name || step.id }}</span>
          <span class="node-type">
            {{ NODE_TYPE_META[step.type].subtitle }}
            <span v-if="step.when" style="color: var(--cyan)"> · when</span>
          </span>
        </span>
        <span class="node-tools" @click.stop>
          <el-switch
            :model-value="step.enabled"
            size="small"
            @update:model-value="emit('toggleEnabled', step.id, Boolean($event))"
          />
          <el-button
            size="small"
            text
            :icon="ArrowUp"
            :disabled="index === 0"
            @click="emit('move', step.id, -1)"
          />
          <el-button
            size="small"
            text
            :icon="ArrowDown"
            :disabled="index === visibleSteps.length - 1"
            @click="emit('move', step.id, 1)"
          />
          <el-button
            size="small"
            text
            type="danger"
            :icon="Delete"
            :disabled="steps.length <= 1"
            @click="emit('delete', step.id)"
          />
        </span>
      </div>
      <el-empty v-if="steps.length === 0" description="还没有节点，点「新增节点」添加" :image-size="56" />
      <div v-else-if="visibleSteps.length === 0" class="hint" style="padding: 20px; text-align: center">
        没有匹配的节点
      </div>
    </div>

    <div class="editor-col-head" style="border-top: 1px solid var(--line); border-bottom: 0">
      <span class="hint">共 {{ steps.length }} 个节点 · 至少保留 1 个</span>
      <StatusTag tone="gray">when 默认关闭</StatusTag>
    </div>
  </div>
</template>
