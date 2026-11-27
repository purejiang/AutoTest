<script setup lang="ts">
import { computed } from 'vue'

import { useSettings } from '@/api/settings'
import type { ScriptData, ScriptSettings } from '@/types/api'
import { numberOrNull } from '@/lib/format'

import { describeFailureFlow } from './lib/failure'
import { editRetry, normalizeScriptRetry, scriptRetryOn } from './lib/retry'
import HelpTip from '@/components/HelpTip.vue'
import KeyValueEditor from './KeyValueEditor.vue'
import { RERUN_SAFETY_OPTIONS } from './nodeDefaults'

const props = defineProps<{ data: ScriptData }>()
const emit = defineEmits<{ change: [data: ScriptData] }>()

const settings = computed(() => props.data.settings)
const retryOn = computed(() => scriptRetryOn(settings.value.retry))

// 采集类开关受全局能力约束：设置里没开，脚本里不允许开（开关置灰）。
const settingsQuery = useSettings()
const effective = computed<Record<string, unknown>>(
  () => (settingsQuery.data.value?.effective ?? {}) as Record<string, unknown>,
)
const logcatAvailable = computed(() => effective.value['capture.logcat_auto_attach'] === true)
const networkAvailable = computed(() => effective.value['capture.network.enabled'] === true)

const flow = computed(() =>
  describeFailureFlow({
    retry: settings.value.retry,
    retryInherited: false,
    onFail: settings.value.on_fail,
    onFailInherited: false,
    screenshot: settings.value.screenshot_on_fail,
  }),
)

function patchData(partial: Partial<ScriptData>): void {
  emit('change', { ...props.data, ...partial })
}

function patchSettings(partial: Partial<ScriptSettings>): void {
  emit('change', { ...props.data, settings: { ...props.data.settings, ...partial } })
}

function toggleScriptRetry(checked: boolean): void {
  patchSettings({ retry: checked ? { count: 1, interval_ms: 1000 } : null })
}

function setRetryCount(value: number | null | undefined): void {
  const next = editRetry('count', numberOrNull(value) ?? 0, settings.value.retry, null)
  patchSettings({ retry: normalizeScriptRetry(next) })
}

function setRetryInterval(value: number | null | undefined): void {
  const next = editRetry('interval_ms', numberOrNull(value) ?? 0, settings.value.retry, null)
  patchSettings({ retry: normalizeScriptRetry(next) })
}
</script>

<template>
  <el-form class="script-settings-form" label-position="top" size="small" @submit.prevent>
    <div class="section-label">备注与变量</div>
    <div class="form-grid">
      <el-form-item label="备注 notes" class="full">
        <el-input
          :model-value="data.notes"
          type="textarea"
          :rows="2"
          autosize
          placeholder="脚本用途、前置条件等（可留空）"
          @update:model-value="patchData({ notes: $event })"
        />
      </el-form-item>
      <el-form-item label="脚本变量 variables" class="full">
        <KeyValueEditor
          :value="data.variables"
          empty-hint="暂无脚本变量；步骤参数里支持变量插值"
          @update:value="patchData({ variables: $event })"
        />
      </el-form-item>
    </div>

    <div class="section-label">执行默认值</div>
    <div class="form-grid">
      <el-form-item label="默认节点超时 (ms)">
        <el-input-number
          :model-value="settings.default_timeout_ms"
          :min="100"
          :max="600000"
          controls-position="right"
          style="width: 100%"
          @update:model-value="patchSettings({ default_timeout_ms: numberOrNull($event) ?? 8000 })"
        />
      </el-form-item>
      <el-form-item label="默认执行前停顿 (ms)">
        <el-input-number
          :model-value="settings.default_delay_before_ms"
          :min="0"
          :max="600000"
          :step="100"
          controls-position="right"
          style="width: 100%"
          @update:model-value="patchSettings({ default_delay_before_ms: numberOrNull($event) ?? 0 })"
        />
      </el-form-item>
      <el-form-item label="默认执行后停顿 (ms)">
        <el-input-number
          :model-value="settings.default_delay_after_ms"
          :min="0"
          :max="600000"
          :step="100"
          controls-position="right"
          style="width: 100%"
          @update:model-value="patchSettings({ default_delay_after_ms: numberOrNull($event) ?? 0 })"
        />
      </el-form-item>
    </div>

    <div class="section-label">失败处理默认值</div>
    <div class="failure-flow" role="note" :title="flow.summary">
      <span class="failure-flow-arrow" aria-hidden="true">↳</span>
      <span>{{ flow.summary }}</span>
    </div>
    <div class="form-grid">
      <el-form-item label="默认重试" class="full">
        <div class="retry-row">
          <el-switch
            data-test="script-retry"
            :model-value="retryOn"
            size="small"
            aria-label="启用/关闭脚本默认重试"
            @update:model-value="toggleScriptRetry(Boolean($event))"
          />
          <template v-if="retryOn && settings.retry">
            <el-input-number
              :model-value="settings.retry.count"
              :min="0"
              :max="20"
              size="small"
              controls-position="right"
              style="width: 108px"
              @update:model-value="setRetryCount"
            >
              <template #prefix>次数</template>
            </el-input-number>
            <el-input-number
              :model-value="settings.retry.interval_ms"
              :min="0"
              :max="60000"
              size="small"
              controls-position="right"
              style="width: 136px"
              @update:model-value="setRetryInterval"
            >
              <template #prefix>间隔</template>
            </el-input-number>
            <span class="hint">节点未单独配置时继承此值</span>
          </template>
          <span v-else class="hint">节点未单独配置时不重试</span>
        </div>
        <div v-if="retryOn" class="hint" style="margin-top: 2px">额外次数，1 = 最多 2 次；断言失败不重试。</div>
      </el-form-item>
      <el-form-item class="full">
        <template #label>
          默认失败策略（重试用尽后）
          <HelpTip text="跳转是节点级高级动作（需要目标节点）；脚本默认只提供中止/继续。" />
        </template>
        <el-select
          :model-value="settings.on_fail"
          style="width: 100%"
          @update:model-value="patchSettings({ on_fail: $event as 'abort' | 'continue' })"
        >
          <el-option value="abort" label="中止整个运行（abort）" />
          <el-option value="continue" label="继续下一步（continue）" />
        </el-select>
      </el-form-item>
      <el-form-item label="失败时自动截图" class="full">
        <div class="row wrap">
          <el-switch
            data-test="script-screenshot"
            :model-value="settings.screenshot_on_fail"
            size="small"
            aria-label="失败时是否自动截图"
            @update:model-value="patchSettings({ screenshot_on_fail: Boolean($event) })"
          />
          <span class="hint">主开关；节点里还要单独开启才会截图。</span>
        </div>
      </el-form-item>
    </div>

    <div class="section-label">采集</div>
    <div class="form-grid">
      <el-form-item label="运行采集 logcat">
        <div class="capture-field">
          <el-switch
            data-test="capture-logcat"
            :model-value="logcatAvailable && settings.logcat"
            :disabled="!logcatAvailable"
            size="small"
            @update:model-value="patchSettings({ logcat: Boolean($event) })"
          />
          <span v-if="!logcatAvailable" class="hint">
            全局未开启：先到
            <RouterLink to="/settings?tab=execution">设置 → 执行 → 采集</RouterLink>
            打开「允许采集 logcat」
          </span>
          <span v-else class="hint">本次运行把 logcat 收进产物</span>
        </div>
      </el-form-item>
      <el-form-item>
        <template #label>
          Logcat 范围
          <HelpTip text="app 范围按运行时 PID 过滤；App 重启或 PID 变化会自动重新解析；日志行按 PID 保留，堆栈续行跟随上一条。" />
        </template>
        <div class="capture-field">
          <el-select
            :model-value="settings.logcat_scope"
            :disabled="!logcatAvailable || !settings.logcat"
            size="small"
            style="width: 100%"
            @update:model-value="patchSettings({ logcat_scope: $event as 'device' | 'app' })"
          >
            <el-option value="device" label="整台设备（默认）" />
            <el-option value="app" label="仅脚本涉及的应用（按 PID 过滤）" />
          </el-select>
          <span v-if="settings.logcat_scope === 'app'" class="hint">
            app 范围按 PID 过滤；App 重启会自动重解析。
          </span>
        </div>
      </el-form-item>
      <el-form-item v-if="settings.logcat_scope === 'app'" label="应用包名（可选）">
        <div class="capture-field">
          <el-input
            :model-value="settings.logcat_package"
            :disabled="!logcatAvailable || !settings.logcat"
            size="small"
            placeholder="例如 com.example.app"
            @update:model-value="patchSettings({ logcat_package: $event })"
          />
          <span class="hint">留空从 app 节点推断；多个 app 节点会合并监听，没有包名时回退整机。</span>
        </div>
      </el-form-item>
      <el-form-item label="抓取域名（本次运行）">
        <div class="capture-field">
          <el-switch
            data-test="capture-network"
            :model-value="networkAvailable && settings.network_capture"
            :disabled="!networkAvailable"
            size="small"
            @update:model-value="patchSettings({ network_capture: Boolean($event) })"
          />
          <span v-if="!networkAvailable" class="hint">
            全局未开启：先到
            <RouterLink to="/settings?tab=execution">设置 → 执行 → 采集</RouterLink>
            打开「允许网络域名抓包」
          </span>
          <span v-else class="hint">只记录 host/port，不含 Header 与正文</span>
        </div>
      </el-form-item>
    </div>

    <div class="section-label">新建节点默认</div>
    <div class="form-grid">
      <el-form-item class="full">
        <template #label>
          新建节点默认重跑安全性
          <HelpTip text="safe 可安全重跑；stateful 依赖运行状态；destructive 会清数据，恢复时必须确认。" />
        </template>
        <el-select
          :model-value="settings.default_rerun_safety"
          style="width: 100%"
          @update:model-value="patchSettings({ default_rerun_safety: $event as 'safe' | 'stateful' | 'destructive' })"
        >
          <el-option
            v-for="option in RERUN_SAFETY_OPTIONS"
            :key="option.value"
            :value="option.value"
            :label="option.label"
          />
        </el-select>
        <div class="hint" style="margin-top: 2px">
          只影响之后新增的节点；已有节点保持自己的值。
        </div>
      </el-form-item>
    </div>
  </el-form>
</template>

<style scoped>
.script-settings-form {
  container-type: inline-size;
}

/* 抽屉窄（桌面 520px）时字段单列；宽度足够时再回到两列。 */
@container (max-width: 639px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
}

.capture-field {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  min-width: 0;
}

.capture-field .hint {
  line-height: 1.5;
}

.capture-field a {
  color: var(--accent);
  text-decoration: none;
}

.capture-field a:hover {
  text-decoration: underline;
}
</style>
