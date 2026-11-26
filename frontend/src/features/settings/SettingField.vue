<script setup lang="ts">
import { RefreshLeft } from '@element-plus/icons-vue'

import type { SettingEntry } from '@/types/api'
import { numberOrNull, settingSourceMeta } from '@/lib/format'
import StatusTag from '@/components/StatusTag.vue'

withDefaults(
  defineProps<{
    entry: SettingEntry
    modelValue: any
    source: 'default' | 'file' | 'db' | 'env'
    /** 启动项（bootstrap）：只读，改配置文件/环境变量后重启 */
    readonly?: boolean
    /** 配置文件路径，启动项提示用 */
    configFile?: string
  }>(),
  { readonly: false, configFile: '' },
)

const emit = defineEmits<{
  'update:modelValue': [value: unknown]
  clear: []
}>()

function update(value: unknown): void {
  emit('update:modelValue', value)
}

function sourceTitle(source: 'default' | 'file' | 'db' | 'env'): string {
  if (source === 'db') return '来自数据库覆盖，可点右侧 ↺ 删除；删除后按优先级回到文件/默认值'
  if (source === 'file') return '来自配置文件，优先级高于数据库/默认值；需修改配置文件后重启'
  if (source === 'env') return '来自环境变量，优先级最高；需取消环境变量并重启进程'
  return '当前使用代码默认值'
}

const EMPTY_DEFAULT_LABELS: Record<string, string> = {
  'adb.path': '留空（自动探测）',
  'runtime.data_dir': '留空（默认数据目录）',
  'runtime.frontend_dir': '留空（默认前端目录）',
  'runtime.artifacts_dir': '留空（默认产物目录）',
  'scrcpy.crop': '不裁剪',
  'scrcpy.jar': '留空（内置版本）',
  'security.token': '留空（不校验）',
}

function defaultLabel(entry: SettingEntry): string | null {
  if (entry.default === undefined) return null
  const value = entry.default
  if (entry.type === 'bool') return value ? '开启' : '关闭'
  if (entry.type === 'enum') {
    const option = entry.options?.find((item) => item.value === value)
    return option ? option.label.split('（')[0] : String(value)
  }
  if (value === '' || value === null) {
    return EMPTY_DEFAULT_LABELS[entry.key] ?? '留空'
  }
  return String(value)
}
</script>

<template>
  <div class="setting-field">
    <div class="field-label">
      {{ entry.title }}
      <StatusTag :tone="settingSourceMeta(source).tone" :title="sourceTitle(source)">
        {{ settingSourceMeta(source).label }}
      </StatusTag>
      <el-button
        v-if="source === 'db'"
        size="small"
        text
        :icon="RefreshLeft"
        title="删除数据库覆盖，按优先级回到配置文件/默认值"
        @click="emit('clear')"
      />
    </div>
    <div v-if="entry.desc" class="field-desc">{{ entry.desc }}</div>
    <div v-if="readonly" class="setting-readonly">
      启动项（打开数据库前就要确定，不存数据库）：改
      <code class="code-text">{{ configFile || '~/.autotest/config.json' }}</code>
      <span v-if="entry.env"> 或设置环境变量 <code class="code-text">{{ entry.env }}</code></span>
      ，然后重启 AutoTest。
    </div>

    <el-switch
      v-if="entry.type === 'bool'"
      :model-value="Boolean(modelValue)"
      size="small"
      :disabled="readonly"
      @update:model-value="update($event)"
    />
    <el-select
      v-else-if="entry.type === 'enum'"
      :model-value="modelValue"
      size="small"
      style="width: 100%"
      :disabled="readonly"
      @update:model-value="update($event)"
    >
      <el-option
        v-for="option in entry.options ?? []"
        :key="option.value"
        :value="option.value"
        :label="option.label"
      />
    </el-select>
    <el-input-number
      v-else-if="entry.type === 'int'"
      :model-value="modelValue"
      size="small"
      :min="entry.min"
      :max="entry.max"
      :step="1"
      controls-position="right"
      style="width: 100%"
      :disabled="readonly"
      @update:model-value="update(numberOrNull($event))"
    />
    <el-input
      v-else
      :model-value="modelValue"
      size="small"
      clearable
      class="code-text"
      :disabled="readonly"
      @update:model-value="update($event)"
    />
    <div v-if="defaultLabel(entry) !== null" class="setting-default">
      默认：{{ defaultLabel(entry) }}
    </div>
  </div>
</template>

<style scoped>
.setting-readonly {
  padding: 4px 6px;
  border-radius: 5px;
  background: var(--surface-2);
  color: var(--muted);
  font-size: 10px;
  line-height: 1.5;
}

.setting-readonly code {
  font-size: 10px;
}
</style>
