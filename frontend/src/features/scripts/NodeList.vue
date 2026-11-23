<script setup lang="ts">
import { Delete, Plus, Rank, Search } from '@element-plus/icons-vue'
import { useDebounceFn } from '@vueuse/core'
import { computed, onBeforeUnmount, ref } from 'vue'

import type { NodeType, StepNode } from '@/types/api'
import { NODE_TYPE_META, NODE_TYPES } from './nodeDefaults'
import StatusTag from '@/components/StatusTag.vue'

type DropPosition = 'before' | 'after'

const props = defineProps<{
  steps: StepNode[]
  selectedId: string | null
}>()

const emit = defineEmits<{
  select: [id: string]
  add: [type: NodeType]
  reorder: [fromId: string, targetId: string, position: DropPosition]
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

const isFiltering = computed(() => search.value.trim().length > 0)
const dragDisabled = computed(() => isFiltering.value)

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

// ---------------------------------------------------------------- 拖拽排序
const draggingId = ref<string | null>(null)
const dropTargetId = ref<string | null>(null)
const dropPosition = ref<DropPosition | null>(null)
const suppressClick = ref(false)
let suppressTimer: number | undefined

function onDragStart(step: StepNode, event: DragEvent): void {
  if (dragDisabled.value) {
    event.preventDefault()
    return
  }
  draggingId.value = step.id
  dropTargetId.value = null
  dropPosition.value = null
  suppressClick.value = true
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', step.id)
  }
}

function onDragOver(step: StepNode, event: DragEvent): void {
  if (!draggingId.value) return
  if (draggingId.value === step.id) {
    dropTargetId.value = null
    dropPosition.value = null
    return
  }
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  const after = event.clientY > rect.top + rect.height / 2
  dropTargetId.value = step.id
  dropPosition.value = after ? 'after' : 'before'
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
}

function onDragLeave(step: StepNode, event: DragEvent): void {
  if (dropTargetId.value !== step.id) return
  const related = event.relatedTarget as Node | null
  if (related && (event.currentTarget as HTMLElement).contains(related)) return
  dropTargetId.value = null
  dropPosition.value = null
}

function finishDrag(): void {
  draggingId.value = null
  dropTargetId.value = null
  dropPosition.value = null
  if (suppressTimer !== undefined) window.clearTimeout(suppressTimer)
  if (suppressClick.value) {
    suppressTimer = window.setTimeout(() => {
      suppressClick.value = false
      suppressTimer = undefined
    }, 160)
  }
}

function onDrop(step: StepNode): void {
  const fromId = draggingId.value
  const position = dropPosition.value ?? 'before'
  finishDrag()
  if (fromId && fromId !== step.id) emit('reorder', fromId, step.id, position)
}

function onRowClick(step: StepNode): void {
  if (draggingId.value || suppressClick.value) return
  emit('select', step.id)
}

onBeforeUnmount(() => {
  if (suppressTimer !== undefined) window.clearTimeout(suppressTimer)
})
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
        v-for="step in visibleSteps"
        :key="step.id"
        :class="[
          'node-row',
          {
            selected: step.id === selectedId,
            disabled: !step.enabled,
            dragging: draggingId === step.id,
            'drop-before': dropTargetId === step.id && dropPosition === 'before',
            'drop-after': dropTargetId === step.id && dropPosition === 'after',
          },
        ]"
        :title="`${step.id} · ${step.type}${step.enabled ? '' : '（已禁用）'}`"
        @click="onRowClick(step)"
        @dragover.prevent="onDragOver(step, $event)"
        @dragleave="onDragLeave(step, $event)"
        @drop.prevent="onDrop(step)"
      >
        <span
          class="drag-handle"
          :class="{ disabled: dragDisabled }"
          :draggable="!dragDisabled"
          :title="dragDisabled ? '搜索过滤时不可拖拽' : '拖动排序'"
          aria-label="拖动排序"
          @dragstart="onDragStart(step, $event)"
          @dragend="finishDrag"
        >
          <el-icon :size="13"><Rank /></el-icon>
        </span>
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
            type="danger"
            :icon="Delete"
            :disabled="steps.length <= 1"
            title="删除节点"
            @click="emit('delete', step.id)"
          />
        </span>
      </div>
      <el-empty v-if="steps.length === 0" description="还没有节点，点「新增节点」添加" :image-size="56" />
      <div v-else-if="visibleSteps.length === 0" class="hint" style="padding: 20px; text-align: center">
        没有匹配的节点
      </div>
    </div>

    <div class="editor-col-head editor-col-foot">
      <span class="hint">
        {{ steps.length }} 个节点 ·
        {{ isFiltering ? '搜索过滤时拖拽已禁用' : '拖动手柄排序 · Alt+↑/↓ 也可移动' }}
      </span>
      <StatusTag tone="gray">when 默认关闭</StatusTag>
    </div>
  </div>
</template>

<style scoped>
.node-row {
  position: relative;
}

.drag-handle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 22px;
  flex: none;
  border-radius: 4px;
  color: var(--muted-2);
  cursor: grab;
}

.drag-handle:hover {
  color: var(--ink);
  background: var(--surface-3);
}

.drag-handle:active {
  cursor: grabbing;
}

.drag-handle.disabled {
  color: var(--muted-2);
  cursor: not-allowed;
  opacity: 0.35;
}

.drag-handle.disabled:hover {
  background: transparent;
}

.node-row.dragging {
  opacity: 0.42;
}

.node-row.drop-before {
  box-shadow: inset 0 2px 0 var(--accent);
}

.node-row.drop-after {
  box-shadow: inset 0 -2px 0 var(--accent);
}

.editor-col-foot {
  border-top: 1px solid var(--line);
  border-bottom: 0;
}
</style>
