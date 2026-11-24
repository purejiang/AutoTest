<script setup lang="ts">
// 镜像画质卡：三档预设只是「一键填三项参数」的快捷方式，保存的仍是
// scrcpy.max_size / max_fps / video_bit_rate；不新增任何配置键。
// 手动改动任意一项后自动显示「自定义」。
import { RefreshLeft } from '@element-plus/icons-vue'
import { computed } from 'vue'

import type { SettingEntry } from '@/types/api'
import { numberOrNull, settingSourceMeta } from '@/lib/format'
import StatusTag from '@/components/StatusTag.vue'
import RestartHint from './RestartHint.vue'

type SettingSource = 'default' | 'file' | 'db' | 'env'

const props = withDefaults(
  defineProps<{
    values: Record<string, unknown>
    sources: Record<string, SettingSource>
    schema: Record<string, SettingEntry>
    serverVersion?: string
    disabled?: boolean
  }>(),
  { serverVersion: '5.0.1', disabled: false },
)

const emit = defineEmits<{
  change: [key: string, value: unknown]
  clear: [key: string]
}>()

const MIRROR_PRESETS = [
  {
    value: 'smooth',
    label: '流畅优先',
    detail: '1200px · 30fps · 6Mbps',
    note: '低带宽，远程 / 弱网',
    recommended: false,
    maxSize: 1200,
    maxFps: 30,
    videoBitRate: 6_000_000,
  },
  {
    value: 'balanced',
    label: '均衡',
    detail: '1600px · 30fps · 8Mbps',
    note: '日常调试推荐',
    recommended: true,
    maxSize: 1600,
    maxFps: 30,
    videoBitRate: 8_000_000,
  },
  {
    value: 'sharp',
    label: '清晰优先',
    detail: '1920px · 30fps · 16Mbps',
    note: '看细节 / 截图核对',
    recommended: false,
    maxSize: 1920,
    maxFps: 30,
    videoBitRate: 16_000_000,
  },
] as const

type MirrorPreset = (typeof MIRROR_PRESETS)[number]

const DEFAULT_PRESET: MirrorPreset = MIRROR_PRESETS.find((preset) => preset.recommended) ?? MIRROR_PRESETS[1]

const mirrorPreset = computed<string>(() => {
  const maxSize = Number(props.values['scrcpy.max_size'])
  const maxFps = Number(props.values['scrcpy.max_fps'])
  const videoBitRate = Number(props.values['scrcpy.video_bit_rate'])
  const matched = MIRROR_PRESETS.find(
    (preset) =>
      preset.maxSize === maxSize && preset.maxFps === maxFps && preset.videoBitRate === videoBitRate,
  )
  return matched?.value ?? 'custom'
})

const mirrorPresetLabel = computed(() => {
  const matched = MIRROR_PRESETS.find((preset) => preset.value === mirrorPreset.value)
  return matched ? `${matched.label}（${matched.detail}）` : '自定义（下面三项手动组合）'
})

function applyPreset(preset: MirrorPreset): void {
  emit('change', 'scrcpy.max_size', preset.maxSize)
  emit('change', 'scrcpy.max_fps', preset.maxFps)
  emit('change', 'scrcpy.video_bit_rate', preset.videoBitRate)
}

function restoreDefaultPreset(): void {
  applyPreset(DEFAULT_PRESET)
}

function numeric(key: string): number | null {
  const value = Number(props.values[key])
  return Number.isFinite(value) ? value : null
}

function updateNumber(key: string, value: number | null | undefined): void {
  const num = numberOrNull(value)
  if (num === null) return
  emit('change', key, num)
}

// 码率对用户按 Mbps 展示，对后端仍存 bps，避免新增 key。
const bitrateMbps = computed<number | null>(() => {
  const value = numeric('scrcpy.video_bit_rate')
  if (value === null) return null
  return Math.round((value / 1_000_000) * 10) / 10
})

function updateBitrate(value: number | null | undefined): void {
  const num = numberOrNull(value)
  if (num === null) return
  emit('change', 'scrcpy.video_bit_rate', Math.round(num * 1_000_000))
}

function cropValue(): string {
  const value = props.values['scrcpy.crop']
  return value === null || value === undefined ? '' : String(value)
}

function limitOf(key: string, field: 'min' | 'max', fallback: number): number {
  const value = props.schema[key]?.[field]
  return typeof value === 'number' ? value : fallback
}

function sourceOf(key: string): SettingSource {
  return props.sources[key] ?? 'default'
}

function sourceMeta(key: string) {
  return settingSourceMeta(sourceOf(key))
}

function sourceTitle(key: string): string {
  const source = sourceOf(key)
  if (source === 'db') return '来自数据库覆盖，可点 ↺ 删除；删除后按优先级回到文件/默认值'
  if (source === 'file') return '来自配置文件，优先级高于默认值；需修改配置文件后重新加载'
  if (source === 'env') return '来自环境变量，优先级最高；需取消环境变量并重启进程'
  return '当前使用代码默认值'
}
</script>

<template>
  <section class="card mirror-config-card">
    <div class="card-head">
      <h2>镜像</h2>
      <RestartHint
        target="镜像"
        detail="预设只修改最大边、帧率和码率；裁剪单独设置。保存后需停止并重新启动镜像；正在进行的运行不受影响。"
      />
      <StatusTag tone="blue">scrcpy {{ serverVersion }}</StatusTag>
    </div>
    <div class="card-body">
      <div class="preset-grid">
        <button
          v-for="preset in MIRROR_PRESETS"
          :key="preset.value"
          type="button"
          class="preset"
          :class="{ active: mirrorPreset === preset.value }"
          :disabled="disabled"
          @click="applyPreset(preset)"
        >
          <span class="preset-name">
            {{ preset.label }}
            <StatusTag v-if="preset.recommended" tone="green">默认</StatusTag>
          </span>
          <span class="preset-detail">{{ preset.detail }}</span>
          <span class="preset-note">{{ preset.note }}</span>
        </button>
      </div>

      <div class="preset-current">
        当前：<b>{{ mirrorPresetLabel }}</b>
        <el-button
          v-if="mirrorPreset === 'custom'"
          size="small"
          text
          type="primary"
          title="套用默认画质：均衡 1600px / 30fps / 8Mbps"
          @click="restoreDefaultPreset"
        >
          恢复默认画质
        </el-button>
        <span> · 默认 {{ DEFAULT_PRESET.detail }}</span>
      </div>

      <div class="field-grid">
        <div class="field">
          <span class="field-label">
            最大边 (px)
            <StatusTag
              v-if="sourceOf('scrcpy.max_size') !== 'default'"
              :tone="sourceMeta('scrcpy.max_size').tone"
              :title="sourceTitle('scrcpy.max_size')"
            >
              {{ sourceMeta('scrcpy.max_size').label }}
            </StatusTag>
            <el-button
              v-if="sourceOf('scrcpy.max_size') === 'db'"
              size="small"
              text
              :icon="RefreshLeft"
              title="删除数据库覆盖，按优先级回到配置文件/默认值"
              @click="emit('clear', 'scrcpy.max_size')"
            />
          </span>
          <el-input-number
            :model-value="numeric('scrcpy.max_size')"
            size="small"
            :min="limitOf('scrcpy.max_size', 'min', 480)"
            :max="limitOf('scrcpy.max_size', 'max', 2560)"
            :step="10"
            controls-position="right"
            style="width: 100%"
            :disabled="disabled"
            @update:model-value="updateNumber('scrcpy.max_size', $event)"
          />
        </div>

        <div class="field">
          <span class="field-label">
            帧率 (fps)
            <StatusTag
              v-if="sourceOf('scrcpy.max_fps') !== 'default'"
              :tone="sourceMeta('scrcpy.max_fps').tone"
              :title="sourceTitle('scrcpy.max_fps')"
            >
              {{ sourceMeta('scrcpy.max_fps').label }}
            </StatusTag>
            <el-button
              v-if="sourceOf('scrcpy.max_fps') === 'db'"
              size="small"
              text
              :icon="RefreshLeft"
              title="删除数据库覆盖，按优先级回到配置文件/默认值"
              @click="emit('clear', 'scrcpy.max_fps')"
            />
          </span>
          <el-input-number
            :model-value="numeric('scrcpy.max_fps')"
            size="small"
            :min="limitOf('scrcpy.max_fps', 'min', 5)"
            :max="limitOf('scrcpy.max_fps', 'max', 120)"
            :step="1"
            controls-position="right"
            style="width: 100%"
            :disabled="disabled"
            @update:model-value="updateNumber('scrcpy.max_fps', $event)"
          />
        </div>

        <div class="field">
          <span class="field-label">
            码率 (Mbps)
            <StatusTag
              v-if="sourceOf('scrcpy.video_bit_rate') !== 'default'"
              :tone="sourceMeta('scrcpy.video_bit_rate').tone"
              :title="sourceTitle('scrcpy.video_bit_rate')"
            >
              {{ sourceMeta('scrcpy.video_bit_rate').label }}
            </StatusTag>
            <el-button
              v-if="sourceOf('scrcpy.video_bit_rate') === 'db'"
              size="small"
              text
              :icon="RefreshLeft"
              title="删除数据库覆盖，按优先级回到配置文件/默认值"
              @click="emit('clear', 'scrcpy.video_bit_rate')"
            />
          </span>
          <el-input-number
            :model-value="bitrateMbps"
            size="small"
            :min="limitOf('scrcpy.video_bit_rate', 'min', 1_000_000) / 1_000_000"
            :max="limitOf('scrcpy.video_bit_rate', 'max', 32_000_000) / 1_000_000"
            :step="1"
            controls-position="right"
            style="width: 100%"
            :disabled="disabled"
            @update:model-value="updateBitrate"
          />
        </div>

        <div class="field">
          <span class="field-label">
            裁剪 w:h:x:y
            <StatusTag
              v-if="sourceOf('scrcpy.crop') !== 'default'"
              :tone="sourceMeta('scrcpy.crop').tone"
              :title="sourceTitle('scrcpy.crop')"
            >
              {{ sourceMeta('scrcpy.crop').label }}
            </StatusTag>
            <el-button
              v-if="sourceOf('scrcpy.crop') === 'db'"
              size="small"
              text
              :icon="RefreshLeft"
              title="删除数据库覆盖，按优先级回到配置文件/默认值"
              @click="emit('clear', 'scrcpy.crop')"
            />
          </span>
          <el-input
            :model-value="cropValue()"
            size="small"
            clearable
            placeholder="留空不裁剪"
            class="code-text"
            :disabled="disabled"
            @update:model-value="emit('change', 'scrcpy.crop', $event)"
          />
        </div>
      </div>

    </div>
  </section>
</template>

<style scoped>
.mirror-config-card .card-body {
  padding: 14px;
}

.preset-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.preset {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}

.preset:hover {
  border-color: var(--accent);
}

.preset.active {
  border-color: var(--accent);
  background: var(--accent-soft);
  box-shadow: 0 0 0 2px var(--accent-soft);
}

.preset:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}

.preset-name {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11.5px;
  font-weight: 650;
}

.preset-detail {
  color: var(--muted);
  font-family: var(--mono);
  font-size: 10px;
}

.preset-note {
  color: var(--muted-2);
  font-size: 9.5px;
}

.preset-current {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  margin: 9px 0 8px;
  color: var(--muted);
  font-size: 10.5px;
}

.preset-current b {
  color: var(--accent);
  font-weight: 650;
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.field-label {
  display: flex;
  align-items: center;
  gap: 4px;
  min-height: 18px;
  color: var(--muted);
  font-size: 11px;
  white-space: nowrap;
}

.field-label :deep(.tag) {
  flex: none;
}

@media (max-width: 1200px) {
  .field-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .preset-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .field-grid {
    grid-template-columns: 1fr;
  }
}
</style>
