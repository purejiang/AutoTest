<script setup lang="ts">
import { computed, ref } from 'vue'
import { Delete } from '@element-plus/icons-vue'

import type { OnFail, Retry, StepNode } from '@/types/api'
import { numberOrNull } from '@/lib/format'

import { NODE_TYPE_META } from './nodeDefaults'
import ParamsEditor from './ParamsEditor.vue'
import WhenEditor from './WhenEditor.vue'
import StatusTag from '@/components/StatusTag.vue'

const props = defineProps<{
  node: StepNode
  total: number
  steps: StepNode[]
  defaultOnFail: 'abort' | 'continue'
  defaultTimeoutMs: number
  defaultRetry: Retry | null
}>()

const emit = defineEmits<{
  change: [node: StepNode]
  delete: []
}>()

const advancedOpen = ref<string[]>([])
const meta = computed(() => NODE_TYPE_META[props.node.type])
const otherSteps = computed(() => props.steps.filter((step) => step.id !== props.node.id))
const onFailAction = computed(() => props.node.on_fail?.action ?? 'none')

const RERUN_SAFETY_OPTIONS = [
  { value: 'safe', label: 'safe（可安全重跑）' },
  { value: 'stateful', label: 'stateful（依赖状态）' },
  { value: 'destructive', label: 'destructive（破坏性，禁止重跑）' },
]
const ON_FAIL_OPTIONS = [
  { value: 'none', label: '继承/不设置' },
  { value: 'abort', label: '中止 abort' },
  { value: 'continue', label: '继续 continue' },
  { value: 'goto', label: '跳转 goto' },
]

function patch(partial: Partial<StepNode>): void {
  emit('change', { ...props.node, ...partial })
}

function toggleRetry(checked: boolean): void {
  patch({ retry: checked ? { count: 1, interval_ms: 1000 } : null })
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

      <div class="section-label">执行与容错</div>
      <div class="form-grid">
        <el-form-item label="节点超时 (ms)" class="hint">
          <el-input-number
            :model-value="node.timeout_ms ?? undefined"
            :min="100"
            :max="600000"
            :placeholder="`继承脚本默认（${defaultTimeoutMs} ms）`"
            controls-position="right"
            style="width: 100%"
            @update:model-value="patch({ timeout_ms: numberOrNull($event) })"
          />
        </el-form-item>
        <el-form-item label="失败截图">
          <el-switch
            :model-value="node.screenshot_on_fail"
            size="small"
            @update:model-value="patch({ screenshot_on_fail: Boolean($event) })"
          />
        </el-form-item>
        <el-form-item label="重试" class="full">
          <div class="row wrap">
            <el-switch
              :model-value="node.retry !== null"
              size="small"
              @update:model-value="toggleRetry(Boolean($event))"
            />
            <template v-if="node.retry">
              <el-input-number
                :model-value="node.retry.count"
                size="small"
                :min="0"
                :max="20"
                controls-position="right"
                style="width: 100px"
                @update:model-value="patch({ retry: { ...node.retry!, count: numberOrNull($event) ?? 0 } })"
              >
                <template #prefix>次数</template>
              </el-input-number>
              <el-input-number
                :model-value="node.retry.interval_ms"
                size="small"
                :min="0"
                :max="60000"
                controls-position="right"
                style="width: 130px"
                @update:model-value="patch({ retry: { ...node.retry!, interval_ms: numberOrNull($event) ?? 0 } })"
              >
                <template #prefix>间隔</template>
              </el-input-number>
            </template>
            <span v-else class="hint">
              {{ defaultRetry ? `继承脚本默认：${defaultRetry.count} 次 / ${defaultRetry.interval_ms} ms` : '不重试' }}
            </span>
          </div>
        </el-form-item>
        <el-form-item label="失败策略 on_fail" class="full">
          <div class="row wrap">
            <el-select :model-value="onFailAction" style="width: 160px" @update:model-value="setOnFail($event)">
              <el-option v-for="option in ON_FAIL_OPTIONS" :key="option.value" :value="option.value" :label="option.label" />
            </el-select>
            <el-select
              v-if="onFailAction === 'goto'"
              :model-value="node.on_fail?.target_id ?? ''"
              placeholder="选择 goto 目标节点"
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
          <div v-if="onFailAction === 'none'" class="hint" style="margin-top: 4px">
            继承脚本默认：{{ defaultOnFail === 'abort' ? '中止运行' : '继续下一步' }}
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
            <el-form-item label="重跑安全性">
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
              <el-input-number
                :model-value="node.delay_before_ms"
                :min="0"
                :max="600000"
                controls-position="right"
                style="width: 100%"
                @update:model-value="patch({ delay_before_ms: numberOrNull($event) ?? 0 })"
              />
            </el-form-item>
            <el-form-item label="执行后停顿（ms）">
              <el-input-number
                :model-value="node.delay_after_ms"
                :min="0"
                :max="600000"
                controls-position="right"
                style="width: 100%"
                @update:model-value="patch({ delay_after_ms: numberOrNull($event) ?? 0 })"
              />
            </el-form-item>
          </div>
          <div class="hint">
            停顿位于节点动作之外，只影响节奏；真正等待界面变化时使用 wait 节点或执行条件。
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
</style>
