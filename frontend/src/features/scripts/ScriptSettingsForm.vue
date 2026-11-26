<script setup lang="ts">
import { computed } from 'vue'

import type { ScriptData, ScriptSettings } from '@/types/api'
import { numberOrNull } from '@/lib/format'

import KeyValueEditor from './KeyValueEditor.vue'

const props = defineProps<{ data: ScriptData }>()
const emit = defineEmits<{ change: [data: ScriptData] }>()

const settings = computed(() => props.data.settings)

function patchData(partial: Partial<ScriptData>): void {
  emit('change', { ...props.data, ...partial })
}

function patchSettings(partial: Partial<ScriptSettings>): void {
  emit('change', { ...props.data, settings: { ...props.data.settings, ...partial } })
}

function toggleRetry(checked: boolean): void {
  patchSettings({ retry: checked ? { count: 1, interval_ms: 1000 } : null })
}
</script>

<template>
  <el-form label-position="top" size="small" @submit.prevent>
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
      <el-form-item label="默认失败策略">
        <el-select
          :model-value="settings.on_fail"
          style="width: 100%"
          @update:model-value="patchSettings({ on_fail: $event as 'abort' | 'continue' })"
        >
          <el-option value="abort" label="abort（中止整个运行）" />
          <el-option value="continue" label="continue（继续下一步）" />
        </el-select>
      </el-form-item>
      <el-form-item label="运行采集 logcat">
        <el-switch
          :model-value="settings.logcat"
          size="small"
          @update:model-value="patchSettings({ logcat: Boolean($event) })"
        />
      </el-form-item>
      <el-form-item label="失败自动截图">
        <el-switch
          :model-value="settings.screenshot_on_fail"
          size="small"
          @update:model-value="patchSettings({ screenshot_on_fail: Boolean($event) })"
        />
      </el-form-item>
      <el-form-item label="脚本级默认重试" class="full">
        <div class="row wrap">
          <el-switch :model-value="settings.retry !== null" size="small" @update:model-value="toggleRetry(Boolean($event))" />
          <template v-if="settings.retry">
            <el-input-number
              :model-value="settings.retry.count"
              :min="0"
              :max="20"
              size="small"
              controls-position="right"
              style="width: 110px"
              @update:model-value="patchSettings({ retry: { ...settings.retry!, count: numberOrNull($event) ?? 0 } })"
            >
              <template #prefix>次数</template>
            </el-input-number>
            <el-input-number
              :model-value="settings.retry.interval_ms"
              :min="0"
              :max="60000"
              size="small"
              controls-position="right"
              style="width: 140px"
              @update:model-value="patchSettings({ retry: { ...settings.retry!, interval_ms: numberOrNull($event) ?? 0 } })"
            >
              <template #prefix>间隔</template>
            </el-input-number>
          </template>
          <span v-else class="hint">节点未单独配置时不重试</span>
        </div>
      </el-form-item>
    </div>
    <div class="hint">
      默认停顿作用在所有节点上：节点自己的「执行前/后停顿」填 0 时跟随这里的默认值，填非 0 值就是该节点自定义。
      录制转换生成的脚本默认停顿为 0，节奏由录制时还原的节点间隔决定。
    </div>
  </el-form>
</template>
