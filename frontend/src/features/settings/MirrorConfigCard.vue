<script setup lang="ts">
// 镜像画质卡：四档预设是「按设备长边缩放 + 上限 + 帧率 + 码率」的快捷方式，
// 保存 scrcpy.scale / max_size / max_fps / video_bit_rate；手动改任意一项显示「自定义」。
// 实际分辨率由后端在镜像启动时按设备长边计算。max_fps=0 表示不限（跟随设备刷新率）。
import { RefreshLeft } from '@element-plus/icons-vue'
import { computed, onMounted, ref } from 'vue'

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

// 预设 = 只打包“画质”（分辨率 + 码率），不打包帧率：
//   - 分辨率/码率决定清晰度与带宽，是“画质档位”；
//   - 帧率决定跟手程度，与画质正交（scrcpy 自己的 --max-fps 默认就是 0 = 不限）。
// 所以任何预设都可以单独把帧率设成 0；调帧率不会让预设变成“自定义”。
const MIRROR_PRESETS = [
  {
    value: 'light',
    label: '轻量',
    detail: '70% · 6Mbps',
    note: '省带宽 / 老电脑',
    recommended: false,
    scale: 70,
    maxSize: 4096,
    videoBitRate: 6_000_000,
  },
  {
    value: 'standard',
    label: '标准',
    detail: '85% · 10Mbps',
    note: '日常调试推荐',
    recommended: true,
    scale: 85,
    maxSize: 4096,
    videoBitRate: 10_000_000,
  },
  {
    value: 'hd',
    label: '高清',
    detail: '100% 原生 · 16Mbps',
    note: '看细节 / 截图核对',
    recommended: false,
    scale: 100,
    maxSize: 4096,
    videoBitRate: 16_000_000,
  },
] as const

type MirrorPreset = (typeof MIRROR_PRESETS)[number]

const scaleIsNative = computed(() => Number(props.values['scrcpy.scale']) >= 100)
const fpsIsUnlimited = computed(() => Number(props.values['scrcpy.max_fps']) === 0)

// 本机显示器刷新率（rAF 采样 ~0.5s）：60Hz 屏幕上“不限帧率”会让设备按 120Hz 推流，
// 解码量翻倍却一帧都显示不出来——这是浏览器端最常见的无谓开销。
const displayHz = ref<number | null>(null)
onMounted(() => {
  if (typeof requestAnimationFrame !== 'function' || typeof performance === 'undefined') return
  let frames = 0
  const started = performance.now()
  const tick = (now: number): void => {
    frames += 1
    const elapsed = now - started
    if (elapsed >= 500) {
      displayHz.value = Math.round((frames * 1000) / elapsed)
      return
    }
    requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)
})

/** 60Hz 屏幕 + 不限帧率 = 解码量白白翻倍，提示一个够用的上限。 */
const fpsOverkill = computed(() => {
  if (!fpsIsUnlimited.value || displayHz.value === null) return null
  if (displayHz.value > 75) return null
  return displayHz.value
})

const mirrorPreset = computed<string>(() => {
  const scale = Number(props.values['scrcpy.scale'])
  const maxSize = Number(props.values['scrcpy.max_size'])
  const videoBitRate = Number(props.values['scrcpy.video_bit_rate'])
  const matched = MIRROR_PRESETS.find(
    (preset) => preset.scale === scale && preset.maxSize === maxSize && preset.videoBitRate === videoBitRate,
  )
  return matched?.value ?? 'custom'
})

function applyPreset(preset: MirrorPreset): void {
  emit('change', 'scrcpy.scale', preset.scale)
  emit('change', 'scrcpy.max_size', preset.maxSize)
  emit('change', 'scrcpy.video_bit_rate', preset.videoBitRate)
}

/** 帧率与预设解耦：这里只改 scrcpy.max_fps，画质档位保持不变。 */
function applyFps(fps: number): void {
  emit('change', 'scrcpy.max_fps', fps)
}

// 帧率快速选项：0 = 不限是 scrcpy 默认，也是“跟手”的关键；60/30 是解码吃紧时的降级。
const FPS_QUICK_PICKS = [
  { value: 0, label: '不限' },
  { value: 60, label: '60' },
  { value: 30, label: '30' },
] as const

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

// 当前组合一览：把四个数字拼成一句话，避免用户对着一堆输入框猜“现在到底跑什么”。
const summary = computed(() => {
  const scale = Number(props.values['scrcpy.scale'])
  const fps = Number(props.values['scrcpy.max_fps'])
  const bitrate = bitrateMbps.value
  const parts = [
    Number.isFinite(scale) ? `${scale}%` : null,
    Number.isFinite(fps) ? (fps === 0 ? '帧率不限' : `${fps}fps`) : null,
    bitrate === null ? null : `${bitrate}Mbps`,
  ].filter(Boolean)
  return parts.join(' · ')
})

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

function hasDefault(key: string): boolean {
  return props.schema[key]?.default !== undefined
}

function defaultLabel(key: string): string {
  const value = props.schema[key]?.default
  if (key === 'scrcpy.video_bit_rate') {
    const bps = Number(value)
    return Number.isFinite(bps) ? `${Math.round((bps / 1_000_000) * 10) / 10} Mbps` : '—'
  }
  if (value === undefined || value === null || value === '') {
    return key === 'scrcpy.crop' ? '不裁剪' : '—'
  }
  return String(value)
}
</script>

<template>
  <section class="card mirror-config-card">
    <div class="card-head">
      <h2>镜像</h2>
      <RestartHint
        target="镜像"
        detail="预设按设备长边比例缩放，再受长边上限约束；实际分辨率在镜像启动时按设备计算。保存后需停止并重新启动镜像。"
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

      <div class="preset-hint">
        预设只决定<strong>画质</strong>（缩放 + 码率）；<strong>帧率是独立项</strong>，任何预设都可以单独设成
        0（不限）。<br />
        100% 使用设备原生分辨率；低于 100%
        时按设备长边比例缩放并受长边上限约束，实际分辨率在启动镜像时计算，不放大。
      </div>

      <div v-if="summary" class="mirror-summary">
        <span>当前：{{ summary }}</span>
        <span v-if="mirrorPreset === 'custom'" class="mirror-summary-tag">自定义画质</span>
      </div>

      <div class="field-grid">
        <div class="field">
          <span class="field-label">
            按设备缩放（%）
            <StatusTag
              v-if="sourceOf('scrcpy.scale') !== 'default'"
              :tone="sourceMeta('scrcpy.scale').tone"
              :title="sourceTitle('scrcpy.scale')"
            >
              {{ sourceMeta('scrcpy.scale').label }}
            </StatusTag>
            <el-button
              v-if="sourceOf('scrcpy.scale') === 'db'"
              size="small"
              text
              :icon="RefreshLeft"
              title="删除数据库覆盖，按优先级回到配置文件/默认值"
              @click="emit('clear', 'scrcpy.scale')"
            />
          </span>
          <el-input-number
            :model-value="numeric('scrcpy.scale')"
            size="small"
            :min="limitOf('scrcpy.scale', 'min', 50)"
            :max="limitOf('scrcpy.scale', 'max', 100)"
            :step="5"
            controls-position="right"
            style="width: 100%"
            :disabled="disabled"
            @update:model-value="updateNumber('scrcpy.scale', $event)"
          />
          <div v-if="hasDefault('scrcpy.scale')" class="field-default">
            默认：{{ defaultLabel('scrcpy.scale') }}
          </div>
        </div>

        <div class="field">
          <span class="field-label">
            长边上限（px）
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
            :max="limitOf('scrcpy.max_size', 'max', 4096)"
            :step="10"
            controls-position="right"
            style="width: 100%"
            :disabled="disabled || scaleIsNative"
            @update:model-value="updateNumber('scrcpy.max_size', $event)"
          />
          <div v-if="scaleIsNative" class="field-default">100% 时按原生分辨率，不使用上限</div>
          <div v-else-if="hasDefault('scrcpy.max_size')" class="field-default">
            默认：{{ defaultLabel('scrcpy.max_size') }}
          </div>
        </div>

        <div class="field">
          <span class="field-label">
            帧率（fps）
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
            :min="limitOf('scrcpy.max_fps', 'min', 0)"
            :max="limitOf('scrcpy.max_fps', 'max', 240)"
            :step="1"
            controls-position="right"
            style="width: 100%"
            :disabled="disabled"
            @update:model-value="updateNumber('scrcpy.max_fps', $event)"
          />
          <div class="fps-quick">
            <button
              v-for="option in FPS_QUICK_PICKS"
              :key="option.value"
              type="button"
              class="fps-chip"
              :class="{ active: Number(values['scrcpy.max_fps']) === option.value }"
              :disabled="disabled"
              @click="applyFps(option.value)"
            >
              {{ option.label }}
            </button>
          </div>
          <div v-if="fpsOverkill !== null" class="field-hint-warn">
            本机屏幕约 {{ fpsOverkill }}Hz：设 0 会让设备按自身刷新率（可能 120Hz）推流，约一半解码是白费；
            建议改成 {{ fpsOverkill }}。
          </div>
          <div v-else-if="fpsIsUnlimited" class="field-default">
            0 = 不限，跟随设备刷新率（与 scrcpy 默认一致）；画质预设不受影响
          </div>
          <div v-else-if="hasDefault('scrcpy.max_fps')" class="field-default">
            默认：{{ defaultLabel('scrcpy.max_fps') }}（不限）
          </div>
        </div>

        <div class="field">
          <span class="field-label">
            码率（Mbps）
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
          <div v-if="hasDefault('scrcpy.video_bit_rate')" class="field-default">
            默认：{{ defaultLabel('scrcpy.video_bit_rate') }}
          </div>
        </div>

        <div class="field">
          <span class="field-label">
            裁剪（w:h:x:y）
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
          <div v-if="hasDefault('scrcpy.crop')" class="field-default">
            默认：{{ defaultLabel('scrcpy.crop') }}
          </div>
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

.preset-hint {
  margin: 4px 0 8px;
  color: var(--muted-2);
  font-size: 10px;
  line-height: 1.5;
}

.field-hint-warn {
  padding: 4px 6px;
  border-radius: 5px;
  background: var(--amber-soft);
  color: var(--amber);
  font-size: 9.5px;
  line-height: 1.45;
}

.preset-hint strong {
  color: var(--muted);
  font-weight: 600;
}

/* “当前：85% · 帧率不限 · 10Mbps” —— 让用户不必从四个输入框里拼出实际状态 */
.mirror-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 10px;
  padding: 5px 9px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface-2);
  color: var(--ink);
  font-family: var(--mono);
  font-size: 10.5px;
}

.mirror-summary-tag {
  padding: 0 5px;
  border-radius: 4px;
  background: var(--surface-3);
  color: var(--muted-2);
  font-family: var(--font);
  font-size: 9.5px;
}

/* 帧率快捷项：不限 / 60 / 30（与预设解耦，随时可切） */
.fps-quick {
  display: flex;
  gap: 4px;
}

.fps-chip {
  flex: 1;
  height: 22px;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: var(--surface);
  color: var(--muted);
  font-family: var(--mono);
  font-size: 10px;
  cursor: pointer;
}

.fps-chip:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.fps-chip.active {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}

.fps-chip:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));
  gap: 10px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.field-default {
  color: var(--muted-2);
  font-size: 10px;
  line-height: 1.4;
  font-variant-numeric: tabular-nums;
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

@media (max-width: 1400px) {
  .preset-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
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
