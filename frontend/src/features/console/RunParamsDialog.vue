<script setup lang="ts">
// 「运行参数」弹窗：按脚本传本次运行变量（不写死脚本默认值）。
// 预填顺序：脚本默认 ← 全局面板默认覆盖 ← 上次运行记住的覆盖；只把与脚本默认不同的项发给后端。
import { Delete } from '@element-plus/icons-vue'
import { computed, ref, watch } from 'vue'

import type { Script } from '@/types/api'
import {
  declaredVariables,
  collectUsedVariables,
  diffOverrides,
  loadRememberedRunVars,
  missingVariables,
  saveRememberedRunVars,
  validVariableName,
} from '@/lib/scriptVars'
import Notice from '@/components/Notice.vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    script: Script | null
    deviceSerial?: string | null
    /** 全局面板（默认覆盖）解析后的值，已包含在预填里。 */
    globalVariables?: Record<string, string>
    /** 父组件正在创建运行。 */
    starting?: boolean
  }>(),
  { deviceSerial: null, globalVariables: () => ({}), starting: false },
)

const emit = defineEmits<{ close: []; start: [variables: Record<string, string>] }>()

type RowSource = 'script' | 'global' | 'memory' | 'manual'

interface ParamRow {
  key: string
  name: string
  value: string
  source: RowSource
  declared: boolean
}

const rows = ref<ParamRow[]>([])
const jsonMode = ref(false)
const jsonText = ref('')
const jsonError = ref('')
const submitError = ref('')
let uid = 0

const defaults = computed<Record<string, string>>(() => declaredVariables(props.script?.data?.variables))
const usedVariables = computed<string[]>(() => collectUsedVariables(props.script?.data))
const missingRows = computed<string[]>(() => missingVariables(defaults.value, usedVariables.value))
// 运行参数只关心“脚本真正用到”的变量；声明了但没参与插值的变量不展示、也不允许覆盖。
const allowedNames = computed(() => new Set(usedVariables.value))
const relevantDefaults = computed<Record<string, string>>(() =>
  Object.fromEntries(Object.entries(defaults.value).filter(([name]) => allowedNames.value.has(name))),
)
const globalNames = computed<string[]>(() =>
  Object.keys(props.globalVariables ?? {}).filter((name) => allowedNames.value.has(name)),
)

const overridesCount = computed(() => {
  const values = jsonMode.value
    ? parseJsonValues(jsonText.value)
    : { ok: true as const, value: valuesFromRows() }
  if (!values.ok) return 0
  return Object.keys(diffOverrides(defaults.value, values.value)).length
})

function nextKey(): string {
  uid += 1
  return `param-${uid}`
}

function rebuild(): void {
  const scriptId = props.script?.id
  const remembered = scriptId ? loadRememberedRunVars(scriptId) : {}
  const next: ParamRow[] = []
  const index = new Map<string, ParamRow>()
  const push = (name: string, value: string, source: RowSource, declared: boolean): void => {
    const existing = index.get(name)
    if (existing) {
      existing.value = value
      existing.source = source
      return
    }
    const row: ParamRow = { key: nextKey(), name, value, source, declared }
    index.set(name, row)
    next.push(row)
  }

  for (const [name, value] of Object.entries(relevantDefaults.value)) push(name, value, 'script', true)
  for (const [name, value] of Object.entries(props.globalVariables ?? {})) {
    if (!allowedNames.value.has(name)) continue
    push(name, value, 'global', name in defaults.value)
  }
  for (const [name, value] of Object.entries(remembered)) {
    if (!allowedNames.value.has(name)) continue
    push(name, value, 'memory', name in defaults.value)
  }

  rows.value = next
  jsonMode.value = false
  jsonText.value = ''
  jsonError.value = ''
  submitError.value = ''
}

watch(
  () => [props.open, props.script?.id] as const,
  ([open]) => {
    if (open) rebuild()
  },
  { immediate: true },
)

function valuesFromRows(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const row of rows.value) {
    const name = row.name.trim()
    if (!name) continue
    out[name] = row.value
  }
  return out
}

function resetRow(row: ParamRow): void {
  row.value = defaults.value[row.name] ?? ''
  row.source = 'script'
}

function removeRow(row: ParamRow): void {
  rows.value = rows.value.filter((item) => item !== row)
}


function addMissing(name: string): void {
  if (rows.value.some((row) => row.name === name)) return
  rows.value = [...rows.value, { key: nextKey(), name, value: '', source: 'manual', declared: false }]
}

function resetToScriptDefaults(): void {
  if (props.script) saveRememberedRunVars(props.script.id, {})
  const next: ParamRow[] = Object.entries(relevantDefaults.value).map(([name, value]) => ({
    key: nextKey(),
    name,
    value,
    source: 'script',
    declared: true,
  }))
  rows.value = next
  jsonMode.value = false
  jsonText.value = ''
  jsonError.value = ''
  submitError.value = ''
}

// ---------------------------------------------------------------- JSON 视图

interface ParseOk {
  ok: true
  value: Record<string, string>
}
interface ParseFail {
  ok: false
  error: string
}

function parseJsonValues(text: string): ParseOk | ParseFail {
  const trimmed = text.trim()
  if (!trimmed) return { ok: true, value: {} }
  let parsed: unknown
  try {
    parsed = JSON.parse(trimmed)
  } catch {
    return { ok: false, error: 'JSON 解析失败，请检查引号与逗号。' }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { ok: false, error: '必须是 JSON 对象，例如 {"package": "com.example.app"}' }
  }
  const value: Record<string, string> = {}
  for (const [key, item] of Object.entries(parsed as Record<string, unknown>)) {
    if (item === null || item === undefined) value[key] = ''
    else if (typeof item === 'string') value[key] = item
    else value[key] = String(item)
  }
  return { ok: true, value }
}

function setJsonMode(next: boolean): void {
  if (next) {
    jsonText.value = JSON.stringify(diffOverrides(defaults.value, valuesFromRows()), null, 2)
    jsonError.value = ''
    submitError.value = ''
    jsonMode.value = true
    return
  }
  const parsed = parseJsonValues(jsonText.value)
  if (!parsed.ok) {
    jsonError.value = parsed.error
    return
  }
  const unknown = unknownNames(parsed.value)
  if (unknown.length > 0) {
    jsonError.value = `以下变量未在脚本中声明或使用：${unknown.join('、')}`
    return
  }
  applyJsonValues(parsed.value)
  jsonMode.value = false
  jsonError.value = ''
}

function applyJsonValues(values: Record<string, string>): void {
  const byName = new Map<string, ParamRow>()
  for (const row of rows.value) byName.set(row.name, row)
  for (const [name, value] of Object.entries(values)) {
    const row = byName.get(name)
    if (row) {
      row.value = value
      if (row.source === 'script') row.source = 'manual'
      continue
    }
    byName.set(name, { key: nextKey(), name, value, source: 'manual', declared: name in defaults.value })
  }
  // JSON 里删掉的“额外变量”同步删除；脚本声明的变量保留默认值。
  const kept: ParamRow[] = []
  for (const row of rows.value) {
    if (row.declared || row.name in values) kept.push(row)
  }
  for (const row of byName.values()) {
    if (!kept.includes(row)) kept.push(row)
  }
  rows.value = kept
}

// ---------------------------------------------------------------- 提交

function unknownNames(values: Record<string, string>): string[] {
  return Object.keys(values).filter((name) => !allowedNames.value.has(name))
}

function start(): void {
  submitError.value = ''
  let values: Record<string, string>
  if (jsonMode.value) {
    const parsed = parseJsonValues(jsonText.value)
    if (!parsed.ok) {
      jsonError.value = parsed.error
      return
    }
    const unknown = unknownNames(parsed.value)
    if (unknown.length > 0) {
      jsonError.value = `以下变量未在脚本中声明或使用：${unknown.join('、')}`
      return
    }
    values = parsed.value
    jsonError.value = ''
  } else {
    const seen = new Set<string>()
    for (const row of rows.value) {
      const name = row.name.trim()
      if (!name) continue
      if (seen.has(name)) {
        submitError.value = `变量名重复：${name}`
        return
      }
      if (row.declared && !validVariableName(name)) {
        submitError.value = `变量名不合法：${name}（只允许字母/数字/下划线/点/短横线）`
        return
      }
      if (!row.declared && !validVariableName(name)) {
        submitError.value = `变量名不合法：${name}（只允许字母/数字/下划线/点/短横线）`
        return
      }
      if (!allowedNames.value.has(name)) {
        submitError.value = `变量「${name}」未在脚本中声明或使用，不需要传参`
        return
      }
      seen.add(name)
    }
    values = valuesFromRows()
  }

  const overrides = diffOverrides(defaults.value, values)
  if (props.script) saveRememberedRunVars(props.script.id, overrides)
  emit('start', overrides)
}
</script>

<template>
  <el-dialog
    :model-value="open"
    title="运行参数"
    width="660px"
    append-to-body
    class="run-params-dialog"
    @update:model-value="!$event && emit('close')"
  >
    <div class="param-head">
      <div class="param-script">
        <strong>{{ script?.name || script?.id || '—' }}</strong>
        <span class="hint">{{ script?.id }}</span>
      </div>
      <span class="hint">
        {{ deviceSerial ? `设备 ${deviceSerial}` : '未选择设备' }} · 只发送与脚本默认值不同的项
      </span>
    </div>

    <Notice
      v-if="globalNames.length > 0 && !jsonMode"
      class="param-alert"
      severity="info"
      :title="`已带入全局面板的默认覆盖：${globalNames.join('、')}`"
    />

    <Notice
      v-if="missingRows.length > 0 && !jsonMode"
      class="param-alert"
      severity="warning"
      title="脚本里用到但未声明的变量"
    >
      <div class="row wrap param-missing">
        <span class="hint"
          >运行时若没有值，<code v-pre>{{var}}</code> 会原样传给设备：</span
        >
        <el-button
          v-for="name in missingRows"
          :key="`miss-${name}`"
          size="small"
          text
          type="primary"
          @click="addMissing(name)"
        >
          + {{ name }}
        </el-button>
      </div>
    </Notice>

    <div class="param-toolbar">
      <span class="hint">本次运行变量（脚本默认值已填好，改动才会覆盖）</span>
      <span class="spacer" />
      <el-button size="small" text @click="resetToScriptDefaults">恢复脚本默认</el-button>
      <el-button size="small" :type="jsonMode ? 'primary' : 'default'" text @click="setJsonMode(!jsonMode)">
        {{ jsonMode ? '表单视图' : 'JSON 视图' }}
      </el-button>
    </div>

    <template v-if="jsonMode">
      <el-input
        v-model="jsonText"
        type="textarea"
        :rows="8"
        class="code-text param-json"
        placeholder='{ "package": "com.example.app" }'
        @update:model-value="jsonError = ''"
      />
      <div v-if="jsonError" class="inline-error param-error">{{ jsonError }}</div>
      <div class="hint">只写要覆盖的项；留空 = {} 表示全部用脚本默认值。</div>
    </template>

    <template v-else>
      <div v-if="rows.length === 0" class="param-empty hint">
        {{
          missingRows.length > 0
            ? '脚本没有声明变量；可点上方未声明变量加入本次运行。'
            : '脚本没有用到变量，本次运行无需传参。'
        }}
      </div>
      <div class="param-list">
        <div v-for="row in rows" :key="row.key" class="param-row">
          <el-input
            v-if="!row.declared"
            v-model="row.name"
            size="small"
            placeholder="变量名"
            class="param-name"
            @update:model-value="submitError = ''"
          />
          <span v-else class="param-name code-text" :title="row.name">{{ row.name }}</span>
          <span class="param-eq">=</span>
          <el-input
            v-model="row.value"
            size="small"
            :placeholder="defaults[row.name] ?? '值'"
            class="param-value"
            @update:model-value="submitError = ''"
          />
          <el-button
            v-if="row.declared"
            text
            size="small"
            :disabled="row.value === (defaults[row.name] ?? '')"
            @click="resetRow(row)"
          >
            默认
          </el-button>
          <el-button v-else text size="small" :icon="Delete" title="删除" @click="removeRow(row)" />
          <span v-if="row.source === 'global'" class="param-tag">全局</span>
          <span v-else-if="row.source === 'memory'" class="param-tag">上次</span>
        </div>
      </div>
    </template>

    <div v-if="submitError" class="inline-error param-error">{{ submitError }}</div>

    <template #footer>
      <span class="param-foot-hint">{{
        overridesCount > 0 ? `将覆盖 ${overridesCount} 项` : '不覆盖任何变量'
      }}</span>
      <el-button @click="emit('close')">取消</el-button>
      <el-button type="primary" :loading="starting" @click="start">开始运行</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.param-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.param-script {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

.param-script strong {
  color: var(--ink);
  font-size: 12.5px;
}

.param-alert {
  margin-bottom: 8px;
}

.param-missing {
  gap: 2px;
  margin-top: 2px;
}

.param-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 4px 0 6px;
}

.param-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.param-row {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}

.param-name {
  flex: none;
  width: 34%;
  overflow: hidden;
  color: var(--ink);
  font-family: var(--mono);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.param-eq {
  flex: none;
  color: var(--muted-2);
  font-size: 11px;
}

.param-value {
  flex: 1;
  min-width: 0;
}

.param-tag {
  flex: none;
  padding: 0 4px;
  border-radius: 4px;
  background: var(--surface-3);
  color: var(--muted-2);
  font-size: 9px;
}

.param-add {
  margin-top: 7px;
}

.param-json {
  margin-bottom: 4px;
}

.param-empty {
  margin: 4px 0 2px;
  line-height: 1.5;
}

.param-error {
  margin-top: 6px;
}

.param-foot-hint {
  margin-right: auto;
  color: var(--muted-2);
  font-size: 10.5px;
}

:global(.run-params-dialog .el-dialog__footer) {
  display: flex;
  align-items: center;
  gap: 8px;
}

@media (max-width: 720px) {
  :global(.run-params-dialog) {
    max-width: 96vw;
  }
}
</style>
