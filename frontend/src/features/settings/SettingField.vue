<script setup lang="ts">
import { RefreshLeft } from '@element-plus/icons-vue'

import type { SettingEntry } from '@/types/api'
import { numberOrNull, settingSourceMeta } from '@/lib/format'
import HelpTip from '@/components/HelpTip.vue'
import StatusTag from '@/components/StatusTag.vue'

const props = withDefaults(
  defineProps<{
    entry: SettingEntry
    modelValue: string | number | boolean | null
    source: 'default' | 'file' | 'env'
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

function sourceTitle(source: 'default' | 'file' | 'env'): string {
  if (source === 'file') return '来自配置文件；点右侧 ↺ 可把该项从配置文件移除（回到默认/环境变量）'
  if (source === 'env') return '来自环境变量，优先级最高；需取消环境变量并重启进程'
  return '当前使用代码默认值'
}

function readonlyHelp(entry: SettingEntry): string {
  const file = props.configFile || '~/.autotest/config.json'
  const env = entry.env ? `，或设置环境变量 ${entry.env}` : ''
  return `启动时确定，运行中不能切换；改 ${file}${env}，然后重启 AutoTest。`
}

const ZERO_LABELS: Record<string, string> = {
  'engine.default_delay_before_ms': '0（不停顿）',
  'engine.default_delay_after_ms': '0（不停顿）',
  'scrcpy.max_fps': '0（不限）',
}

function defaultLabel(entry: SettingEntry): string | null {
  // 字符串项的默认值直接作为输入框占位符，不再在下方重复一行。
  if (entry.type === 'string' || entry.default === undefined) return null
  const value = entry.default
  if (entry.type === 'bool') return value ? '开启' : '关闭'
  if (entry.type === 'enum') {
    const option = entry.options?.find((item) => item.value === value)
    return option ? option.label.split('（')[0] : String(value)
  }
  if (value === 0 && entry.key in ZERO_LABELS) return ZERO_LABELS[entry.key]
  return String(value)
}

function intPlaceholder(entry: SettingEntry): string {
  return entry.default === undefined || entry.default === null ? '' : `默认 ${entry.default}`
}
</script>

<template>
  <div class="setting-field">
    <div class="setting-field-body">
      <div class="field-label">
        {{ entry.title }}
        <HelpTip
          v-if="readonly || entry.help"
          :text="readonly ? readonlyHelp(entry) : (entry.help ?? '')"
        />
        <StatusTag :tone="settingSourceMeta(source).tone" :title="sourceTitle(source)">
          {{ settingSourceMeta(source).label }}
        </StatusTag>
        <el-button
          v-if="source === 'file'"
          size="small"
          text
          :icon="RefreshLeft"
          title="从配置文件移除该项，回到默认值/环境变量"
          @click="emit('clear')"
        />
      </div>
      <div v-if="entry.desc" class="hint field-desc">{{ entry.desc }}</div>
      <div v-if="readonly" class="hint setting-readonly">启动项：改配置文件/环境变量后重启</div>

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
        :placeholder="intPlaceholder(entry)"
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
        :placeholder="entry.default_display ?? ''"
        :disabled="readonly"
        @update:model-value="update($event)"
      />
      <div v-if="defaultLabel(entry) !== null" class="setting-default">
        默认：{{ defaultLabel(entry) }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.setting-field {
  container-type: inline-size;
  min-width: 0;
}

.setting-field-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 0;
}

/* 宽卡片：一行一个设置，标签/说明在左、控件在右；窄卡片（如工具链）自动退回上下排列。 */
@container (min-width: 640px) {
  .setting-field-body {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 280px;
    column-gap: 24px;
    align-items: start;
    padding: 10px 0;
    border-bottom: 1px solid var(--line-soft);
  }

  .setting-field-body .field-label {
    grid-column: 1;
    grid-row: 1;
  }

  .setting-field-body .field-desc {
    grid-column: 1;
    grid-row: 2;
    margin-top: 2px;
  }

  .setting-field-body .setting-readonly {
    grid-column: 1;
    grid-row: 3;
    margin-top: 2px;
  }

  .setting-field-body > .el-switch,
  .setting-field-body > .el-select,
  .setting-field-body > .el-input-number,
  .setting-field-body > .el-input {
    grid-column: 2;
    grid-row: 1 / span 2;
    align-self: start;
    width: 100%;
  }

  .setting-field-body > .el-switch {
    justify-self: start;
    width: auto;
  }

  .setting-field-body > .setting-default {
    grid-column: 2;
    grid-row: 3;
    text-align: left;
  }

  .setting-field:last-child .setting-field-body {
    border-bottom: 0;
  }
}

</style>
