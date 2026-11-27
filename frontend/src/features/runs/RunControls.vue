<script setup lang="ts">
import { CaretRight, CircleClose, RefreshLeft, VideoPause } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, ref } from 'vue'

import { useDebugRun, usePauseRun, useResumeCheck, useResumeRun, useStopRun, type ResumeCheckResult } from '@/api/runs'
import type { RunDetail, Script } from '@/types/api'
import { prettyJson, runStatusMeta } from '@/lib/format'
import Notice from '@/components/Notice.vue'

const props = defineProps<{
  run: RunDetail
  liveStatus: string
  script?: Script
}>()

const pauseMutation = usePauseRun(computed(() => props.run.id))
const resumeCheckMutation = useResumeCheck(computed(() => props.run.id))
const resumeMutation = useResumeRun(computed(() => props.run.id))
const stopMutation = useStopRun(computed(() => props.run.id))
const debugMutation = useDebugRun(computed(() => props.run.id))

const checkOpen = ref(false)
const checkResult = ref<ResumeCheckResult | null>(null)
const runFromStepId = ref<string | undefined>(
  props.run.steps.find((step) => step.status === 'failed')?.step_id,
)

const RUN_FROM_STATUSES = ['failed', 'stopped', 'interrupted', 'aborted', 'paused']
const status = computed(() => props.liveStatus || props.run.status)
const isPaused = computed(() => status.value === 'paused')
const isRunning = computed(() => status.value === 'running')

function safetyOf(stepId: string): string {
  return props.script?.data?.steps?.find((step) => step.id === stepId)?.rerun_safety ?? 'safe'
}

const stepOptions = computed(() =>
  props.run.steps.map((step, index) => ({
    value: step.step_id,
    label: `#${index + 1} ${step.step_name || step.step_id}（${step.step_type}${safetyOf(step.step_id) === 'destructive' ? ' · destructive' : ''}）`,
    disabled: safetyOf(step.step_id) === 'destructive',
  })),
)

const canRunFrom = computed(() => RUN_FROM_STATUSES.includes(status.value) && stepOptions.value.length > 0)

function pause(): void {
  pauseMutation.mutate(undefined, {
    onSuccess: () => ElMessage.success('已请求暂停'),
    onError: undefined,
  })
}

async function stop(): Promise<void> {
  try {
    await ElMessageBox.confirm('停止运行？正在执行的动作最多等待 5s 后强制中断。', '停止运行', {
      type: 'warning',
      confirmButtonText: '停止',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  stopMutation.mutate(undefined, { onSuccess: () => ElMessage.success('已请求停止') })
}

function debug(action: 'step' | 'continue'): void {
  debugMutation.mutate(
    { action },
    { onSuccess: () => ElMessage.success(action === 'step' ? '已请求单步执行' : '已请求继续执行') },
  )
}

async function openResumeCheck(): Promise<void> {
  try {
    const result = await resumeCheckMutation.mutateAsync()
    checkResult.value = result
    checkOpen.value = true
  } catch {
    // 全局错误提示
  }
}

async function confirmResume(): Promise<void> {
  try {
    await resumeMutation.mutateAsync({ ack: true })
    checkOpen.value = false
    ElMessage.success('已请求恢复运行')
  } catch {
    // 全局错误提示
  }
}

function runFrom(): void {
  if (!runFromStepId.value) {
    ElMessage.warning('请选择要重跑的节点')
    return
  }
  debugMutation.mutate(
    { action: 'run_from', step_id: runFromStepId.value },
    { onSuccess: () => ElMessage.success(`已请求从节点 ${runFromStepId.value} 重跑`) },
  )
}

const warnings = computed(() => {
  const raw = checkResult.value?.warnings
  return Array.isArray(raw) ? raw : []
})
</script>

<template>
  <div class="card">
    <div class="card-head">
      <h2>运行控制</h2>
      <span class="tag" :class="isPaused ? 'amber' : isRunning ? 'blue' : 'gray'">当前：{{ runStatusMeta(status).label }}</span>
    </div>
    <div class="card-body">
      <div class="row wrap">
        <el-button
          v-if="isRunning"
          :icon="VideoPause"
          :loading="pauseMutation.isPending.value"
          @click="pause"
        >
          暂停
        </el-button>
        <el-button
          v-if="isRunning"
          :icon="CaretRight"
          :loading="debugMutation.isPending.value"
          title="单步执行到下一节点前暂停（需后端 controls 支持）"
          @click="debug('step')"
        >
          单步执行
        </el-button>
        <template v-if="isPaused">
          <el-button
            type="primary"
            :icon="CaretRight"
            :loading="resumeCheckMutation.isPending.value"
            @click="openResumeCheck"
          >
            恢复（先检查）
          </el-button>
          <el-button
            :icon="CaretRight"
            :loading="debugMutation.isPending.value"
            @click="debug('continue')"
          >
            继续执行
          </el-button>
        </template>
        <el-button
          v-if="['running', 'queued', 'paused', 'stopping'].includes(status)"
          type="danger"
          :icon="CircleClose"
          :loading="stopMutation.isPending.value"
          @click="stop"
        >
          停止
        </el-button>
      </div>

      <Notice
        v-if="isPaused"
        severity="warning"
        title="运行已暂停（干预模式）"
        style="margin-top: 10px"
      >
        点击「恢复（先检查）」会请求 resume-check，确认无 warning 后 hot resume；也可以选择从某个节点重跑。
      </Notice>

      <div v-if="canRunFrom" style="margin-top: 10px">
        <div class="row wrap">
          <strong class="small">从节点重跑</strong>
          <el-select
            v-model="runFromStepId"
            filterable
            placeholder="选择起始节点（destructive 节点禁用）"
            style="min-width: 320px"
          >
            <el-option
              v-for="option in stepOptions"
              :key="option.value"
              :value="option.value"
              :label="option.label"
              :disabled="option.disabled"
            />
          </el-select>
          <el-button :icon="RefreshLeft" :loading="debugMutation.isPending.value" @click="runFrom">
            从该节点重跑
          </el-button>
        </div>
        <div class="hint" style="margin-top: 4px">
          run_from 会从指定节点继续执行；rerun_safety=destructive 的节点不允许作为重跑起点。
        </div>
      </div>

      <el-dialog v-model="checkOpen" title="恢复前检查（resume-check）" width="640px" append-to-body>
        <template v-if="checkResult">
          <div v-if="checkResult.resume_kind !== undefined || checkResult.kind !== undefined" style="margin-bottom: 8px">
            恢复方式：
            <span class="tag blue">{{ String(checkResult.resume_kind ?? checkResult.kind) }}</span>
          </div>
          <Notice
            v-if="warnings.length > 0"
            severity="warning"
            title="恢复前确认（resume-check）"
          >
            <ul style="margin: 0; padding-left: 18px">
              <li v-for="(warning, index) in warnings" :key="index">
                {{ typeof warning === 'string' ? warning : prettyJson(warning) }}
              </li>
            </ul>
          </Notice>
          <Notice v-else severity="success" title="检查通过，可以恢复（hot resume）" />
          <div v-if="checkResult.message" style="margin-top: 8px">{{ String(checkResult.message) }}</div>
          <pre class="pre-block" style="margin-top: 10px">{{ prettyJson(checkResult) }}</pre>
        </template>
        <template #footer>
          <el-button @click="checkOpen = false">取消</el-button>
          <el-button type="primary" :loading="resumeMutation.isPending.value" @click="confirmResume">确认恢复</el-button>
        </template>
      </el-dialog>
    </div>
  </div>
</template>
