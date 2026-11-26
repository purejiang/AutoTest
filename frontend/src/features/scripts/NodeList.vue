<script setup lang="ts">
import { Plus, Search } from '@element-plus/icons-vue'
import { useDebounceFn } from '@vueuse/core'
import { computed, onBeforeUnmount, ref } from 'vue'

import type { NodeType, StepNode } from '@/types/api'
import StatusTag from '@/components/StatusTag.vue'

import { NODE_TYPE_GROUPS, NODE_TYPE_META } from './nodeDefaults'
import { describeNodeParams } from './nodeSummary'

type DropPosition = 'before' | 'after'

const props = defineProps<{
  steps: StepNode[]
  selectedId: string | null
  issues?: Record<string, { errors: number; warnings: number }>
}>()

const emit = defineEmits<{
  select: [id: string]
  add: [type: NodeType]
  'add-preset': [preset: 'clear_data']
  reorder: [fromId: string, targetId: string, position: DropPosition]
}>()

const CLEAR_DATA_COMMAND = '__clear_data'

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

function metaOf(step: StepNode) {
  return NODE_TYPE_META[step.type]
}

function stepNumber(stepId: string): number {
  return props.steps.findIndex((item) => item.id === stepId) + 1
}

function nodeTitle(step: StepNode): string {
  return step.name || `${metaOf(step).label}${stepNumber(step.id)}`
}

function paramText(step: StepNode): string {
  return describeNodeParams(step)
}

const visibleSteps = computed(() => {
  if (!filteredSearch.value) return props.steps
  return props.steps.filter((step) => {
    const meta = metaOf(step)
    const text = [
      step.id,
      step.name,
      step.type,
      meta.label,
      step.description,
      describeNodeParams(step),
    ]
      .join(' ')
      .toLowerCase()
    return text.includes(filteredSearch.value)
  })
})

function addNode(command: string): void {
  if (command === CLEAR_DATA_COMMAND) {
    emit('add-preset', 'clear_data')
    return
  }
  emit('add', command as NodeType)
}

function issueTone(stepId: string): 'error' | 'warning' | null {
  const issue = props.issues?.[stepId]
  if (!issue) return null
  if (issue.errors > 0) return 'error'
  return issue.warnings > 0 ? 'warning' : null
}

function issueTitle(stepId: string): string {
  const issue = props.issues?.[stepId]
  if (!issue) return ''
  const parts: string[] = []
  if (issue.errors) parts.push(`${issue.errors} 个错误`)
  if (issue.warnings) parts.push(`${issue.warnings} 个警告`)
  return parts.join(' · ')
}

function clearSearch(): void {
  search.value = ''
  filteredSearch.value = ''
}

defineExpose({ clearSearch })

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
    const row = (event.currentTarget as HTMLElement | null)?.closest('.node-row')
    if (row instanceof HTMLElement) event.dataTransfer.setDragImage(row, 18, 18)
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
          <el-dropdown-menu class="node-add-menu">
            <li class="node-add-group" role="presentation">快捷</li>
            <el-dropdown-item :command="CLEAR_DATA_COMMAND">
              <span class="node-add-icon">🧹</span>
              <span>清除数据</span>
              <span class="node-add-code">app/clear</span>
            </el-dropdown-item>
            <template v-for="group in NODE_TYPE_GROUPS" :key="group.label">
              <li class="node-add-group" role="presentation">{{ group.label }}</li>
              <el-dropdown-item v-for="type in group.types" :key="type" :command="type">
                <span class="node-add-icon">{{ NODE_TYPE_META[type].icon }}</span>
                <span>{{ NODE_TYPE_META[type].label }}</span>
                <span class="node-add-code">{{ type }}</span>
              </el-dropdown-item>
            </template>
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
        aria-label="搜索节点"
        :prefix-icon="Search"
        @update:model-value="onSearchInput"
      />
      <div v-if="isFiltering" class="node-filter-hint">搜索过滤时不可拖拽</div>
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
        :title="`${nodeTitle(step)} · ${step.id}`"
        role="button"
        tabindex="0"
        :aria-current="step.id === selectedId ? 'true' : undefined"
        @click="onRowClick(step)"
        @keydown.enter.prevent="onRowClick(step)"
        @keydown.space.prevent="onRowClick(step)"
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
          <svg class="grip" viewBox="0 0 10 16" width="10" height="16" aria-hidden="true" focusable="false">
            <circle cx="2" cy="2" r="1.2" />
            <circle cx="8" cy="2" r="1.2" />
            <circle cx="2" cy="8" r="1.2" />
            <circle cx="8" cy="8" r="1.2" />
            <circle cx="2" cy="14" r="1.2" />
            <circle cx="8" cy="14" r="1.2" />
          </svg>
        </span>
        <span class="node-num">{{ stepNumber(step.id) }}</span>
        <span class="node-main">
          <span class="node-title">
            <span class="node-name">{{ nodeTitle(step) }}</span>
            <StatusTag class="node-type-tag" :tone="metaOf(step).tone">
              {{ metaOf(step).icon }} {{ metaOf(step).label }}
            </StatusTag>
          </span>
          <span v-if="paramText(step)" class="node-params" :title="paramText(step)">
            {{ paramText(step) }}
          </span>
        </span>
        <span
          v-if="issueTone(step.id)"
          class="node-issue"
          :class="issueTone(step.id)"
          :title="issueTitle(step.id)"
          :aria-label="issueTitle(step.id)"
        />
      </div>
      <el-empty v-if="steps.length === 0" description="还没有节点，点「新增节点」添加" :image-size="56" />
      <div v-else-if="visibleSteps.length === 0" class="hint" style="padding: 20px; text-align: center">
        没有匹配的节点
      </div>
    </div>
  </div>
</template>

<style scoped>
.node-row {
  position: relative;
  display: grid;
  grid-template-columns: 14px minmax(18px, max-content) minmax(0, 1fr) 7px;
  align-items: center;
  gap: 6px;
  min-height: 54px;
  padding: 6px 8px;
}

.node-row:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.node-num {
  width: auto;
  min-width: 18px;
  color: var(--muted-2);
  font-family: var(--mono);
  font-size: 10px;
  text-align: right;
}

.node-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.node-title {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.node-name {
  flex: 1;
  min-width: 0;
}

.node-type-tag {
  flex: none;
}

.node-params {
  overflow: hidden;
  color: var(--muted-2);
  font-size: 10px;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-filter-hint {
  margin-top: 4px;
  color: var(--muted-2);
  font-size: 10px;
}

.node-issue {
  width: 7px;
  height: 7px;
  flex: none;
  border-radius: 50%;
}

.node-issue.error {
  background: var(--red);
  box-shadow: 0 0 0 2px var(--red-soft);
}

.node-issue.warning {
  background: var(--amber);
  box-shadow: 0 0 0 2px var(--amber-soft);
}

.drag-handle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 24px;
  flex: none;
  border-radius: 4px;
  color: var(--muted-2);
  cursor: grab;
}

.grip {
  display: block;
  fill: currentColor;
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
</style>
