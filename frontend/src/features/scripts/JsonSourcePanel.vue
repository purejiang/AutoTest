<script setup lang="ts">
import { basicSetup } from 'codemirror'
import { json } from '@codemirror/lang-json'
import { Compartment, EditorState } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { oneDark } from '@codemirror/theme-one-dark'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Check, MagicStick, RefreshLeft } from '@element-plus/icons-vue'

import type { ScriptValidation } from '@/types/api'
import { useTheme } from '@/composables/useTheme'
import StatusTag from '@/components/StatusTag.vue'

const props = defineProps<{
  text: string
  dirty: boolean
  parseError: string | null
  validation: ScriptValidation | null
}>()

const emit = defineEmits<{
  'update:text': [text: string]
  format: []
  apply: []
  reset: []
  jumpPath: [path: string]
}>()

const host = ref<HTMLDivElement | null>(null)
let view: EditorView | null = null
let applyingExternal = false

const { resolvedTheme } = useTheme()

// 浅色主题保持 v0.2.4 观感；深色用 one-dark 打底，再用项目 token 对齐背景/边框。
const lightTheme = EditorView.theme({
  '&': { height: '100%', backgroundColor: '#ffffff', fontSize: '12px' },
  '.cm-scroller': {
    overflow: 'auto',
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
  },
  '.cm-content': { padding: '8px 0' },
  '.cm-gutters': { backgroundColor: '#fafbfd', borderRight: '1px solid #eef2f7', color: '#94a3b8' },
  '&.cm-focused': { outline: 'none' },
})

const darkTheme = [
  oneDark,
  EditorView.theme({
    '&': { height: '100%', backgroundColor: 'var(--surface)', fontSize: '12px' },
    '.cm-scroller': {
      overflow: 'auto',
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    },
    '.cm-content': { padding: '8px 0' },
    '.cm-gutters': {
      backgroundColor: 'var(--surface-2)',
      borderRight: '1px solid var(--line)',
      color: 'var(--muted-2)',
    },
    '.cm-activeLine': { backgroundColor: 'var(--surface-2)' },
    '.cm-activeLineGutter': { backgroundColor: 'var(--surface-2)' },
    '&.cm-focused': { outline: 'none' },
  }),
]

const themeCompartment = new Compartment()

function themeExtensions() {
  return resolvedTheme.value === 'dark' ? darkTheme : lightTheme
}

onMounted(async () => {
  await nextTick()
  if (!host.value) return
  view = new EditorView({
    parent: host.value,
    state: EditorState.create({
      doc: props.text,
      extensions: [
        basicSetup,
        json(),
        themeCompartment.of(themeExtensions()),
        EditorView.updateListener.of((update) => {
          if (!update.docChanged || applyingExternal) return
          emit('update:text', update.state.doc.toString())
        }),
      ],
    }),
  })
})

// 主题切换只重配主题 compartment，不动文档/选区，也不触发 update:text。
watch(resolvedTheme, (value) => {
  if (!view) return
  view.dispatch({
    effects: themeCompartment.reconfigure(value === 'dark' ? darkTheme : lightTheme),
  })
})

watch(
  () => props.text,
  (value) => {
    if (!view) return
    const current = view.state.doc.toString()
    if (current === value) return
    applyingExternal = true
    view.dispatch({ changes: { from: 0, to: current.length, insert: value } })
    applyingExternal = false
  },
)

onBeforeUnmount(() => {
  view?.destroy()
  view = null
})
</script>

<template>
  <div class="editor-col">
    <div class="editor-col-head">
      <span class="title">
        JSON 源码
        <StatusTag :tone="dirty ? 'amber' : 'green'">{{ dirty ? '未应用' : '与表单同步' }}</StatusTag>
      </span>
      <div class="row" style="gap: 4px">
        <el-button
          size="small"
          :icon="RefreshLeft"
          :disabled="!dirty"
          title="放弃源码改动，恢复为表单内容"
          @click="emit('reset')"
          >重置</el-button
        >
        <el-button size="small" :icon="MagicStick" @click="emit('format')">格式化</el-button>
        <el-button size="small" type="primary" plain :icon="Check" @click="emit('apply')"
          >应用到表单</el-button
        >
      </div>
    </div>

    <div class="json-editor-host">
      <div ref="host" style="height: 100%" />
    </div>

    <div v-if="parseError" style="padding: 8px">
      <el-alert type="error" :closable="false" show-icon title="JSON 解析失败" :description="parseError" />
    </div>

    <div v-if="validation" class="validation-list">
      <div class="row" style="margin-bottom: 4px">
        <strong class="small">校验结果</strong>
        <StatusTag :tone="validation.valid ? 'green' : 'red'">{{
          validation.valid ? '通过' : '未通过'
        }}</StatusTag>
        <span class="hint">错误 {{ validation.errors.length }} · 警告 {{ validation.warnings.length }}</span>
      </div>
      <div
        v-for="(item, index) in validation.errors"
        :key="`error-${index}-${item.path}`"
        class="validation-item"
        :title="item.path ? `点击跳转：${item.path}` : undefined"
        @click="emit('jumpPath', item.path)"
      >
        <StatusTag tone="red">错误</StatusTag>
        <span class="code-text">{{ item.path || 'script' }}</span>
        <span>{{ item.message }}</span>
      </div>
      <div
        v-for="(item, index) in validation.warnings"
        :key="`warning-${index}-${item.path}`"
        class="validation-item"
        :title="item.path ? `点击跳转：${item.path}` : undefined"
        @click="emit('jumpPath', item.path)"
      >
        <StatusTag tone="amber">警告</StatusTag>
        <span class="code-text">{{ item.path || 'script' }}</span>
        <span>{{ item.message }}</span>
      </div>
      <div v-if="validation.errors.length === 0 && validation.warnings.length === 0" class="hint">
        没有发现错误
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 深色下容器/校验列表跟随项目 token，避免写死浅色背景；浅色保持原有观感。 */
.json-editor-host :deep(.cm-editor) {
  background: var(--surface);
}

html.dark .json-editor-host {
  border: 1px solid var(--line);
  border-radius: 6px;
}

.validation-list {
  border-top: 1px solid var(--line);
  background: var(--surface);
}

.validation-item:hover {
  background: var(--surface-hover);
}
</style>
