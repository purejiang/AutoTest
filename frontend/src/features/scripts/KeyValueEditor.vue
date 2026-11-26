<script setup lang="ts">
import { Delete, Plus } from '@element-plus/icons-vue'
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
  }>(),
  {
    keyPlaceholder: '变量名',
    valuePlaceholder: '值（运行时可被请求覆盖）',
    emptyHint: '暂无变量',
    variablePicker: false,
  },
)

const emit = defineEmits<{ 'update:value': [value: Record<string, string>] }>()

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
    <div v-if="Object.keys(value).length === 0" class="hint">{{ emptyHint }}</div>
    <div v-for="([key, itemValue], index) in Object.entries(value)" :key="`kv-${index}`" class="kv-row">
      <el-input
        :model-value="key"
        size="small"
        :placeholder="keyPlaceholder"
        @update:model-value="renameKey(key, $event)"
      />
      <VariableField
        v-if="variablePicker"
        :model-value="itemValue"
        size="small"
        :placeholder="valuePlaceholder"
        @update:model-value="setValue(key, $event)"
      />
      <el-input
        v-else
        :model-value="itemValue"
        size="small"
        :placeholder="valuePlaceholder"
        @update:model-value="setValue(key, $event)"
      />
      <el-button size="small" :icon="Delete" title="删除" @click="removeKey(key)" />
    </div>
    <el-button size="small" :icon="Plus" plain @click="addKey">添加变量</el-button>
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
.kv-row .el-input:first-child {
  width: 35%;
  flex: none;
}

.kv-row .variable-field {
  flex: 1;
  min-width: 0;
}
</style>
