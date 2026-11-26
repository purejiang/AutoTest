<script setup lang="ts">
// 带「{{ }}」变量按钮的输入框：单行用 suffix，多行在下方右对齐。
// 变量按钮插入到光标处（保留选区），无 provider 时按钮自动隐藏（退化成普通 el-input）。
import { computed, nextTick, ref, useAttrs, type StyleValue } from 'vue'

import { insertToken } from '@/lib/scriptVars'

import VariablePickerButton from './VariablePickerButton.vue'

defineOptions({ inheritAttrs: false })

const props = withDefaults(
  defineProps<{
    modelValue?: string
    /** 多行（textarea）：变量按钮放输入框下方。 */
    multiline?: boolean
    disabled?: boolean
  }>(),
  { modelValue: '', multiline: false, disabled: false },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const attrs = useAttrs()
const boxRef = ref<HTMLElement | null>(null)
// 点变量按钮时输入框会失焦，先记住最后一次光标/选区，插入时再用。
let lastCaret: { start: number; end: number } | null = null

function currentElement(): HTMLInputElement | HTMLTextAreaElement | null {
  return boxRef.value?.querySelector<HTMLInputElement | HTMLTextAreaElement>('input, textarea') ?? null
}

function rememberCaret(): void {
  const el = currentElement()
  if (!el) return
  const fallback = el.value.length
  const start = el.selectionStart ?? fallback
  lastCaret = { start, end: el.selectionEnd ?? start }
}

const rootClass = computed(() => attrs.class)
const rootStyle = computed<StyleValue | undefined>(() => attrs.style as StyleValue | undefined)
const innerAttrs = computed(() => {
  const { class: _class, style: _style, ...rest } = attrs
  return rest
})

function insert(name: string): void {
  const el = currentElement()
  const current = props.modelValue ?? ''
  const focused = el !== null && document.activeElement === el
  let start = current.length
  let end = start
  if (focused && el.selectionStart !== null) {
    start = el.selectionStart
    end = el.selectionEnd ?? start
  } else if (lastCaret) {
    start = lastCaret.start
    end = lastCaret.end
  }
  const result = insertToken(current, name, start, end)
  lastCaret = { start: result.caret, end: result.caret }
  emit('update:modelValue', result.text)
  void nextTick(() => {
    if (!el) return
    el.focus()
    try {
      el.setSelectionRange(result.caret, result.caret)
    } catch {
      // number/email 等 input type 不支持 setSelectionRange
    }
  })
}
</script>

<template>
  <div
    ref="boxRef"
    class="variable-field"
    :class="[rootClass, { 'is-multiline': multiline }]"
    :style="rootStyle"
    @mousedown="rememberCaret"
    @keyup="rememberCaret"
    @focusout="rememberCaret"
  >
    <el-input
      v-bind="innerAttrs"
      :model-value="modelValue"
      :type="multiline ? 'textarea' : 'text'"
      :disabled="disabled"
      @update:model-value="emit('update:modelValue', $event)"
    >
      <template v-if="!multiline" #suffix>
        <VariablePickerButton :disabled="disabled" @pick="insert" />
      </template>
    </el-input>
    <div v-if="multiline" class="variable-field-foot">
      <VariablePickerButton :disabled="disabled" placement="top-end" @pick="insert" />
    </div>
  </div>
</template>

<style scoped>
.variable-field {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.variable-field :deep(.el-input) {
  width: 100%;
}

.variable-field-foot {
  display: flex;
  justify-content: flex-end;
  min-height: 16px;
}
</style>
