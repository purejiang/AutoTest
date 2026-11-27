<script setup lang="ts">
import { computed } from 'vue'

import { useScripts } from '@/api/scripts'
import type { NodeType, Target } from '@/types/api'
import { numberOrNull } from '@/lib/format'

import VariableField from '@/components/VariableField.vue'

import KeyValueEditor from './KeyValueEditor.vue'
import TargetEditor from './TargetEditor.vue'

const props = defineProps<{
  type: NodeType
  params: Record<string, unknown>
}>()

const emit = defineEmits<{ change: [params: Record<string, unknown>] }>()

// 12 类节点 params 的字段差异很大；这里用宽松对象读取，字段级仍由后端 schema 校验。
const p = computed(() => props.params as Record<string, any>)
const scriptsQuery = useScripts({ limit: 200 })

const KEY_OPTIONS = [
  'back',
  'home',
  'recents',
  'enter',
  'delete',
  'volume_up',
  'volume_down',
  'power',
  'menu',
  'notification',
].map((key) => ({ value: key, label: key }))

const APP_ACTIONS = [
  { value: 'launch', label: '启动 launch' },
  { value: 'stop', label: '停止 stop' },
  { value: 'clear', label: '清除数据 clear' },
  { value: 'restart', label: '重启 restart' },
  { value: 'grant', label: '授权 grant' },
]

const ASSERT_KINDS = [
  { value: 'exists', label: '存在 exists' },
  { value: 'not_exists', label: '不存在 not_exists' },
  { value: 'text_contains', label: '文本包含 text_contains' },
  { value: 'foreground', label: '前台应用 foreground' },
]

const scriptOptions = computed(() =>
  (scriptsQuery.data.value?.items ?? []).map((script) => ({
    value: script.id,
    label: `${script.name}（${script.step_count ?? script.data?.steps?.length ?? '?'} 节点）`,
  })),
)

function patch(partial: Record<string, unknown>): void {
  emit('change', { ...props.params, ...partial })
}

function setTarget(field: string, target: Target | null): void {
  if (target) patch({ [field]: target })
}

function waitMode(mode: 'sleep' | 'until'): void {
  patch({ mode })
}

const needsTarget = computed(
  () => p.value.kind === 'exists' || p.value.kind === 'not_exists' || p.value.kind === 'text_contains',
)
const needsAssertValue = computed(() => p.value.kind === 'text_contains' || p.value.kind === 'foreground')
</script>

<template>
  <!-- click -->
  <template v-if="type === 'click'">
    <el-form-item label="点击目标" required>
      <TargetEditor :value="p.target as Target" @update:value="setTarget('target', $event)" />
    </el-form-item>
  </template>

  <!-- long_click -->
  <template v-else-if="type === 'long_click'">
    <el-form-item label="长按目标" required>
      <TargetEditor :value="p.target as Target" @update:value="setTarget('target', $event)" />
    </el-form-item>
    <el-form-item label="长按时长 (ms)">
      <el-input-number
        :model-value="p.duration_ms ?? 800"
        size="small"
        :min="100"
        :max="10000"
        controls-position="right"
        @update:model-value="patch({ duration_ms: numberOrNull($event) ?? 800 })"
      />
    </el-form-item>
  </template>

  <!-- swipe / drag -->
  <template v-else-if="type === 'swipe' || type === 'drag'">
    <el-form-item label="起点 from" required>
      <TargetEditor :value="p.from as Target" @update:value="setTarget('from', $event)" />
    </el-form-item>
    <el-form-item label="终点 to" required>
      <TargetEditor :value="p.to as Target" @update:value="setTarget('to', $event)" />
    </el-form-item>
    <el-form-item label="持续时间 (ms)">
      <el-input-number
        :model-value="p.duration_ms ?? (type === 'drag' ? 1000 : 300)"
        size="small"
        :min="50"
        :max="10000"
        controls-position="right"
        @update:model-value="patch({ duration_ms: numberOrNull($event) ?? (type === 'drag' ? 1000 : 300) })"
      />
    </el-form-item>
  </template>

  <!-- input -->
  <template v-else-if="type === 'input'">
    <el-form-item label="输入文本" required>
      <VariableField
        :model-value="p.text ?? ''"
        multiline
        :rows="2"
        placeholder="支持 {{var}} 变量插值"
        @update:model-value="patch({ text: $event })"
      />
    </el-form-item>
    <el-form-item label="定位输入框（可选）">
      <TargetEditor allow-null :value="(p.target as Target | null) ?? null" @update:value="patch({ target: $event })" />
    </el-form-item>
    <el-form-item>
      <div class="row">
        <el-switch
          :model-value="Boolean(p.clear_first)"
          size="small"
          @update:model-value="patch({ clear_first: Boolean($event) })"
        />
        <span class="small">输入前清空输入框</span>
      </div>
    </el-form-item>
    <el-form-item>
      <div class="row">
        <el-switch
          :model-value="Boolean(p.hide_keyboard)"
          size="small"
          @update:model-value="patch({ hide_keyboard: Boolean($event) })"
        />
        <span class="small">输入后收起键盘（避免键盘盖住下面的按钮，点击落到键盘上）</span>
      </div>
    </el-form-item>
  </template>

  <!-- key -->
  <template v-else-if="type === 'key'">
    <el-form-item label="按键 key">
      <el-select
        :model-value="p.key ?? ''"
        size="small"
        clearable
        placeholder="选择按键名"
        style="width: 220px"
        @update:model-value="patch({ key: $event || undefined, keycode: undefined })"
      >
        <el-option v-for="option in KEY_OPTIONS" :key="option.value" :value="option.value" :label="option.label" />
      </el-select>
    </el-form-item>
    <el-form-item label="或 keycode" class="hint">
      <el-input-number
        :model-value="p.keycode ?? undefined"
        size="small"
        :min="0"
        placeholder="例如 4（返回键）"
        controls-position="right"
        style="width: 220px"
        @update:model-value="patch({ keycode: numberOrNull($event) ?? undefined, key: undefined })"
      />
    </el-form-item>
    <div class="hint">key 与 keycode 二选一，优先 key。</div>
  </template>

  <!-- wait -->
  <template v-else-if="type === 'wait'">
    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="等待节点会进入步骤表，并受节点超时限制"
      description="固定等待会真实占用执行时间；等待元素会按轮询间隔检查，超时按失败处理。"
      style="margin-bottom: 10px"
    />
    <el-form-item label="等待模式">
      <el-radio-group :model-value="p.mode ?? 'sleep'" size="small" @update:model-value="waitMode($event as 'sleep' | 'until')">
        <el-radio-button value="sleep">固定等待</el-radio-button>
        <el-radio-button value="until">等待元素</el-radio-button>
      </el-radio-group>
    </el-form-item>
    <template v-if="p.mode === 'until'">
      <el-form-item label="等待目标" required>
        <TargetEditor :value="p.target as Target" @update:value="setTarget('target', $event)" />
      </el-form-item>
      <div class="row wrap" style="gap: 12px">
        <el-form-item label="状态">
          <el-select
            :model-value="p.state ?? 'visible'"
            size="small"
            style="width: 150px"
            @update:model-value="patch({ state: $event })"
          >
            <el-option value="visible" label="可见 visible" />
            <el-option value="gone" label="消失 gone" />
          </el-select>
        </el-form-item>
        <el-form-item label="超时 (ms)">
          <el-input-number
            :model-value="p.timeout_ms ?? 5000"
            size="small"
            :min="100"
            :max="600000"
            controls-position="right"
            @update:model-value="patch({ timeout_ms: numberOrNull($event) ?? 5000 })"
          />
        </el-form-item>
        <el-form-item label="轮询间隔 (ms)">
          <el-input-number
            :model-value="p.interval_ms ?? 350"
            size="small"
            :min="50"
            :max="5000"
            controls-position="right"
            @update:model-value="patch({ interval_ms: numberOrNull($event) ?? 350 })"
          />
        </el-form-item>
      </div>
    </template>
    <el-form-item v-else label="等待时长 (ms)" required>
      <el-input-number
        :model-value="p.ms ?? 1000"
        size="small"
        :min="0"
        :max="600000"
        controls-position="right"
        @update:model-value="patch({ ms: numberOrNull($event) ?? 1000 })"
      />
    </el-form-item>
    <div class="hint" style="margin-top: -2px">
      节点超时不够时引擎会自动抬到「等待时长 + 2s」（上限 600000ms）；显式设置的「节点超时」仍是硬上限。
    </div>
  </template>

  <!-- app -->
  <template v-else-if="type === 'app'">
    <el-form-item label="操作">
      <el-select
        :model-value="p.action ?? 'launch'"
        size="small"
        style="width: 200px"
        @update:model-value="patch({ action: $event })"
      >
        <el-option v-for="option in APP_ACTIONS" :key="option.value" :value="option.value" :label="option.label" />
      </el-select>
    </el-form-item>
    <el-form-item label="package">
      <VariableField
        :model-value="p.package ?? ''"
        size="small"
        placeholder="com.example.app，或 {{package}}"
        style="max-width: 360px"
        @update:model-value="patch({ package: $event || null })"
      />
    </el-form-item>
    <div v-if="p.action === 'clear'" class="hint" style="margin: -2px 0 8px">
      清除数据会清空该应用的全部数据，属于破坏性操作；建议把「重跑安全性」设为 destructive。
      清理后平台会自动重新授予该应用声明的运行时权限（避免启动时系统授权框挡住脚本）。
    </div>
    <div v-if="p.action === 'grant'" class="hint" style="margin: -2px 0 8px">
      补齐该应用声明的全部运行时权限（通知/存储/相机等）。不想清数据、但启动后弹系统授权框时可单独加这一步。
    </div>
    <el-form-item v-if="(p.action ?? 'launch') === 'launch' || p.action === 'restart'" label="activity（可选）">
      <VariableField
        :model-value="p.activity ?? ''"
        size="small"
        placeholder="默认：launcher activity（也可填 .MainActivity）"
        style="max-width: 360px"
        @update:model-value="patch({ activity: $event || null })"
      />
    </el-form-item>
  </template>

  <!-- shell -->
  <template v-else-if="type === 'shell'">
    <el-form-item label="命令" required>
      <VariableField
        :model-value="p.command ?? ''"
        multiline
        :rows="3"
        placeholder="adb shell 命令（不含 adb shell 前缀），支持 {{var}}"
        @update:model-value="patch({ command: $event })"
      />
    </el-form-item>
    <div class="row wrap" style="gap: 12px">
      <el-form-item label="超时 (ms)">
        <el-input-number
          :model-value="p.timeout_ms ?? 10000"
          size="small"
          :min="100"
          :max="600000"
          controls-position="right"
          @update:model-value="patch({ timeout_ms: numberOrNull($event) ?? 10000 })"
        />
      </el-form-item>
      <el-form-item label="检查退出码">
        <el-switch
          :model-value="p.check_exit_code !== false"
          size="small"
          @update:model-value="patch({ check_exit_code: Boolean($event) })"
        />
      </el-form-item>
    </div>
  </template>

  <!-- assert -->
  <template v-else-if="type === 'assert'">
    <el-form-item label="断言类型">
      <el-select
        :model-value="p.kind ?? 'exists'"
        size="small"
        style="width: 220px"
        @update:model-value="patch({ kind: $event })"
      >
        <el-option v-for="option in ASSERT_KINDS" :key="option.value" :value="option.value" :label="option.label" />
      </el-select>
    </el-form-item>
    <el-form-item v-if="needsTarget" label="断言目标" required>
      <TargetEditor :value="p.target as Target" @update:value="setTarget('target', $event)" />
    </el-form-item>
    <el-form-item v-if="needsAssertValue" :label="p.kind === 'foreground' ? '前台包名' : '包含文本'" required>
      <VariableField
        :model-value="p.value ?? ''"
        size="small"
        :placeholder="p.kind === 'foreground' ? 'com.example.app，或 {{package}}' : '期望包含的文本'"
        style="max-width: 360px"
        @update:model-value="patch({ value: $event || null })"
      />
    </el-form-item>
  </template>

  <!-- sub_script -->
  <template v-else-if="type === 'sub_script'">
    <el-form-item label="子脚本" required>
      <el-select
        :model-value="p.script_id || ''"
        size="small"
        filterable
        :loading="scriptsQuery.isLoading.value"
        placeholder="选择要调用的脚本"
        style="width: 100%"
        @update:model-value="patch({ script_id: $event })"
      >
        <el-option v-for="option in scriptOptions" :key="option.value" :value="option.value" :label="option.label" />
      </el-select>
    </el-form-item>
    <el-form-item label="变量覆盖（可选）">
      <KeyValueEditor
        :value="p.variables ?? {}"
        variable-picker
        empty-hint="暂无变量覆盖，子脚本使用自身 variables"
        @update:value="patch({ variables: $event })"
      />
    </el-form-item>
    <div class="hint">整段子脚本受本节点「节点超时」限制；没显式设置时不再按脚本默认 8s 截断（最多到引擎上限/总预算），长流程建议显式设置。</div>
  </template>

  <!-- screenshot -->
  <template v-else-if="type === 'screenshot'">
    <el-form-item label="截图名称（可选）">
      <VariableField
        :model-value="p.name ?? ''"
        size="small"
        placeholder="默认：自动命名（也可填 {{name}}）"
        style="max-width: 360px"
        @update:model-value="patch({ name: $event || null })"
      />
    </el-form-item>
  </template>
</template>
