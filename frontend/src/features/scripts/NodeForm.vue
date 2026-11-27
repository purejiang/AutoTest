<script setup lang="ts">
import { computed, ref } from 'vue'
import { Delete } from '@element-plus/icons-vue'

import type { OnFail, Retry, StepNode } from '@/types/api'
import { useScriptVariables } from '@/composables/useScriptVariables'
import { numberOrNull } from '@/lib/format'
import { collectUsedVariables, missingVariables } from '@/lib/scriptVars'

import { describeFailureFlow } from './lib/failure'
import { editRetry, resolveRetry, toggleRetry } from './lib/retry'
import { NODE_TYPE_META, RERUN_SAFETY_OPTIONS } from './nodeDefaults'
import ParamsEditor from './ParamsEditor.vue'
import WhenEditor from './WhenEditor.vue'
import HelpTip from '@/components/HelpTip.vue'
import Notice from '@/components/Notice.vue'
import StatusTag from '@/components/StatusTag.vue'

const props = withDefaults(
  defineProps<{
    node: StepNode
    total: number
    steps: StepNode[]
    defaultOnFail: 'abort' | 'continue'
    defaultTimeoutMs: number
    defaultDelayBeforeMs: number
    defaultDelayAfterMs: number
    defaultRetry: Retry | null
    /** 脚本设置里的失败截图主开关；关闭时节点开关不生效。 */
    scriptScreenshotOnFail: boolean
  }>(),
  { scriptScreenshotOnFail: true },
)

const emit = defineEmits<{
  change: [node: StepNode]
  delete: []
}>()

const advancedOpen = ref<string[]>([])
const meta = computed(() => NODE_TYPE_META[props.node.type])
const otherSteps = computed(() => props.steps.filter((step) => step.id !== props.node.id))

// 当前节点 params/when 里用到、但脚本 variables 没声明的变量：运行时会原样保留，提前提示。
const scriptVariables = useScriptVariables()
const nodeUsedVariables = computed(() =>
  collectUsedVariables({ params: props.node.params, when: props.node.when }),
)
const nodeMissingVariables = computed(() =>
  scriptVariables ? missingVariables(scriptVariables.variables.value, nodeUsedVariables.value) : [],
)

function declareNodeVariable(name: string): void {
  scriptVariables?.addVariable(name)
}
const onFailAction = computed(() => props.node.on_fail?.action ?? 'none')

const onFailOptions = computed(() => {
  const inherited = props.defaultOnFail === 'abort' ? '中止整个运行' : '继续下一步'
  return [
    { value: 'none', label: `继承脚本默认（${inherited}）` },
    { value: 'abort', label: '中止整个运行（abort）' },
    { value: 'continue', label: '继续下一步（continue）' },
    ...(otherSteps.value.length > 0 || onFailAction.value === 'goto'
      ? [{ value: 'goto', label: '跳转到节点（高级）' }]
      : []),
  ]
})

const gotoStep = computed(
  () => otherSteps.value.find((step) => step.id === props.node.on_fail?.target_id) ?? null,
)
const gotoLabel = computed(() =>
  gotoStep.value ? `${gotoStep.value.id} · ${gotoStep.value.name || gotoStep.value.type}` : null,
)

const retryState = computed(() => resolveRetry(props.node.retry, props.defaultRetry))
const retryHint = computed(() => {
  if (retryState.value.mode === 'inherit') {
    return '继承脚本默认；改次数/间隔即自定义。'
  }
  if (retryState.value.mode === 'custom' && props.defaultRetry) return '优先于脚本默认。'
  if (retryState.value.mode === 'off' && retryState.value.overridden && props.defaultRetry) {
    return '已显式关闭。'
  }
  return ''
})

const screenshotEffective = computed(
  () => props.node.screenshot_on_fail && props.scriptScreenshotOnFail,
)

/** wait 节点自身的等待时长（sleep 的 ms / until 的 timeout_ms）；非 wait 返回 null。 */
const waitDurationMs = computed<number | null>(() => {
  if (props.node.type !== 'wait') return null
  const params = props.node.params as { mode?: string; ms?: number; timeout_ms?: number }
  // 与 UI/后端 schema 默认值保持一致：until 默认 5000ms，sleep 默认 1000ms。
  const duration = params.mode === 'sleep' ? (params.ms ?? 1000) : (params.timeout_ms ?? 5000)
  if (typeof duration !== 'number' || !Number.isFinite(duration) || duration <= 0) return null
  return Math.round(duration)
})

/** 继承节点超时时，引擎会把 wait 的等待时长 + 2s 作为下限。 */
const autoNodeTimeoutMs = computed<number | null>(() => {
  if (waitDurationMs.value === null) return null
  return Math.min(waitDurationMs.value + 2000, 600000)
})

const nodeTimeoutPlaceholder = computed(() => {
  if (autoNodeTimeoutMs.value !== null) return `自动 ${autoNodeTimeoutMs.value} ms（等待 + 2s）`
  return `默认 ${props.defaultTimeoutMs} ms`
})

/** 显式节点超时小于「等待 + 2s」时会被提前截断，给出可操作的提醒。 */
const waitTimeoutCut = computed<{ node: number; needed: number; wait: number } | null>(() => {
  if (waitDurationMs.value === null || props.node.timeout_ms == null) return null
  const needed = Math.min(waitDurationMs.value + 2000, 600000)
  const node = Number(props.node.timeout_ms)
  if (!Number.isFinite(node) || node >= needed) return null
  return { node, needed, wait: waitDurationMs.value }
})

const flow = computed(() =>
  describeFailureFlow({
    retry: retryState.value.effective,
    retryInherited: retryState.value.mode === 'inherit',
    onFail: onFailAction.value === 'none' ? props.defaultOnFail : onFailAction.value,
    onFailInherited: onFailAction.value === 'none',
    gotoLabel: gotoLabel.value,
    screenshot: screenshotEffective.value,
    screenshotBlocked: !props.scriptScreenshotOnFail,
  }),
)

function patch(partial: Partial<StepNode>): void {
  emit('change', { ...props.node, ...partial })
}

function setRetryEnabled(enabled: boolean): void {
  patch({ retry: toggleRetry(enabled, props.node.retry, props.defaultRetry) })
}

function setRetryCount(value: number | null | undefined): void {
  patch({ retry: editRetry('count', numberOrNull(value) ?? 0, props.node.retry, props.defaultRetry) })
}

function setRetryInterval(value: number | null | undefined): void {
  patch({
    retry: editRetry('interval_ms', numberOrNull(value) ?? 0, props.node.retry, props.defaultRetry),
  })
}

function restoreRetry(): void {
  patch({ retry: null })
}

function setOnFail(value: string): void {
  if (value === 'none') {
    patch({ on_fail: null })
  } else if (value === 'goto') {
    const fallback: OnFail = { action: 'goto', target_id: otherSteps.value[0]?.id ?? '' }
    patch({ on_fail: fallback })
  } else {
    patch({ on_fail: { action: value as 'abort' | 'continue' } })
  }
}

function setWhen(value: StepNode['when']): void {
  patch({ when: value })
}

/** 节点停顿留空时继承脚本默认；占位符直接显示会生效的值。 */
function delayPlaceholder(defaultValue: number): string {
  return defaultValue > 0 ? `默认 ${defaultValue} ms` : '默认：不停顿'
}
</script>

<template>
  <div class="node-editor">
    <div class="node-editor-head">
      <div class="node-editor-meta">
        <StatusTag :tone="meta.tone">{{ meta.icon }} {{ meta.label }}</StatusTag>
        <el-input
          class="node-name-input"
          :model-value="node.name"
          size="small"
          placeholder="节点名称"
          aria-label="节点名称"
          @update:model-value="patch({ name: $event })"
        />
        <StatusTag v-if="node.when" tone="cyan">带条件</StatusTag>
      </div>
      <div class="node-head-actions">
        <span class="hint">启用</span>
        <el-switch
          :model-value="node.enabled"
          size="small"
          aria-label="启用/禁用节点"
          @update:model-value="patch({ enabled: Boolean($event) })"
        />
        <span class="node-head-divider" />
        <el-button
          class="node-delete"
          text
          type="danger"
          :icon="Delete"
          :disabled="total <= 1"
          title="删除节点"
          @click="emit('delete')"
        >
          删除
        </el-button>
      </div>
    </div>

    <el-form class="node-editor-form" label-position="top" size="small" @submit.prevent>
      <div class="section-label">动作参数</div>
      <ParamsEditor :type="node.type" :params="node.params" @change="patch({ params: $event })" />
      <Notice
        v-if="nodeMissingVariables.length > 0"
        class="node-var-warning"
        severity="warning"
        title="当前节点使用了未声明的脚本变量"
      >
        <div class="row wrap node-var-warning-body">
          <span class="hint">
            运行时若没有覆盖，<code class="code-text" v-pre>{{var}}</code> 会原样传给设备
          </span>
          <el-button
            v-for="name in nodeMissingVariables"
            :key="name"
            size="small"
            text
            type="primary"
            @click="declareNodeVariable(name)"
          >
            {{ name }} · 声明
          </el-button>
        </div>
      </Notice>

      <div class="form-grid" style="margin-top: 6px">
        <el-form-item label="描述" class="full">
          <el-input
            :model-value="node.description"
            type="textarea"
            :rows="1"
            autosize
            placeholder="这一步做什么（可留空）"
            @update:model-value="patch({ description: $event })"
          />
        </el-form-item>
      </div>

      <div class="section-label">
        执行条件 when
        <span class="tag gray">可选</span>
        <span class="hint">不满足时 require=失败 / optional=跳过</span>
      </div>
      <WhenEditor :model-value="node.when" @update:model-value="setWhen" />

      <div class="section-label">执行</div>
      <div class="form-grid">
        <el-form-item>
          <template #label>
            节点超时 (ms)
            <HelpTip text="整个节点的硬上限（含动作耗时）。留空时 wait 节点自动 = 等待超时 + 2s；显式设置更小会截断等待。" />
          </template>
          <el-input-number
            :model-value="node.timeout_ms ?? undefined"
            :min="100"
            :max="600000"
            :placeholder="nodeTimeoutPlaceholder"
            controls-position="right"
            style="width: 100%"
            @update:model-value="patch({ timeout_ms: numberOrNull($event) })"
          />
          <Notice
            v-if="waitTimeoutCut"
            severity="warning"
            variant="compact"
            style="margin-top: 6px"
          >
            节点超时 {{ waitTimeoutCut.node }}ms 小于等待超时 + 2s（{{ waitTimeoutCut.needed }}ms），
            等待可能被提前截断；建议留空或调到 ≥ {{ waitTimeoutCut.needed }}ms。
          </Notice>
        </el-form-item>
      </div>

      <div class="section-label">失败处理</div>
      <div class="failure-flow" role="note" :title="flow.summary">
        <span class="failure-flow-arrow" aria-hidden="true">↳</span>
        <span>{{ flow.summary }}</span>
      </div>
      <div class="form-grid">
        <el-form-item class="full">
          <template #label>
            重试
            <HelpTip text="1 = 额外重试 1 次（最多执行 2 次）；条件不满足也会直接进入失败策略。" />
          </template>
          <div class="retry-row">
            <el-switch
              data-test="node-retry"
              :model-value="retryState.enabled"
              size="small"
              aria-label="启用/关闭节点重试"
              @update:model-value="setRetryEnabled(Boolean($event))"
            />
            <template v-if="retryState.enabled">
              <el-input-number
                data-test="retry-count"
                :model-value="retryState.effective?.count ?? 1"
                size="small"
                :min="0"
                :max="20"
                controls-position="right"
                style="width: 108px"
                @update:model-value="setRetryCount"
              >
                <template #prefix>次数</template>
              </el-input-number>
              <el-input-number
                data-test="retry-interval"
                :model-value="retryState.effective?.interval_ms ?? 1000"
                size="small"
                :min="0"
                :max="60000"
                controls-position="right"
                style="width: 136px"
                @update:model-value="setRetryInterval"
              >
                <template #prefix>间隔</template>
              </el-input-number>
            </template>
            <StatusTag v-if="retryState.mode === 'inherit'" tone="gray">继承脚本默认</StatusTag>
            <StatusTag v-else-if="retryState.mode === 'custom'" tone="blue">自定义</StatusTag>
            <StatusTag
              v-else-if="retryState.overridden && defaultRetry"
              tone="gray"
            >已覆盖：不重试</StatusTag>
            <span v-else class="hint">不重试</span>
            <el-button
              v-if="node.retry !== null && defaultRetry"
              data-test="retry-restore"
              text
              size="small"
              @click="restoreRetry"
            >恢复继承</el-button>
          </div>
          <div v-if="retryHint" class="hint" style="margin-top: 4px">{{ retryHint }}</div>
          <div v-if="retryState.enabled" class="hint" style="margin-top: 2px">额外次数，1 = 最多 2 次；断言失败不重试。</div>
        </el-form-item>
        <el-form-item class="full">
          <template #label>
            失败策略（重试用尽后）
            <HelpTip text="跳转是高级动作：失败后跳到目标节点，重入会重置目标及之后节点的重试计数；一般保持中止/继续即可。" />
          </template>
          <div class="row wrap">
            <el-select
              data-test="on-fail"
              :model-value="onFailAction"
              style="width: 220px"
              @update:model-value="setOnFail($event)"
            >
              <el-option
                v-for="option in onFailOptions"
                :key="option.value"
                :value="option.value"
                :label="option.label"
              />
            </el-select>
            <el-select
              v-if="onFailAction === 'goto' && otherSteps.length > 0"
              data-test="goto-target"
              :model-value="node.on_fail?.target_id ?? ''"
              placeholder="选择跳转目标节点"
              style="width: 220px"
              @update:model-value="patch({ on_fail: { action: 'goto', target_id: $event } })"
            >
              <el-option
                v-for="step in otherSteps"
                :key="step.id"
                :value="step.id"
                :label="`${step.id} · ${step.name || step.type}`"
              />
            </el-select>
          </div>
          <div v-if="onFailAction === 'goto'" class="hint" style="margin-top: 4px">
            失败后跳到目标节点；重入会重置后续重试计数。
          </div>
        </el-form-item>
        <el-form-item label="失败时截图" class="full">
          <div class="row wrap">
            <el-switch
              data-test="node-screenshot"
              :model-value="screenshotEffective"
              :disabled="!scriptScreenshotOnFail"
              size="small"
              aria-label="失败时是否截图"
              @update:model-value="patch({ screenshot_on_fail: Boolean($event) })"
            />
            <span v-if="!scriptScreenshotOnFail" class="hint">
              脚本已关闭失败截图，此开关不生效。
            </span>
            <span v-else class="hint">{{ screenshotEffective ? '失败时保存截图' : '该节点不截图' }}</span>
          </div>
        </el-form-item>
      </div>

      <el-collapse v-model="advancedOpen" style="margin-top: 8px">
        <el-collapse-item name="advanced">
          <template #title>
            高级 / 元数据
            <span class="hint" style="margin-left: 6px">重跑安全性、标签、执行节奏</span>
          </template>
          <div class="form-grid">
            <el-form-item label="节点 ID" class="full">
              <code class="code-text">{{ node.id }}</code>
            </el-form-item>
            <el-form-item>
              <template #label>
                重跑安全性
                <HelpTip text="safe 可安全重跑；stateful 依赖运行状态；destructive 会清数据，恢复时必须确认。" />
              </template>
              <el-select
                :model-value="node.rerun_safety"
                style="width: 100%"
                @update:model-value="patch({ rerun_safety: $event })"
              >
                <el-option
                  v-for="option in RERUN_SAFETY_OPTIONS"
                  :key="option.value"
                  :value="option.value"
                  :label="option.label"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="标签">
              <el-select
                :model-value="node.tags"
                multiple
                filterable
                allow-create
                default-first-option
                :reserve-keyword="false"
                placeholder="回车添加标签"
                style="width: 100%"
                @update:model-value="patch({ tags: $event as string[] })"
              />
            </el-form-item>
            <el-form-item label="执行前停顿（ms）">
              <div style="width: 100%">
                <el-input-number
                  :model-value="node.delay_before_ms || undefined"
                  :min="0"
                  :max="600000"
                  :placeholder="delayPlaceholder(defaultDelayBeforeMs)"
                  controls-position="right"
                  style="width: 100%"
                  @update:model-value="patch({ delay_before_ms: numberOrNull($event) ?? 0 })"
                />
              </div>
            </el-form-item>
            <el-form-item label="执行后停顿（ms）">
              <div style="width: 100%">
                <el-input-number
                  :model-value="node.delay_after_ms || undefined"
                  :min="0"
                  :max="600000"
                  :placeholder="delayPlaceholder(defaultDelayAfterMs)"
                  controls-position="right"
                  style="width: 100%"
                  @update:model-value="patch({ delay_after_ms: numberOrNull($event) ?? 0 })"
                />
              </div>
            </el-form-item>
          </div>
          <div class="hint">
            留空继承脚本默认；大于 0 即节点自定义。
            <HelpTip text="真正等待界面变化时用 wait 节点或执行条件。" />
          </div>
        </el-collapse-item>
      </el-collapse>
    </el-form>
  </div>
</template>

<style scoped>
.node-head-actions .node-delete {
  width: auto;
  padding: 0 6px;
}

.node-var-warning {
  margin: 0 0 8px;
}

.node-var-warning-body {
  gap: 4px;
  margin-top: 3px;
}


</style>
