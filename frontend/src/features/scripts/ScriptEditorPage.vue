<script setup lang="ts">
import { ArrowLeft, Check, Close, Document, EditPen, Plus, Refresh, Setting, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useWindowSize } from '@vueuse/core'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'

import { describeApiError, validationErrorsOf } from '@/api/client'
import { useScript, useUpdateScript, useValidateScriptData } from '@/api/scripts'
import type { NodeType, Script, ScriptData, ScriptValidation, StepNode } from '@/types/api'
import {
  formatRelative,
  formatShortDateTime,
  prettyJson,
  runStatusMeta,
  safeParseJson,
  sourceMeta,
} from '@/lib/format'
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
const settingsDrawerOpen = ref(false)
const jsonOpen = ref(false)
const validationOpen = ref(false)
const loadedId = ref<string | null>(null)
const nodeListRef = ref<{ clearSearch: () => void } | null>(null)
const nodeScrollRef = ref<HTMLElement | null>(null)
const editingName = ref(false)
const nameDraft = ref('')
const nameBeforeEdit = ref('')
const nameInputRef = ref<{ focus: () => void } | null>(null)

const { width: windowWidth } = useWindowSize()
const jsonDrawerSize = computed(() => (windowWidth.value <= 900 ? '100%' : '560px'))
const settingsDrawerSize = computed(() => (windowWidth.value <= 900 ? '100%' : '520px'))

const hasUnsavedChanges = computed(() => dirty.value || jsonDirty.value)
const script = computed<Script | null>(() => scriptQuery.data.value ?? null)
const source = computed(() => sourceMeta(script.value?.source))

function loadFromScript(nextScript: Script): void {
  if (loadedId.value === nextScript.id) return
  loadedId.value = nextScript.id
  const data = cloneScriptData(normalizeScriptData(nextScript))
  draft.value = data
  selectedId.value = data.steps[0]?.id ?? null
  jsonText.value = prettyJson(data)
  jsonDirty.value = false
  jsonError.value = null
  dirty.value = false
  validation.value = null
  validationOpen.value = false
  settingsDrawerOpen.value = false
  jsonOpen.value = false
  editingName.value = false
}

watch(
  () => scriptQuery.data.value,
  (nextScript) => {
    if (nextScript) loadFromScript(nextScript)
  },
  { immediate: true },
)

watch(selectedId, async () => {
  await nextTick()
  nodeScrollRef.value?.scrollTo({ top: 0 })
})

const selectedIndex = computed(() => {
  if (!draft.value || !selectedId.value) return -1
  return draft.value.steps.findIndex((step) => step.id === selectedId.value)
})

const selectedNode = computed(() => {
  const data = draft.value
  return data && selectedIndex.value >= 0 ? data.steps[selectedIndex.value] : null
})

const stepCount = computed(() => draft.value?.steps.length ?? 0)

const subtitle = computed(() => {
  const parts = [`${stepCount.value} 个节点`]
  const updated = script.value?.updated_at
  if (updated) parts.push(`更新于 ${formatShortDateTime(updated)}`)
  return parts.join(' · ')
})

const stepIssues = computed<Record<string, { errors: number; warnings: number }>>(() => {
  const result: Record<string, { errors: number; warnings: number }> = {}
  const data = draft.value
  if (!data || !validation.value) return result
  const add = (path: string, key: 'errors' | 'warnings'): void => {
    const match = path.match(/^(?:script\.)?steps(?:\[|\.)(\d+)/)
    if (!match) return
    const node = data.steps[Number(match[1])]
    if (!node) return
    const issue = (result[node.id] ??= { errors: 0, warnings: 0 })
    issue[key] += 1
  }
  validation.value.errors.forEach((item) => add(item.path, 'errors'))
  validation.value.warnings.forEach((item) => add(item.path, 'warnings'))
  return result
})

function startEditName(): void {
  if (!draft.value) return
  nameBeforeEdit.value = draft.value.name
  nameDraft.value = draft.value.name
  editingName.value = true
  void nextTick(() => nameInputRef.value?.focus())
}

function commitName(): void {
  if (!editingName.value || !draft.value) return
  const name = nameDraft.value.trim()
  if (!name) {
    ElMessage.warning('脚本名称不能为空')
    nameDraft.value = draft.value.name
    editingName.value = false
    return
  }
  editingName.value = false
  if (name !== nameBeforeEdit.value) {
    applyDraft({ ...draft.value, name })
  }
}

function cancelName(): void {
  editingName.value = false
  nameDraft.value = nameBeforeEdit.value
}

function openRun(runId: number): void {
  void router.push(`/runs/${runId}`)
}

function openLastRun(): void {
  const run = script.value?.last_run
  if (run) openRun(run.id)
}

function openOriginRecording(): void {
  const recordingId = script.value?.origin_recording_id
  if (recordingId) void router.push(`/recorder/${recordingId}`)
}

function applyDraft(next: ScriptData): void {
  draft.value = next
  dirty.value = true
  validation.value = null
  validationOpen.value = false
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

function nextNodeId(existingIds: string[]): string {
  const used = new Set(existingIds)
  let index = existingIds.length + 1
  while (used.has(`s${index}`)) index += 1
  return `s${index}`
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
  nodeListRef.value?.clearSearch()
  selectedId.value = step.id
}

function duplicateNode(nodeId: string): void {
  const data = draft.value
  if (!data) return
  const index = data.steps.findIndex((step) => step.id === nodeId)
  const sourceNode = data.steps[index]
  if (!sourceNode) return
  const copy = JSON.parse(JSON.stringify(sourceNode)) as StepNode
  copy.id = nextNodeId(data.steps.map((step) => step.id))
  copy.name = `${sourceNode.name || '节点'} 副本`
  const steps = [...data.steps]
  steps.splice(index + 1, 0, copy)
  applyDraft({ ...data, steps })
  nodeListRef.value?.clearSearch()
  selectedId.value = copy.id
  ElMessage.success('已复制节点（保存后生效）')
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

/** ⌘/Ctrl+S 保存；Alt+↑/↓ 移动当前节点（输入框内不拦截 Alt 移动）。 */
function handleGlobalKeydown(event: KeyboardEvent): void {
  if ((event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 's') {
    event.preventDefault()
    if (editingName.value) commitName()
    if (hasUnsavedChanges.value && !updateMutation.isPending.value) void handleSave()
    return
  }
  if (!event.altKey || event.ctrlKey || event.metaKey) return
  if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
  if (isEditableTarget(event.target)) return
  const nodeId = selectedId.value
  if (!nodeId || !draft.value) return
  event.preventDefault()
  moveNode(nodeId, event.key === 'ArrowUp' ? -1 : 1)
}

function handleBeforeUnload(event: BeforeUnloadEvent): void {
  if (!hasUnsavedChanges.value) return
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => {
  window.addEventListener('keydown', handleGlobalKeydown)
  window.addEventListener('beforeunload', handleBeforeUnload)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleGlobalKeydown)
  window.removeEventListener('beforeunload', handleBeforeUnload)
})

onBeforeRouteLeave(async () => {
  if (!hasUnsavedChanges.value) return true
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

async function handleRun(): Promise<void> {
  if (editingName.value) commitName()
  if (hasUnsavedChanges.value) {
    const saved = await handleSave()
    if (!saved) return
  }
  goRun()
}

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

function duplicateSelected(): void {
  if (selectedNode.value) duplicateNode(selectedNode.value.id)
}

function deleteSelected(): void {
  if (selectedNode.value) deleteNode(selectedNode.value.id)
}

function moveSelected(direction: -1 | 1): void {
  if (selectedNode.value) moveNode(selectedNode.value.id, direction)
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
  validationOpen.value = false
  if (!parsed.data.steps.some((step) => step.id === selectedId.value)) {
    selectedId.value = parsed.data.steps[0]?.id ?? null
  }
  ElMessage.success('已应用到表单')
}

function handleResetJson(): void {
  if (!draft.value) return
  jsonText.value = prettyJson(draft.value)
  jsonDirty.value = false
  jsonError.value = null
  validation.value = null
  validationOpen.value = false
  ElMessage.success('已恢复为表单内容')
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
    jsonOpen.value = true
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

async function handleSave(): Promise<boolean> {
  const data = effectiveData()
  if (!data) return false
  if (data.steps.length === 0) {
    ElMessage.warning('脚本至少需要一个节点')
    return false
  }
  try {
    await updateMutation.mutateAsync({ name: data.name, notes: data.notes, data })
    dirty.value = false
    jsonDirty.value = false
    validation.value = null
    validationOpen.value = false
    loadedId.value = id.value
    ElMessage.success('脚本已保存')
    return true
  } catch (error) {
    const errors = validationErrorsOf(error)
    if (errors.length > 0) {
      validation.value = { valid: false, errors, warnings: [] }
      validationOpen.value = true
      // 节点内错误：关掉设置抽屉，让用户直接看到可点击定位的校验条
      if (settingsDrawerOpen.value && errors.every((item) => item.path.startsWith('steps'))) {
        settingsDrawerOpen.value = false
      }
      ElMessage.error(`保存失败：${errors[0]?.message ?? '校验未通过'}`)
    }
    return false
  }
}

async function handleSaveFromSettings(): Promise<void> {
  if (await handleSave()) settingsDrawerOpen.value = false
}

function applyValidation(result: ScriptValidation): void {
  validation.value = result
  validationOpen.value = !result.valid || result.warnings.length > 0
  if (result.valid) {
    ElMessage.success(result.warnings.length ? `校验通过，有 ${result.warnings.length} 条警告` : '校验通过')
  } else {
    ElMessage.error(`校验未通过：${result.errors.length} 个错误`)
  }
}

async function handleValidate(): Promise<void> {
  const data = effectiveData()
  if (!data) return
  try {
    applyValidation(await validateMutation.mutateAsync({ data }))
  } catch (error) {
    const errors = validationErrorsOf(error)
    if (errors.length > 0) applyValidation({ valid: false, errors, warnings: [] })
  }
}

function jumpToPath(path: string): void {
  const data = draft.value
  if (!data) return
  const match = path.match(/^(?:script\.)?steps(?:\[|\.)(\d+)/)
  if (match) {
    const node = data.steps[Number(match[1])]
    if (node) {
      nodeListRef.value?.clearSearch()
      selectedId.value = node.id
      return
    }
  }
  if (path.startsWith('name') || path.startsWith('notes') || path.startsWith('settings') || path.startsWith('defaults') || path.startsWith('variables')) {
    settingsDrawerOpen.value = true
    ElMessage.info('该问题在「脚本设置」中')
  }
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

  <div v-else class="page-fill script-editor-page">
    <PageHead :title="draft.name || '脚本编辑器'" :subtitle="subtitle" @back="router.push('/scripts')">
      <template #back><el-icon><ArrowLeft /></el-icon></template>
      <template #title>
        <div class="script-title">
          <el-input
            v-if="editingName"
            ref="nameInputRef"
            v-model="nameDraft"
            class="script-name-input"
            size="small"
            maxlength="200"
            @keydown.enter.prevent="commitName"
            @keydown.esc.prevent="cancelName"
            @blur="commitName"
          />
          <template v-else>
            <h1 :title="draft.name || '脚本编辑器'" @dblclick="startEditName">
              {{ draft.name || '脚本编辑器' }}
            </h1>
            <button type="button" class="icon-btn name-edit" title="重命名脚本" @click="startEditName">
              <el-icon><EditPen /></el-icon>
            </button>
          </template>
        </div>
      </template>
      <template #title-extra>
        <StatusTag v-if="script" :tone="source.tone">{{ source.label }}</StatusTag>
        <StatusTag :tone="hasUnsavedChanges ? 'amber' : 'green'">
          {{ hasUnsavedChanges ? '未保存' : '已保存' }}
        </StatusTag>
        <button
          v-if="script?.last_run"
          type="button"
          class="last-run-chip"
          :title="`运行 #${script.last_run.id} · ${script.last_run.device_serial || '未知设备'}`"
          @click="openLastRun"
        >
          <StatusTag :tone="runStatusMeta(script.last_run.status).tone">
            {{ runStatusMeta(script.last_run.status).label }}
          </StatusTag>
          <span class="hint">
            {{ formatRelative(script.last_run.started_at ?? script.last_run.created_at) }}
          </span>
        </button>
        <StatusTag v-else tone="gray">未运行</StatusTag>
      </template>
      <template #actions>
        <el-button :icon="Check" :loading="validateMutation.isPending.value" @click="handleValidate">校验</el-button>
        <el-button :icon="Setting" @click="settingsDrawerOpen = true">设置</el-button>
        <el-button :icon="Document" @click="jsonOpen = true">JSON</el-button>
        <el-button
          :icon="VideoPlay"
          :disabled="stepCount === 0 || updateMutation.isPending.value"
          @click="handleRun"
        >
          {{ hasUnsavedChanges ? '保存并运行' : '运行' }}
        </el-button>
        <el-button
          type="primary"
          :icon="Refresh"
          :loading="updateMutation.isPending.value"
          :disabled="!hasUnsavedChanges"
          title="保存（⌘S / Ctrl+S）"
          @click="handleSave"
        >
          保存
        </el-button>
      </template>
    </PageHead>

    <div v-if="jsonDirty" class="card editor-json-warning">
      <StatusTag tone="amber">源码未应用</StatusTag>
      <span>JSON 源码有未应用的修改；保存时会以源码为准。</span>
      <span class="spacer" />
      <el-button size="small" @click="jsonOpen = true">查看</el-button>
      <el-button size="small" type="primary" plain @click="handleApplyJson">应用到表单</el-button>
      <el-button size="small" @click="handleResetJson">放弃源码改动</el-button>
    </div>

    <div v-if="validation && validationOpen" class="card editor-validation">
      <div class="editor-validation-head">
        <StatusTag :tone="!validation.valid ? 'red' : validation.warnings.length ? 'amber' : 'green'">
          {{ !validation.valid ? '未通过' : validation.warnings.length ? '有警告' : '通过' }}
        </StatusTag>
        <strong>脚本校验</strong>
        <span class="hint">错误 {{ validation.errors.length }} · 警告 {{ validation.warnings.length }}</span>
        <span class="spacer" />
        <el-button text :icon="Close" title="关闭校验结果" @click="validationOpen = false" />
      </div>
      <div class="editor-validation-body">
        <div
          v-for="(item, index) in validation.errors"
          :key="`error-${index}-${item.path}`"
          class="validation-item"
          :title="item.path ? `点击定位：${item.path}` : undefined"
          @click="jumpToPath(item.path)"
        >
          <StatusTag tone="red">错误</StatusTag>
          <span class="code-text">{{ item.path || 'script' }}</span>
          <span>{{ item.message }}</span>
        </div>
        <div
          v-for="(item, index) in validation.warnings"
          :key="`warning-${index}-${item.path}`"
          class="validation-item"
          :title="item.path ? `点击定位：${item.path}` : undefined"
          @click="jumpToPath(item.path)"
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

    <div class="editor-grid">
      <NodeList
        ref="nodeListRef"
        :steps="draft.steps"
        :selected-id="selectedId"
        :issues="stepIssues"
        @select="selectedId = $event"
        @add="addNode"
        @reorder="reorderNode"
        @delete="deleteNode"
        @toggle-enabled="toggleNodeEnabled"
      />

      <div class="editor-main">
        <div class="card editor-card node-card">
          <div ref="nodeScrollRef" class="card-body">
            <NodeForm
              v-if="selectedNode"
              :node="selectedNode"
              :index="selectedIndex"
              :total="stepCount"
              :steps="draft.steps"
              :default-on-fail="draft.settings.on_fail"
              :default-retry="draft.defaults.retry ?? draft.settings.retry"
              :default-timeout-ms="draft.settings.default_timeout_ms"
              @change="patchNode"
              @duplicate="duplicateSelected"
              @delete="deleteSelected"
              @move="moveSelected"
            />
            <el-empty v-else description="选择左侧节点进行编辑" :image-size="64">
              <el-button size="small" type="primary" plain :icon="Plus" @click="addNode('click')">
                新增点击节点
              </el-button>
            </el-empty>
          </div>
        </div>
      </div>
    </div>

    <el-drawer v-model="settingsDrawerOpen" title="脚本设置" :size="settingsDrawerSize" append-to-body class="settings-drawer">
      <ScriptSettingsForm :data="draft" @change="applyDraft" />
      <div class="script-meta">
        <div><span>脚本 ID</span><code class="code-text">{{ id }}</code></div>
        <div><span>来源</span><StatusTag :tone="source.tone">{{ source.label }}</StatusTag></div>
        <template v-if="script">
          <div v-if="script.origin_recording_id">
            <span>来源录制</span>
            <button type="button" class="cell-link code-text" @click="openOriginRecording">
              {{ script.origin_recording_id }}
            </button>
          </div>
          <div><span>Schema</span><span>v{{ script.schema_version }}</span></div>
          <div><span>创建</span><span>{{ formatShortDateTime(script.created_at) }}</span></div>
          <div><span>更新</span><span>{{ formatShortDateTime(script.updated_at) }}</span></div>
        </template>
      </div>
      <template #footer>
        <el-button @click="settingsDrawerOpen = false">关闭</el-button>
        <el-button
          type="primary"
          :loading="updateMutation.isPending.value"
          :disabled="!hasUnsavedChanges"
          @click="handleSaveFromSettings"
        >
          保存脚本
        </el-button>
      </template>
    </el-drawer>

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
          @reset="handleResetJson"
          @jump-path="jumpToPath"
        />
      </div>
    </el-drawer>
  </div>
</template>
