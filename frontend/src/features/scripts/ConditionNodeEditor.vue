<script setup lang="ts">
import { ArrowDown, ArrowRight, Delete, Plus, Switch } from '@element-plus/icons-vue'
import { computed, ref } from 'vue'

import {
  CONDITION_TYPE_OPTIONS,
  WEEKDAY_OPTIONS,
  conditionTypeLabel,
  createConditionNode,
  createGroup,
  createNot,
  createTargetCondition,
} from './lib/condition'
import type {
  AppConditionState,
  ConditionGroup,
  ConditionNode,
  ConditionState,
  ConditionTime,
  Target,
} from '@/types/api'
import VariableField from '@/components/VariableField.vue'

import TargetEditor from './TargetEditor.vue'

const props = withDefaults(
  defineProps<{
    node: ConditionNode
    depth?: number
    root?: boolean
    removable?: boolean
  }>(),
  { depth: 0, root: false, removable: true },
)

const emit = defineEmits<{
  'update:node': [node: ConditionNode]
  remove: []
}>()

const collapsed = ref(props.depth >= 3)
const label = computed(() => conditionTypeLabel(props.node))
const isLeaf = computed(() => props.node.type === 'target' || props.node.type === 'app' || props.node.type === 'time')
const isGroup = computed(() => props.node.type === 'group')
const isNot = computed(() => props.node.type === 'not')

function update(next: ConditionNode): void {
  emit('update:node', next)
}

function changeType(value: string): void {
  if (value === 'target' || value === 'app' || value === 'time') {
    update(createConditionNode(value))
  }
}

function addChild(kind: string): void {
  if (props.node.type !== 'group') return
  // 后端限制 group.items ≤50。
  if (props.node.items.length >= 50) return
  let child: ConditionNode
  if (kind === 'group') child = createGroup('and', [createTargetCondition()])
  else if (kind === 'not') child = createNot()
  else child = createConditionNode(kind === 'app' ? 'app' : kind === 'time' ? 'time' : 'target')
  update({ ...props.node, items: [...props.node.items, child] })
}

function updateItem(index: number, item: ConditionNode): void {
  if (props.node.type !== 'group') return
  const items = props.node.items.map((current, currentIndex) => (currentIndex === index ? item : current))
  update({ ...props.node, items })
}

function removeItem(index: number): void {
  if (props.node.type !== 'group') return
  // 后端要求 group.items 至少 1 项；UI 不允许把分组删空（先添加替代条件再删）。
  if (props.node.items.length <= 1) return
  update({ ...props.node, items: props.node.items.filter((_, currentIndex) => currentIndex !== index) })
}

function toggleGroupOp(op: 'and' | 'or'): void {
  if (props.node.type !== 'group') return
  update({ ...props.node, op })
}

function toggleNot(): void {
  if (props.node.type === 'not') {
    update(props.node.item)
  } else {
    update(createNot(props.node))
  }
}

// ---- 叶子节点字段更新 ----
function updateTarget(target: Target | null): void {
  if (props.node.type !== 'target' || !target) return
  update({ ...props.node, target })
}

function setTargetState(state: ConditionState): void {
  if (props.node.type !== 'target') return
  update({ ...props.node, state })
}

function setAppState(state: AppConditionState): void {
  if (props.node.type !== 'app') return
  update({ ...props.node, state })
}

function updateAppField(field: 'package' | 'activity', value: string): void {
  if (props.node.type !== 'app') return
  update({ ...props.node, [field]: value || null })
}

function updateTimeField(field: 'start' | 'end', value: string): void {
  if (props.node.type !== 'time') return
  update({ ...props.node, [field]: value })
}

function toggleDay(day: number): void {
  if (props.node.type !== 'time') return
  const node = props.node as ConditionTime
  // 后端要求 days 至少 1 个。
  if (node.days.length <= 1 && node.days.includes(day)) return
  const days = node.days.includes(day) ? node.days.filter((item) => item !== day) : [...node.days, day].sort((a, b) => a - b)
  update({ ...node, days })
}
</script>

<template>
  <div class="condition-card" :class="node.type">
    <div class="condition-head">
      <button class="icon-btn" style="width: 22px; height: 22px" type="button" @click="collapsed = !collapsed">
        <el-icon :size="12"><ArrowRight v-if="collapsed" /><ArrowDown v-else /></el-icon>
      </button>
      <span class="condition-kind">{{ label }}</span>

      <el-select
        v-if="isLeaf"
        :model-value="node.type"
        size="small"
        style="width: 126px"
        @update:model-value="changeType"
      >
        <el-option v-for="option in CONDITION_TYPE_OPTIONS" :key="option.value" :value="option.value" :label="option.label" />
      </el-select>

      <el-radio-group
        v-if="isGroup"
        :model-value="(node as ConditionGroup).op"
        size="small"
        @update:model-value="toggleGroupOp($event as 'and' | 'or')"
      >
        <el-radio-button value="and">AND</el-radio-button>
        <el-radio-button value="or">OR</el-radio-button>
      </el-radio-group>

      <span v-if="isNot" class="hint">对下方条件取反</span>

      <div class="condition-actions">
        <el-dropdown v-if="isGroup" trigger="click" @command="addChild">
          <el-button size="small" :icon="Plus">条件</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="target">目标 target</el-dropdown-item>
              <el-dropdown-item command="app">应用 app</el-dropdown-item>
              <el-dropdown-item command="time">时间 time</el-dropdown-item>
              <el-dropdown-item command="group" divided>逻辑分组 group</el-dropdown-item>
              <el-dropdown-item command="not">取反 not</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button v-if="isGroup" size="small" @click="addChild('group')">+ 分组</el-button>
        <el-button v-if="!root || isGroup" size="small" :icon="Switch" @click="toggleNot">
          {{ isNot ? '取消取反' : '取反' }}
        </el-button>
        <el-button v-if="!root && removable" size="small" type="danger" plain :icon="Delete" title="删除条件" @click="emit('remove')" />
      </div>
    </div>

    <div v-show="!collapsed" class="condition-body">
      <!-- 分组：任意条件递归 -->
      <template v-if="node.type === 'group'">
        <div v-if="node.items.length === 0" class="condition-empty">
          空 {{ node.op.toUpperCase() }} 分组：点「条件」添加目标 / 应用 / 时间 / 子分组（保存时分组至少 1 项）
        </div>
        <div v-else class="condition-items">
          <div v-for="(item, index) in node.items" :key="index" class="condition-item">
            <span class="condition-branch">{{ index === 0 ? '' : node.op.toUpperCase() }}</span>
            <ConditionNodeEditor
              :node="item"
              :depth="depth + 1"
              :removable="node.items.length > 1"
              @update:node="updateItem(index, $event)"
              @remove="removeItem(index)"
            />
          </div>
        </div>
      </template>

      <!-- 取反：单个子条件 -->
      <template v-else-if="node.type === 'not'">
        <ConditionNodeEditor
          :node="node.item"
          :depth="depth + 1"
          :removable="false"
          @update:node="update({ ...node, item: $event })"
        />
      </template>

      <!-- 目标 -->
      <template v-else-if="node.type === 'target'">
        <div class="condition-inline">
          <span class="hint">目标</span>
          <TargetEditor :value="node.target" :allow-point="false" @update:value="updateTarget" />
          <el-select
            :model-value="node.state"
            size="small"
            style="width: 120px"
            @update:model-value="setTargetState($event as ConditionState)"
          >
            <el-option value="visible" label="可见 visible" />
            <el-option value="gone" label="消失 gone" />
          </el-select>
        </div>
      </template>

      <!-- 应用 -->
      <template v-else-if="node.type === 'app'">
        <div class="condition-inline">
          <VariableField
            :model-value="node.package ?? ''"
            size="small"
            placeholder="package，例如 com.android.settings"
            style="width: 240px"
            @update:model-value="updateAppField('package', $event)"
          />
          <VariableField
            :model-value="node.activity ?? ''"
            size="small"
            placeholder="activity（可选）"
            style="width: 200px"
            @update:model-value="updateAppField('activity', $event)"
          />
          <el-select
            :model-value="node.state"
            size="small"
            style="width: 138px"
            @update:model-value="setAppState($event as AppConditionState)"
          >
            <el-option value="foreground" label="前台 foreground" />
            <el-option value="background" label="后台 background" />
          </el-select>
        </div>
      </template>

      <!-- 时间 -->
      <template v-else-if="node.type === 'time'">
        <div class="condition-inline">
          <el-input
            :model-value="node.start"
            size="small"
            type="time"
            style="width: 126px"
            @update:model-value="updateTimeField('start', $event)"
          />
          <span class="hint">至</span>
          <el-input
            :model-value="node.end"
            size="small"
            type="time"
            style="width: 126px"
            @update:model-value="updateTimeField('end', $event)"
          />
          <div class="weekday-picker">
            <button
              v-for="day in WEEKDAY_OPTIONS"
              :key="day.value"
              type="button"
              :class="['weekday-btn', { on: node.days.includes(day.value) }]"
              @click="toggleDay(day.value)"
            >
              {{ day.label }}
            </button>
            <button type="button" class="weekday-btn" @click="update({ ...node, days: [1, 2, 3, 4, 5, 6, 7] })">
              每天
            </button>
          </div>
          <span class="hint">days 1=周一…7=周日；start &gt; end 表示跨午夜，start == end 表示全天</span>
        </div>
      </template>
    </div>
  </div>
</template>
