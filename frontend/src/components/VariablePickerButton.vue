<script setup lang="ts">
// 「{{ }}」变量按钮：从当前脚本声明的变量里选一个插入到输入框光标处。
// 只有 ScriptEditorPage 提供变量上下文时才渲染（其他场景自动隐藏）。
import { computed, ref } from 'vue'

import { useScriptVariables } from '@/composables/useScriptVariables'

const props = withDefaults(
  defineProps<{
    disabled?: boolean
    /** 弹层方向，表单底部字段建议 top。 */
    placement?: 'bottom' | 'bottom-end' | 'top' | 'top-end'
  }>(),
  { disabled: false, placement: 'bottom-end' },
)

const emit = defineEmits<{ pick: [name: string] }>()

const store = useScriptVariables()
const visible = ref(false)
const keyword = ref('')

const variables = computed<Array<{ name: string; value: string }>>(() => {
  const source = store?.variables.value ?? {}
  return Object.entries(source)
    .map(([name, value]) => ({ name, value }))
    .sort((left, right) => left.name.localeCompare(right.name))
})

const missing = computed(() => store?.missing.value ?? [])

function matches(name: string, value = ''): boolean {
  const text = keyword.value.trim().toLowerCase()
  if (!text) return true
  return name.toLowerCase().includes(text) || value.toLowerCase().includes(text)
}

const shownVariables = computed(() =>
  variables.value.filter((item) => matches(item.name, item.value)).slice(0, 100),
)
const shownMissing = computed(() => missing.value.filter((name) => matches(name)).slice(0, 30))

function pick(name: string): void {
  emit('pick', name)
  visible.value = false
}

function declareAndPick(name: string): void {
  store?.addVariable(name)
  pick(name)
}

function openSettings(): void {
  visible.value = false
  store?.openSettings()
}
</script>

<template>
  <el-popover
    v-if="store"
    v-model:visible="visible"
    :width="292"
    :placement="placement"
    :show-arrow="false"
    trigger="click"
    popper-class="var-picker-popper"
  >
    <template #reference>
      <el-button
        class="var-trigger"
        text
        size="small"
        :disabled="disabled"
        title="插入脚本变量（{{ }}）"
        aria-label="插入脚本变量"
      >
        <span class="var-trigger-braces" v-pre>{{}}</span>
      </el-button>
    </template>

    <div class="var-pop">
      <div class="var-pop-head">
        <strong>插入变量</strong>
        <span class="hint">{{ variables.length }} 个已声明</span>
      </div>
      <el-input v-model="keyword" size="small" placeholder="搜索变量名或默认值" clearable />

      <div class="var-list">
        <button
          v-for="item in shownVariables"
          :key="item.name"
          type="button"
          class="var-item"
          @click="pick(item.name)"
        >
          <span class="var-name">{{ item.name }}</span>
          <span class="var-value" :title="item.value">{{ item.value || '（空）' }}</span>
        </button>
        <div v-if="shownVariables.length === 0" class="hint var-empty">
          {{ variables.length === 0 ? '还没有脚本变量，先在脚本设置里添加。' : '没有匹配的变量' }}
        </div>
      </div>

      <div v-if="shownMissing.length > 0" class="var-missing">
        <div class="var-section">脚本里用到但未声明</div>
        <div v-for="name in shownMissing" :key="`missing-${name}`" class="var-item var-item-missing">
          <span class="var-name">{{ name }}</span>
          <el-button text size="small" type="primary" @click="declareAndPick(name)">声明并插入</el-button>
        </div>
      </div>

      <div class="var-foot">
        <span class="hint">插入到光标处</span>
        <el-button text size="small" @click="openSettings">管理变量 →</el-button>
      </div>
    </div>
  </el-popover>
</template>

<style scoped>
.var-trigger {
  flex: none;
  height: 18px;
  padding: 0 2px;
  color: var(--muted-2);
  font-family: var(--mono);
  font-size: 10px;
  line-height: 1;
}

.var-trigger:hover {
  color: var(--accent);
}

.var-trigger-braces {
  letter-spacing: -0.5px;
}
</style>

<style>
/* 弹层被 teleport 到 body，不能放进 scoped。 */
.var-picker-popper.el-popover {
  padding: 9px;
}

.var-picker-popper .var-pop {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.var-picker-popper .var-pop-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
  color: var(--ink);
  font-size: 11.5px;
}

.var-picker-popper .var-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
  max-height: 230px;
  overflow-y: auto;
}

.var-picker-popper .var-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 4px 6px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.var-picker-popper .var-item:hover {
  background: var(--surface-3);
}

.var-picker-popper .var-name {
  flex: none;
  max-width: 46%;
  overflow: hidden;
  color: var(--ink);
  font-family: var(--mono);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.var-picker-popper .var-value {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--muted-2);
  font-size: 10.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.var-picker-popper .var-empty {
  padding: 6px 2px;
  line-height: 1.5;
}

.var-picker-popper .var-missing {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-top: 6px;
  border-top: 1px solid var(--line-soft);
}

.var-picker-popper .var-section {
  color: var(--amber);
  font-size: 10.5px;
  font-weight: 600;
}

.var-picker-popper .var-item-missing {
  cursor: default;
}

.var-picker-popper .var-item-missing:hover {
  background: var(--surface-2);
}

.var-picker-popper .var-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding-top: 6px;
  border-top: 1px solid var(--line-soft);
}
</style>
