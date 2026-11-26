<script setup lang="ts">
import { MagicStick, Refresh, Search, VideoPlay } from '@element-plus/icons-vue'
import { useDebounceFn } from '@vueuse/core'
import { useQueryClient } from '@tanstack/vue-query'
import { ElMessage } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { describeApiError, get } from '@/api/client'
import { isActiveRun, runKeys, useStartRun } from '@/api/runs'
import { useScripts } from '@/api/scripts'
import type { Device, Script } from '@/types/api'
import { runStatusMeta, safeParseJson } from '@/lib/format'
import {
  collectUsedVariables,
  declaredVariables,
  loadRememberedRunVars,
} from '@/lib/scriptVars'
import { useUiStore } from '@/stores/ui'
import StatusTag from '@/components/StatusTag.vue'

import RunMonitor from './RunMonitor.vue'
import RunParamsDialog from './RunParamsDialog.vue'

const props = defineProps<{
  serial: string | null
  device: Device | null
  /** 设备列表首次加载是否结束（避免加载中误报“没有设备”）。 */
  devicesReady?: boolean
}>()

const emit = defineEmits<{
  'running-change': [active: boolean, paused: boolean]
}>()

const router = useRouter()
const ui = useUiStore()
const queryClient = useQueryClient()

// ---------------------------------------------------------------- 脚本列表
const searchInput = ref('')
const search = ref('')
const applySearch = useDebounceFn((value: string) => {
  search.value = value.trim()
}, 250)

const scriptsQuery = useScripts(computed(() => ({ query: search.value, limit: 100, offset: 0 })))
const scripts = computed<Script[]>(() => scriptsQuery.data.value?.items ?? [])
const total = computed(() => scriptsQuery.data.value?.total ?? scripts.value.length)

function onSearchInput(value: string): void {
  void applySearch(value)
}

function clearSearch(): void {
  searchInput.value = ''
  void applySearch('')
}

function refreshScripts(): void {
  void scriptsQuery.refetch()
}

function openScript(script: Script): void {
  void router.push(`/scripts/${encodeURIComponent(script.id)}`)
}

// ---------------------------------------------------------------- 变量面板（默认覆盖）
const varsOpen = ref(false)
const variablesText = ref('')

interface VarsParseOk {
  ok: true
  value: Record<string, string>
}
interface VarsParseFail {
  ok: false
  error: string
}

/** 全局面板 JSON -> 默认覆盖值；留空 = 不覆盖。 */
function parseGlobalVariables(text: string): VarsParseOk | VarsParseFail {
  const trimmed = text.trim()
  if (!trimmed) return { ok: true, value: {} }
  const parsed = safeParseJson<unknown>(trimmed)
  if (!parsed.ok) return { ok: false, error: 'JSON 解析失败，请检查变量面板中的内容。' }
  const value = parsed.value
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return { ok: false, error: '变量必须是 JSON 对象，例如 {"user": "alice"}。' }
  }
  const result: Record<string, string> = {}
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    result[key] = typeof item === 'string' ? item : String(item)
  }
  return { ok: true, value: result }
}

const globalParse = computed(() => parseGlobalVariables(variablesText.value))
const globalVariables = computed<Record<string, string>>(() =>
  globalParse.value.ok ? globalParse.value.value : {},
)
const varsError = computed(() => (globalParse.value.ok ? '' : globalParse.value.error))

// ---------------------------------------------------------------- 运行参数弹窗
const paramsOpen = ref(false)
const paramsScript = ref<Script | null>(null)
const paramsStarting = ref(false)

/** 脚本有默认变量 / 用到 {{x}} / 有上次记住的覆盖 / 有全局默认覆盖 -> 先弹运行参数。 */
function needsRunParams(script: Script, global: Record<string, string>): boolean {
  const defaults = declaredVariables(script.data?.variables)
  const used = collectUsedVariables(script.data)
  const remembered = loadRememberedRunVars(script.id)
  return (
    Object.keys(defaults).length > 0 ||
    used.length > 0 ||
    Object.keys(remembered).length > 0 ||
    Object.keys(global).length > 0
  )
}

function openRunParams(script: Script): void {
  paramsScript.value = script
  paramsOpen.value = true
}

async function fetchScript(scriptId: string): Promise<Script | null> {
  try {
    return await get<Script>(`/scripts/${encodeURIComponent(scriptId)}`)
  } catch (error) {
    ElMessage.error(describeApiError(error))
    return null
  }
}

// ---------------------------------------------------------------- 运行
const startMutation = useStartRun()
const startingId = ref<string | null>(null)
const startingPending = ref(false)
const pendingWarnedId = ref<string | null>(null)

const runId = ref<number | null>(null)
const runScriptId = ref<string | null>(null)
const runStatus = ref('')
const monitorVisible = ref(false)

const runMeta = computed(() => runStatusMeta(runStatus.value))

function emitRunningState(status: string): void {
  emit('running-change', isActiveRun(status), status === 'paused')
}

function refreshRunData(): void {
  void scriptsQuery.refetch()
  void queryClient.invalidateQueries({ queryKey: runKeys.all })
}

async function startScript(scriptId: string, variables: Record<string, string>): Promise<boolean> {
  if (!props.serial) {
    ElMessage.warning('请先在控制台选择一台设备')
    return false
  }
  if (runId.value !== null && isActiveRun(runStatus.value)) {
    ElMessage.warning('已有运行进行中，请先停止或等待其结束。')
    monitorVisible.value = true
    return false
  }
  const hasVariables = Object.keys(variables).length > 0

  startingId.value = scriptId
  try {
    const run = await startMutation.mutateAsync({
      script_id: scriptId,
      serial: props.serial,
      variables: hasVariables ? variables : undefined,
    })
    runId.value = run.id
    runScriptId.value = scriptId
    runStatus.value = run.status || 'queued'
    monitorVisible.value = true
    emitRunningState(runStatus.value)
    ElMessage.success(`已创建运行 #${run.id}`)
    return true
  } catch {
    // 全局错误提示
    return false
  } finally {
    startingId.value = null
  }
}

/** 点「运行」：需要参数就弹窗，否则直接用当前默认覆盖启动。 */
async function requestRun(script: Script): Promise<boolean> {
  const parsed = globalParse.value
  if (!parsed.ok) {
    varsOpen.value = true
    ElMessage.error(parsed.error)
    return false
  }
  if (needsRunParams(script, parsed.value)) {
    openRunParams(script)
    return true
  }
  return startScript(script.id, parsed.value)
}

async function runScript(script: Script): Promise<void> {
  await requestRun(script)
}

async function startFromParams(overrides: Record<string, string>): Promise<void> {
  const script = paramsScript.value
  if (!script) return
  paramsStarting.value = true
  try {
    const started = await startScript(script.id, overrides)
    if (started) paramsOpen.value = false
  } finally {
    paramsStarting.value = false
  }
}

function handleRunStatusChange(status: string): void {
  runStatus.value = status
  emitRunningState(status)
}

function backToList(): void {
  monitorVisible.value = false
  refreshRunData()
}

/**
 * 脚本页/编辑器带来的待运行 id：有设备才启动；无设备时保留 pending，
 * 等用户在控制台选中设备、serial 变化后自动启动。需要参数的脚本会先弹运行参数。
 */
watch(
  () => [ui.pendingRunScriptId, props.serial, props.devicesReady] as const,
  async ([scriptId, serial, devicesReady]) => {
    if (!scriptId) {
      pendingWarnedId.value = null
      return
    }
    if (!serial) {
      // 设备列表还在加载：先等一等，加载结束后仍无设备再提示并保留 pending。
      if (!devicesReady) return
      if (pendingWarnedId.value !== scriptId) {
        pendingWarnedId.value = scriptId
        ElMessage.warning('待运行脚本已保留：选择设备后会自动开始。')
      }
      return
    }
    if (startingPending.value) return
    startingPending.value = true
    try {
      const script = scripts.value.find((item) => item.id === scriptId) ?? (await fetchScript(scriptId))
      ui.setPendingRunScriptId(null)
      if (!script) return
      await requestRun(script)
    } finally {
      startingPending.value = false
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="auto-panel">
    <div v-show="!monitorVisible" class="auto-list">
      <div class="auto-head">
        <h2>脚本</h2>
        <span class="count-pill">{{ total }}</span>
        <span class="spacer" />
        <el-button size="small" text :class="{ active: varsOpen }" @click="varsOpen = !varsOpen"
          >变量</el-button
        >
        <el-tooltip content="刷新脚本列表" placement="bottom">
          <el-button
            size="small"
            :icon="Refresh"
            circle
            :loading="scriptsQuery.isFetching.value"
            @click="refreshScripts"
          />
        </el-tooltip>
      </div>

      <el-input
        v-model="searchInput"
        class="auto-search"
        size="small"
        placeholder="搜索脚本"
        :prefix-icon="Search"
        clearable
        @input="onSearchInput"
        @clear="clearSearch"
      />

      <div v-if="varsOpen" class="vars-panel">
        <label class="vars-label">变量覆盖（JSON，可选）</label>
        <el-input
          v-model="variablesText"
          type="textarea"
          :rows="4"
          class="code-text"
          placeholder='{ "user": "alice" }'
        />
        <div class="hint">
          默认覆盖：对所有脚本生效；运行参数弹窗会基于它预填并允许逐次修改。留空表示不覆盖，值按字符串发送。
        </div>
        <div v-if="varsError" class="vars-error">{{ varsError }}</div>
      </div>

      <div v-if="runId !== null" class="run-reopen">
        <StatusTag :tone="runMeta.tone">{{ runMeta.label }}</StatusTag>
        <span class="run-reopen-text">运行 #{{ runId }}</span>
        <el-button size="small" text @click="monitorVisible = true">查看监控</el-button>
      </div>

      <el-alert
        v-if="scriptsQuery.isError.value"
        type="error"
        :closable="false"
        show-icon
        title="脚本列表加载失败"
        :description="describeApiError(scriptsQuery.error.value)"
      />

      <div v-else-if="scriptsQuery.isLoading.value" class="script-loading">
        <el-skeleton :rows="4" animated />
      </div>

      <div v-else class="script-list">
        <div v-for="script in scripts" :key="script.id" class="script-row">
          <button
            type="button"
            class="script-title"
            :title="script.name || script.id"
            @click="openScript(script)"
          >
            {{ script.name || script.id }}
          </button>
          <el-button
            class="run-params-btn"
            text
            size="small"
            title="带变量运行：打开运行参数"
            @click="openRunParams(script)"
          >
            参数
          </el-button>
          <el-button
            class="run-script"
            type="primary"
            size="small"
            :icon="VideoPlay"
            :loading="startingId === script.id"
            :disabled="startMutation.isPending.value || !serial"
            @click="runScript(script)"
          >
            运行
          </el-button>
        </div>
        <el-empty
          v-if="scripts.length === 0"
          :image-size="52"
          :description="search ? `没有匹配「${search}」的脚本` : '还没有脚本，去脚本页创建吧'"
        />
      </div>

      <div class="ai-card">
        <div class="ai-title">
          <el-icon><MagicStick /></el-icon>
          AI 操作
          <span class="tag violet">规划中</span>
        </div>
        <p>用自然语言描述目标，自动规划并执行；当前只预留入口，还没有接入后端能力。</p>
        <div class="ai-actions">
          <el-button size="small" disabled>描述任务生成脚本</el-button>
          <el-button size="small" disabled>AI 实时接管</el-button>
        </div>
      </div>

      <div class="hint auto-foot">
        点击脚本标题进入编辑器；点「运行」直接用当前参数开始，需要换值时用「参数」或运行前弹窗。
      </div>
    </div>

    <RunMonitor
      v-if="runId !== null"
      v-show="monitorVisible"
      :run-id="runId"
      :script-id="runScriptId"
      @back="backToList"
      @status-change="handleRunStatusChange"
    />

    <RunParamsDialog
      :open="paramsOpen"
      :script="paramsScript"
      :device-serial="serial"
      :global-variables="globalVariables"
      :starting="paramsStarting"
      @close="paramsOpen = false"
      @start="startFromParams"
    />
  </div>
</template>

<style scoped>
.auto-panel {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.auto-list {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow-y: auto;
  padding: 12px;
}

.auto-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 9px;
}

.auto-head h2 {
  margin: 0;
  color: var(--ink);
  font-size: 13px;
  font-weight: 650;
}

.auto-head .el-button.active {
  color: var(--accent);
  background: var(--accent-soft);
}

.auto-search {
  flex: none;
  margin-bottom: 6px;
}

.vars-panel {
  flex: none;
  margin-bottom: 8px;
  padding: 9px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface-2);
}

.vars-label {
  display: block;
  margin-bottom: 5px;
  color: var(--muted);
  font-size: 10.5px;
  font-weight: 500;
}

.vars-panel .hint {
  margin-top: 5px;
}

.vars-error {
  margin-top: 5px;
  color: var(--red);
  font-size: 10.5px;
}

.run-reopen {
  display: flex;
  align-items: center;
  gap: 7px;
  flex: none;
  margin-bottom: 7px;
  padding: 5px 7px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface-2);
}

.run-reopen-text {
  flex: 1;
  overflow: hidden;
  color: var(--muted);
  font-size: 10.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.script-loading {
  flex: none;
  padding: 4px 0;
}

.script-list {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 2px;
}

.script-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 39px;
  padding: 5px 7px;
  border-radius: 6px;
  background: transparent;
}

.script-row:hover {
  background: var(--surface-2);
}

.script-title {
  flex: 1;
  min-width: 0;
  padding: 0;
  overflow: hidden;
  border: 0;
  background: transparent;
  color: var(--ink);
  font-size: 12px;
  font-weight: 550;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.script-title:hover {
  color: var(--accent);
}

.run-script {
  flex: none;
}

.run-params-btn {
  flex: none;
  color: var(--muted-2);
}

.ai-card {
  flex: none;
  margin-top: 10px;
  padding: 11px;
  border: 1px dashed var(--line-2);
  border-radius: 8px;
  background: linear-gradient(135deg, var(--surface-2), var(--surface));
}

.ai-title {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--ink);
  font-size: 12px;
  font-weight: 600;
}

.ai-title .el-icon {
  color: var(--violet);
}

.ai-card p {
  margin: 5px 0 8px;
  color: var(--muted-2);
  font-size: 10.5px;
  line-height: 1.5;
}

.ai-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.ai-actions .el-button {
  opacity: 0.55;
}

.auto-foot {
  flex: none;
  margin-top: 10px;
}

@media (max-width: 640px) {
  .auto-list {
    padding: 9px;
  }
}
</style>
