<script setup lang="ts">
import { Aim } from '@element-plus/icons-vue'
import { computed, ref } from 'vue'

import VariableField from '@/components/VariableField.vue'
import TargetPickerDialog from '@/features/devices/TargetPickerDialog.vue'
import type { Target, TargetBy } from '@/types/api'
import { numberOrNull } from '@/lib/format'

const props = withDefaults(
  defineProps<{
    value?: Target | null
    allowNull?: boolean
    allowPoint?: boolean
    disabled?: boolean
  }>(),
  { value: null, allowNull: false, allowPoint: true, disabled: false },
)

const emit = defineEmits<{ 'update:value': [target: Target | null] }>()

const ALL_TARGET_BY_OPTIONS: Array<{ value: TargetBy; label: string }> = [
  { value: 'text', label: '文本 text' },
  { value: 'text_contains', label: '文本包含 text_contains' },
  { value: 'resource_id', label: '资源 ID resource_id' },
  { value: 'content_desc', label: '内容描述 content_desc' },
  { value: 'class_name', label: '类名 class_name' },
  { value: 'xpath', label: 'XPath xpath' },
  { value: 'point', label: '坐标点 point' },
]
const options = computed(() =>
  props.allowPoint ? ALL_TARGET_BY_OPTIONS : ALL_TARGET_BY_OPTIONS.filter((item) => item.value !== 'point'),
)

const PLACEHOLDERS: Record<TargetBy, string> = {
  text: '文本，例如：登录',
  text_contains: '包含的文本片段',
  resource_id: 'com.example.app:id/btn_login',
  content_desc: '内容描述',
  class_name: 'android.widget.Button',
  xpath: '//android.widget.Button[@text="登录"]',
  point: '',
}

const enabled = computed(() => !props.allowNull || (props.value !== null && props.value !== undefined))
const pickerOpen = ref(false)
// 懒挂载：条件编辑器里可能有几十个 TargetEditor，没点过按钮就不挂弹窗（避免创建一堆闲置查询）。
const pickerMounted = ref(false)

function openPicker(): void {
  pickerMounted.value = true
  pickerOpen.value = true
}

/** 从截图拾取的元素/坐标直接覆盖当前 Target（index 已按文档顺序算好）。 */
function onPicked(target: Target): void {
  emitTarget(target)
  pickerOpen.value = false
}
const target = computed<Target>(() => props.value ?? { by: 'text', value: '', index: 0 })
const isPoint = computed(() => target.value.by === 'point')
// 非坐标定位必须填值（后端 Target 校验：by=text/resource_id/... requires value），
// 提前在表单里提示，避免点保存才被拦。
const emptyValue = computed(
  () => enabled.value && !isPoint.value && !String(target.value.value ?? '').trim(),
)

function emitTarget(next: Target): void {
  emit('update:value', next)
}

function changeBy(by: TargetBy): void {
  if (by === 'point') {
    emitTarget({ by: 'point', x: target.value.x ?? 0, y: target.value.y ?? 0 })
    return
  }
  emitTarget({
    by,
    value: isPoint.value ? '' : (target.value.value ?? ''),
    index: target.value.index ?? 0,
  })
}

function changeValue(value: string): void {
  emitTarget({ by: target.value.by, value, index: target.value.index ?? 0 })
}

function changeIndex(value: number | undefined): void {
  emitTarget({ by: target.value.by, value: target.value.value ?? '', index: numberOrNull(value) ?? 0 })
}

function changeX(value: number | undefined): void {
  emitTarget({ by: 'point', x: numberOrNull(value) ?? undefined, y: target.value.y })
}

function changeY(value: number | undefined): void {
  emitTarget({ by: 'point', x: target.value.x, y: numberOrNull(value) ?? undefined })
}

function toggleEnabled(checked: boolean): void {
  emit('update:value', checked ? { by: 'text', value: '', index: 0 } : null)
}
</script>

<template>
  <div class="target-row">
    <el-switch
      v-if="allowNull"
      :model-value="enabled"
      size="small"
      :disabled="disabled"
      inline-prompt
      active-text="指定"
      inactive-text="空"
      @update:model-value="toggleEnabled(Boolean($event))"
    />
    <template v-if="enabled">
      <el-select
        :model-value="target.by"
        class="target-by"
        size="small"
        :disabled="disabled"
        @update:model-value="changeBy($event as TargetBy)"
      >
        <el-option
          v-for="option in options"
          :key="option.value"
          :value="option.value"
          :label="option.label"
        />
      </el-select>
      <template v-if="isPoint">
        <el-input-number
          :model-value="target.x"
          class="target-coord"
          size="small"
          :min="0"
          :disabled="disabled"
          controls-position="right"
          @update:model-value="changeX"
        >
          <template #prefix>X</template>
        </el-input-number>
        <el-input-number
          :model-value="target.y"
          class="target-coord"
          size="small"
          :min="0"
          :disabled="disabled"
          controls-position="right"
          @update:model-value="changeY"
        >
          <template #prefix>Y</template>
        </el-input-number>
      </template>
      <template v-else>
        <VariableField
          :model-value="target.value ?? ''"
          class="target-value"
          size="small"
          :placeholder="PLACEHOLDERS[target.by]"
          :disabled="disabled"
          @update:model-value="changeValue"
        />
        <el-input-number
          :model-value="target.index ?? 0"
          class="target-index"
          size="small"
          :min="0"
          :disabled="disabled"
          controls-position="right"
          @update:model-value="changeIndex"
        >
          <template #prefix>#</template>
        </el-input-number>
      </template>
    </template>
    <span v-else class="hint">未指定目标（可为空）</span>
    <el-button
      v-if="enabled"
      class="target-pick"
      size="small"
      :icon="Aim"
      :disabled="disabled"
      title="从当前屏幕截图里选择坐标或元素"
      @click="openPicker"
    >
      从截图选择
    </el-button>
  </div>
  <div v-if="emptyValue" class="hint target-empty">
    定位值不能为空：保存会被校验拦截（填上目标值，或把定位方式改成坐标 point）
  </div>

  <TargetPickerDialog
    v-if="pickerMounted"
    :open="pickerOpen"
    :allow-point="allowPoint"
    @close="pickerOpen = false"
    @pick="onPicked"
  />
</template>

<style scoped>
.target-pick {
  flex: none;
}

.target-empty {
  margin-top: 4px;
  color: var(--el-color-warning);
}
</style>
