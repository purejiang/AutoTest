<script setup lang="ts">
import { ArrowLeft, Check, Document, Refresh, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useWindowSize } from '@vueuse/core'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'

import { describeApiError, validationErrorsOf } from '@/api/client'
import { useScript, useUpdateScript, useValidateScriptData } from '@/api/scripts'
import type { NodeType, Script, ScriptData, ScriptValidation, StepNode } from '@/types/api'
import { prettyJson, safeParseJson } from '@/lib/format'
import PageHead from '@/components/PageHead.vue'
import StatusTag from '@/components/StatusTag.vue'

import JsonSourcePanel from './JsonSourcePanel.vue'
import NodeForm from './NodeForm.vue'
import NodeList from './NodeList.vue'
import ScriptSettingsForm from './ScriptSettingsForm.vue'
import { cloneScriptData, createStep, normalizeDraft, normalizeScriptData } from './nodeDefaults'

const route = useRoute()
const router = useRouter()
const id = computed(() => String(route.params.id ?? ''))

const scriptQuery = useScript(id)
const updateMutation = useUpdateScript(id)
const validateMutation = useValidateScriptData()

const draft = ref<ScriptData | null>(null)
const selectedId = ref<string | null>(null)
const dirty = ref(false)
const jsonText = ref('')
const jsonDirty = ref(false)
const jsonError = ref<string | null>(null)
const validation = ref<ScriptValidation | null>(null)
const settingsOpen = ref<string[]>([])
const jsonOpen = ref(false)
const loadedId = ref<string | null>(null)

const { width: windowWidth } = useWindowSize()
const jsonDrawerSize = computed(() => (windowWidth.value <= 900 ? '100%' : '560px'))

function loadFromScript(script: Script): void {
  if (loadedId.value === script.id) return
  loadedId.value = script.id
  const data = cloneScriptData(normalizeScriptData(script))
  draft.value = data
  selectedId.value = data.steps[0]?.id ?? null
  jsonText.value = prettyJson(data)
  jsonDirty.value = false
  jsonError.value = null
  dirty.value = false
  validation.value = null
}

watch(
  () => scriptQuery.data.value,
  (script) => {
    if (script) loadFromScript(script)
  },
  { immediate: true },
)

const selectedIndex = computed(() => {
  if (!draft.value || !selectedId.value) return -1
  return draft.value.steps.findIndex((step) => step.id === selectedId.value)
})

const selectedNode = computed(() => {
  const data = draft.value
  return data && selectedIndex.value >= 0 ? data.steps[selectedIndex.value] : null
})

const stepCount = computed(() => draft.value?.steps.length ?? 0)

function applyDraft(next: ScriptData): void {
  draft.value = next
  dirty.value = true
  validation.value = null
  if (!jsonDirty.value) {
    jsonText.value = prettyJson(next)
    jsonError.value = null
  }
}

function patchNode(next: StepNode): void {
  const data = draft.value
  if (!data) return
  applyDraft({ ...data, steps: data.steps.map((step) => (step.id === next.id ? next : step)) })
}

function addNode(type: NodeType): void {
  const data = draft.value
  if (!data) return
  const step = createStep(
    type,
    data.steps.map((item) => item.id),
  )
  const steps = [...data.steps]
  const insertAt = selectedIndex.value >= 0 ? selectedIndex.value + 1 : steps.length
  steps.splice(insertAt, 0, step)
  applyDraft({ ...data, steps })
  selectedId.value = step.id
}

function moveNode(nodeId: string, direction: -1 | 1): void {
  const data = draft.value
  if (!data) return
  const index = data.steps.findIndex((step) => step.id === nodeId)
  const targetIndex = index + direction
  if (index < 0 || targetIndex < 0 || targetIndex >= data.steps.length) return
  const steps = [...data.steps]
  const temp = steps[index]
  steps[index] = steps[targetIndex]
  steps[targetIndex] = temp
  applyDraft({ ...data, steps })
}

/** 拖拽排序：只改本地 steps 并标 dirty，保存仍走顶部按钮；step.id 保持稳定。 */
function reorderNode(fromId: string, targetId: string, position: 'before' | 'after'): void {
  const data = draft.value
  if (!data || fromId === targetId) return
  const fromIndex = data.steps.findIndex((step) => step.id === fromId)
  const targetIndex = data.steps.findIndex((step) => step.id === targetId)
  if (fromIndex < 0 || targetIndex < 0) return
  const steps = [...data.steps]
  const moved = steps.splice(fromIndex, 1)[0]
  let insertAt = targetIndex + (position === 'after' ? 1 : 0)
  if (fromIndex < insertAt) insertAt -= 1
  steps.splice(insertAt, 0, moved)
  if (steps.every((step, index) => step.id === data.steps[index]?.id)) return
  applyDraft({ ...data, steps })
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (target.isContentEditable) return true
  return Boolean(target.closest('[contenteditable="true"]'))
}

/** 全局 Alt+↑/↓：移动当前选中节点；输入框/文本域/contenteditable 内不拦截。 */
function handleAltMove(event: KeyboardEvent): void {
  if (!event.altKey || event.ctrlKey || event.metaKey) return
  if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
  if (isEditableTarget(event.target)) return
  const nodeId = selectedId.value
  if (!nodeId || !draft.value) return
  event.preventDefault()
  moveNode(nodeId, event.key === 'ArrowUp' ? -1 : 1)
}

onMounted(() => window.addEventListener('keydown', handleAltMove))
onBeforeUnmount(() => window.removeEventListener('keydown', handleAltMove))

onBeforeRouteLeave(async () => {
  if (!dirty.value && !jsonDirty.value) return true
  try {
    await ElMessageBox.confirm('当前脚本有未保存的修改，离开后修改将丢失。', '未保存的修改', {
      type: 'warning',
      confirmButtonText: '离开',
      cancelButtonText: '留在此页',
    })
    return true
  } catch {
    return false
  }
})

function goRun(): void {
  void router.push({ path: '/console', query: { tab: 'auto', script: id.value } })
}

function deleteNode(nodeId: string): void {
  const data = draft.value
  if (!data || data.steps.length <= 1) return
  const index = data.steps.findIndex((step) => step.id === nodeId)
  const steps = data.steps.filter((step) => step.id !== nodeId)
  applyDraft({ ...data, steps })
  if (selectedId.value === nodeId) {
    selectedId.value = steps[Math.min(Math.max(index, 0), steps.length - 1)]?.id ?? null
  }
}

function toggleNodeEnabled(nodeId: string, enabled: boolean): void {
  const data = draft.value
  if (!data) return
  applyDraft({
    ...data,
    steps: data.steps.map((step) => (step.id === nodeId ? { ...step, enabled } : step)),
  })
}

function parseJsonToData(text: string): { data?: ScriptData; error?: string } {
  const parsed = safeParseJson<unknown>(text)
  if (!parsed.ok) return { error: parsed.error }
  const value = parsed.value
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { error: '顶层必须是 JSON 对象' }
  const record = value as Partial<ScriptData>
  if (!Array.isArray(record.steps)) return { error: '缺少 steps 数组' }
  if (typeof record.name !== 'string' || !record.name.trim()) return { error: '缺少 name 字段（脚本名称）' }
  return { data: normalizeDraft(record) }
}

function handleJsonChange(text: string): void {
  jsonText.value = text
  jsonDirty.value = true
  const parsed = parseJsonToData(text)
  jsonError.value = parsed.error ?? null
}

function handleFormat(): void {
  const parsed = parseJsonToData(jsonText.value)
  if (parsed.error || !parsed.data) {
    jsonError.value = parsed.error ?? 'JSON 无效'
    return
  }
  jsonText.value = prettyJson(parsed.data)
  jsonError.value = null
}

function handleApplyJson(): void {
  const parsed = parseJsonToData(jsonText.value)
  if (parsed.error || !parsed.data) {
    jsonError.value = parsed.error ?? 'JSON 无效'
    ElMessage.error(`JSON 无效：${parsed.error ?? '未知错误'}`)
    return
  }
  draft.value = parsed.data
  dirty.value = true
  jsonDirty.value = false
  jsonText.value = prettyJson(parsed.data)
  jsonError.value = null
  validation.value = null
  if (!parsed.data.steps.some((step) => step.id === selectedId.value)) {
    selectedId.value = parsed.data.steps[0]?.id ?? null
  }
  ElMessage.success('已应用到表单')
}

/** 保存/校验前取有效数据：JSON 有改动且能解析时以源码为准。 */
function effectiveData(): ScriptData | null {
  const data = draft.value
  if (!data) return null
  if (!jsonDirty.value) return data
  const parsed = parseJsonToData(jsonText.value)
  if (parsed.error || !parsed.data) {
    const errorText = parsed.error ?? 'JSON 无效'
    jsonError.value = errorText
    ElMessage.error(`JSON 无效：${errorText}`)
    return null
  }
  draft.value = parsed.data
  dirty.value = true
  jsonDirty.value = false
  jsonText.value = prettyJson(parsed.data)
  jsonError.value = null
  return parsed.data
}

async function handleSave(): Promise<void> {
  const data = effectiveData()
  if (!data) return
  if (data.steps.length === 0) {
    ElMessage.warning('脚本至少需要一个节点')
    return
  }
  try {
    await updateMutation.mutateAsync({ name: data.name, notes: data.notes, data })
    dirty.value = false
    validation.value = null
    loadedId.value = id.value
    ElMessage.success('脚本已保存')
  } catch (error) {
    const errors = validationErrorsOf(error)
    if (errors.length > 0) validation.value = { valid: false, errors, warnings: [] }
  }
}

async function handleValidate(): Promise<void> {
  const data = effectiveData()
  if (!data) return
  try {
    const result = await validateMutation.mutateAsync({ data })
    validation.value = result
    if (result.valid) ElMessage.success('校验通过')
  } catch (error) {
    const errors = validationErrorsOf(error)
    if (errors.length > 0) validation.value = { valid: false, errors, warnings: [] }
  }
}

function jumpToPath(path: string): void {
  const data = draft.value
  if (!data) return
  const match = path.match(/steps[.[](\d+)/)
  if (!match) return
  const index = Number(match[1])
  const node = data.steps[index]
  if (node) selectedId.value = node.id
}
</script>

<template>
  <div v-if="scriptQuery.isLoading.value" class="page">
    <el-skeleton :rows="10" animated />
  </div>

  <div v-else-if="scriptQuery.isError.value" class="page">
    <el-result icon="warning" title="脚本加载失败" :sub-title="describeApiError(scriptQuery.error.value)">
      <template #extra>
        <el-button @click="scriptQuery.refetch()">重试</el-button>
        <el-button type="primary" @click="router.push('/scripts')">返回脚本列表</el-button>
      </template>
    </el-result>
  </div>

  <div v-else-if="!draft" class="page">
    <el-skeleton :rows="10" animated />
  </div>

  <div v-else class="page-fill">
    <PageHead
      :title="draft.name || '脚本编辑器'"
      :subtitle="`${stepCount} 个节点 · ${id}`"
      @back="router.push('/scripts')"
    >
      <template #back><el-icon><ArrowLeft /></el-icon></template>
      <template #title-extra>
        <StatusTag :tone="dirty ? 'amber' : 'green'">{{ dirty ? '未保存' : '已保存' }}</StatusTag>
      </template>
      <template #actions>
        <el-button :icon="Check" :loading="validateMutation.isPending.value" @click="handleValidate">校验</el-button>
        <el-button type="primary" :icon="Refresh" :loading="updateMutation.isPending.value" :disabled="!dirty && !jsonDirty" @click="handleSave">
          保存
        </el-button>
        <el-button :icon="VideoPlay" @click="goRun">运行</el-button>
        <el-button :icon="Document" @click="jsonOpen = true">JSON</el-button>
      </template>
    </PageHead>

    <div class="editor-grid">
      <NodeList
        :steps="draft.steps"
        :selected-id="selectedId"
        @select="selectedId = $event"
        @add="addNode"
        @reorder="reorderNode"
        @delete="deleteNode"
        @toggle-enabled="toggleNodeEnabled"
      />

      <div class="editor-main">
        <div class="card editor-card">
          <el-collapse v-model="settingsOpen">
            <el-collapse-item name="settings">
              <template #title>
                脚本设置
                <span class="hint" style="margin-left: 6px">名称 / 变量 / 默认超时 / 失败策略 / logcat</span>
              </template>
              <ScriptSettingsForm :data="draft" @change="applyDraft" />
            </el-collapse-item>
          </el-collapse>
        </div>

        <div class="card editor-card">
          <div class="card-body">
            <NodeForm
              v-if="selectedNode"
              :node="selectedNode"
              :index="selectedIndex"
              :total="stepCount"
              :steps="draft.steps"
              @change="patchNode"
            />
            <el-empty v-else description="选择左侧节点进行编辑，或新增一个节点" :image-size="72" />
          </div>
        </div>
      </div>

    </div>

    <el-drawer v-model="jsonOpen" title="JSON 源码" :size="jsonDrawerSize" append-to-body class="json-drawer">
      <div class="json-drawer-content">
        <JsonSourcePanel
          :text="jsonText"
          :dirty="jsonDirty"
          :parse-error="jsonError"
          :validation="validation"
          @update:text="handleJsonChange"
          @format="handleFormat"
          @apply="handleApplyJson"
          @jump-path="jumpToPath"
        />
      </div>
    </el-drawer>
  </div>
</template>
