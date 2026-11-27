<script setup lang="ts">
import { computed } from 'vue'
import { Delete, Plus, RefreshLeft } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import VariableField from '@/components/VariableField.vue'

const props = withDefaults(
  defineProps<{
    value: Record<string, string>
    keyPlaceholder?: string
    valuePlaceholder?: string
    emptyHint?: string
    /** 值输入框是否提供「{{ }}」变量按钮（脚本设置里声明变量时不需要）。 */
    variablePicker?: boolean
    /** 固定展示的子变量名（如 sub_script 子脚本变量）；未覆盖时显示继承默认值。 */
    presetKeys?: string[]
    /** presetKeys 的默认值，用于占位提示。 */
    presetDefaults?: Record<string, string>
    /** 是否允许手动添加额外变量；固定变量场景（如 sub_script）可关闭。 */
    allowAdd?: boolean
  }>(),
  {
    keyPlaceholder: '变量名',
    valuePlaceholder: '值（运行时可被请求覆盖）',
    emptyHint: '暂无变量',
    variablePicker: false,
    presetKeys: () => [],
    presetDefaults: () => ({}),
    allowAdd: true,
  },
)

const emit = defineEmits<{ 'update:value': [value: Record<string, string>] }>()

const presetSet = computed(() => new Set(props.presetKeys))
const displayKeys = computed(() => {
  const seen = new Set<string>()
  const keys: string[] = []
  for (const key of props.presetKeys) {
    if (!seen.has(key)) {
      seen.add(key)
      keys.push(key)
    }
  }
  for (const key of Object.keys(props.value)) {
    if (!seen.has(key)) {
      seen.add(key)
      keys.push(key)
    }
  }
  return keys
})

function isPreset(key: string): boolean {
  return presetSet.value.has(key)
}

function placeholderFor(key: string): string {
  if (!isPreset(key)) return props.valuePlaceholder
  if (key in props.presetDefaults) {
    const fallback = props.presetDefaults[key]
    return fallback ? `继承默认：${fallback}` : '继承默认：空'
  }
  return '脚本里用到但未声明'
}

function renameKey(oldKey: string, nextKey: string): void {
  if (nextKey === oldKey) return
  if (nextKey && nextKey in props.value) {
    ElMessage.warning(`变量名「${nextKey}」已存在`)
    return
  }
  const next: Record<string, string> = {}
  for (const [key, itemValue] of Object.entries(props.value)) {
    next[key === oldKey ? nextKey : key] = itemValue
  }
  emit('update:value', next)
}

function setValue(key: string, nextValue: string): void {
  emit('update:value', { ...props.value, [key]: nextValue })
}

function removeKey(key: string): void {
  const next = { ...props.value }
  delete next[key]
  emit('update:value', next)
}

function addKey(): void {
  let index = Object.keys(props.value).length + 1
  let key = `var_${index}`
  while (key in props.value) {
    index += 1
    key = `var_${index}`
  }
  emit('update:value', { ...props.value, [key]: '' })
}
</script>

<template>
  <div class="kv-editor">
    <div v-if="displayKeys.length === 0" class="hint">{{ emptyHint }}</div>
    <div v-for="key in displayKeys" :key="`kv-${key}`" class="kv-row">
      <code v-if="isPreset(key)" class="kv-preset-key" :title="key">{{ key }}</code>
      <el-input
        v-else
        :model-value="key"
        size="small"
        :placeholder="keyPlaceholder"
        @update:model-value="renameKey(key, $event)"
      />
      <VariableField
        v-if="variablePicker"
        :model-value="value[key] ?? ''"
        size="small"
        :placeholder="placeholderFor(key)"
        @update:model-value="setValue(key, $event)"
      />
      <el-input
        v-else
        :model-value="value[key] ?? ''"
        size="small"
        :placeholder="placeholderFor(key)"
        @update:model-value="setValue(key, $event)"
      />
      <el-button
        v-if="isPreset(key)"
        size="small"
        :icon="RefreshLeft"
        title="恢复继承"
        :disabled="!(key in value)"
        @click="removeKey(key)"
      />
      <el-button v-else size="small" :icon="Delete" title="删除" @click="removeKey(key)" />
    </div>
    <el-button v-if="allowAdd" size="small" :icon="Plus" plain @click="addKey">添加变量</el-button>
  </div>
</template>

<style scoped>
.kv-editor {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
}
.kv-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.kv-row .el-input:first-child,
.kv-preset-key {
  width: 35%;
  flex: none;
}

.kv-preset-key {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted);
  font-size: 11px;
}

.kv-row .variable-field {
  flex: 1;
  min-width: 0;
}
</style>
