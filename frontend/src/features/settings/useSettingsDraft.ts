// 设置页草稿状态：本地编辑值、未保存统计、保存/放弃/恢复默认/重新载入。
// 页面（SettingsPage.vue）负责创建并通过 provideSettingsDraft() 下发，
// 各 tab 组件用 useSettingsDraftContext() 读取，避免层层传 props。
import { useMutation } from '@tanstack/vue-query'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, inject, provide, reactive, ref, watch, type ComputedRef, type InjectionKey } from 'vue'

import {
  useDeleteSettingOverride,
  useReloadSettings,
  useResetSettings,
  useScrcpyOptions,
  useSettings,
  useUpdateSettings,
} from '@/api/settings'
import type { SettingEntry, SettingsResponse } from '@/types/api'

export type SettingSource = 'default' | 'file' | 'env'

// 每个 key 只出现在一个分组里；只渲染后端 SETTINGS_SCHEMA 里已有的 key。
export type KeyGroup = 'adb' | 'scrcpyJar' | 'engine' | 'recording' | 'capture' | 'security' | 'storage'

export const GROUP_KEYS: Record<KeyGroup, string[]> = {
  adb: ['adb.path', 'adb.connect_timeout_s'],
  scrcpyJar: ['scrcpy.jar'],
  engine: [
    'engine.poll_interval_ms',
    'engine.default_timeout_ms',
    'engine.default_delay_before_ms',
    'engine.default_delay_after_ms',
    'engine.default_on_fail',
    'engine.max_total_ms',
    'engine.max_steps',
  ],
  recording: ['recording.click_dump', 'recording.confirm_before_save', 'recording.target_mode'],
  capture: ['capture.logcat_auto_attach', 'capture.network.enabled', 'capture.network.port'],
  security: ['security.token'],
  storage: ['retention.days', 'retention.report_exclude_video', 'retention.report_max_mb'],
}

export interface SettingsDraft {
  /** 本地编辑值（保存前不落盘）。 */
  values: Record<string, any>
  settings: ComputedRef<SettingsResponse | null>
  sources: ComputedRef<Record<string, SettingSource>>
  schemaByKey: ComputedRef<Record<string, SettingEntry>>
  entriesFor: (group: KeyGroup) => SettingEntry[]
  sourceOf: (entry: SettingEntry) => SettingSource
  configFile: ComputedRef<string>
  /** scrcpy-server 版本 / 内置 jar 由后端提供。 */
  scrcpyVersion: ComputedRef<string>
  scrcpyJar: ComputedRef<string>
  scrcpyStatus: ComputedRef<{ tone: 'gray' | 'green' | 'red'; label: string }>
  changedKeys: ComputedRef<string[]>
  showSavebar: ComputedRef<boolean>
  showSavedTag: ComputedRef<boolean>
  overrideCount: ComputedRef<number>
  hasOverrides: ComputedRef<boolean>
  overrideTooltip: ComputedRef<string>
  setValue: (key: string, value: unknown) => void
  deleteOverride: (entry: SettingEntry) => void
  clearKey: (key: string) => void
  discardChanges: () => Promise<void>
  saveSettings: () => Promise<void>
  resetSettings: () => Promise<void>
  reloadSettings: () => Promise<void>
  onMoreCommand: (command: string) => void
  resetPending: ComputedRef<boolean>
  savePending: ComputedRef<boolean>
}

function cloneRecord<T>(input: T): T {
  return JSON.parse(JSON.stringify(input)) as T
}

export function useSettingsDraft(): SettingsDraft {
  const settingsQuery = useSettings()
  const updateMutation = useUpdateSettings()
  const resetMutation = useResetSettings()
  const deleteOverrideMutation = useDeleteSettingOverride()
  const reloadMutation = useReloadSettings()
  const scrcpyOptionsQuery = useScrcpyOptions()

  const values = reactive<Record<string, any>>({})
  const loaded = ref<Record<string, unknown>>({})

  // 用 loaded 的深拷贝重置本地编辑值，不触发任何后端请求。
  function restoreLoadedValues(): void {
    for (const key of Object.keys(values)) delete values[key]
    Object.assign(values, cloneRecord(loaded.value))
  }

  watch(
    () => settingsQuery.data.value,
    (data) => {
      if (!data) return
      loaded.value = cloneRecord(data.effective) as Record<string, unknown>
      restoreLoadedValues()
    },
    { immediate: true },
  )

  const settings = computed(() => settingsQuery.data.value ?? null)
  const sources = computed<Record<string, SettingSource>>(
    () => (settings.value?.sources ?? {}) as Record<string, SettingSource>,
  )
  const configFile = computed(() => settings.value?.config_file ?? '')

  const scrcpyVersion = computed(() => scrcpyOptionsQuery.data.value?.server_version ?? '—')
  const scrcpyJar = computed(() => {
    const version = scrcpyOptionsQuery.data.value?.server_version
    return version ? `vendor/scrcpy-server-v${version}.jar` : '—'
  })
  const scrcpyStatus = computed(() => {
    if (scrcpyOptionsQuery.isError.value) return { tone: 'gray' as const, label: '未知' }
    if (scrcpyOptionsQuery.data.value?.jar_present === false) {
      return { tone: 'red' as const, label: '缺失' }
    }
    return { tone: 'green' as const, label: '已锁定' }
  })

  const schemaByKey = computed<Record<string, SettingEntry>>(() => {
    const out: Record<string, SettingEntry> = {}
    for (const entry of settings.value?.schema ?? []) out[entry.key] = entry
    return out
  })

  const schemaKeys = computed(() => new Set(Object.keys(schemaByKey.value)))

  function entriesFor(group: KeyGroup): SettingEntry[] {
    const keys = GROUP_KEYS[group]
    return (settings.value?.schema ?? []).filter((entry) => keys.includes(entry.key))
  }

  function sourceOf(entry: SettingEntry): SettingSource {
    return sources.value[entry.key] ?? 'default'
  }

  // 保存条只统计后端 schema key 的改动；主题 / 语言是纯前端偏好，不产生未保存项。
  const changedKeys = computed(() =>
    Object.keys(values)
      .filter((key) => schemaKeys.value.has(key))
      .filter((key) => JSON.stringify(values[key]) !== JSON.stringify(loaded.value[key])),
  )

  const showSavebar = computed(() => changedKeys.value.length > 0)
  const showSavedTag = computed(() => Boolean(settings.value) && changedKeys.value.length === 0)

  const overrideCount = computed(
    () => Object.values(sources.value).filter((source) => source !== 'default').length,
  )
  const hasOverrides = computed(() => overrideCount.value > 0)
  const overrideTooltip = computed(() => {
    const counts: Record<string, number> = { file: 0, env: 0 }
    for (const source of Object.values(sources.value)) {
      if (source in counts) counts[source] += 1
    }
    return [
      `有 ${overrideCount.value} 项设置不是代码默认值：配置文件 ${counts.file} 项 · 环境变量 ${counts.env} 项。`,
      '优先级：默认值 < 配置文件 < 环境变量。',
      '设置页保存 = 原子写 config.json 并立即重载；手改文件后可点「重新载入配置文件」。',
      '启动项（数据目录、端口等）启动时确定，运行中不能切换，改了需重启。',
    ].join('\n')
  })

  function setValue(key: string, value: unknown): void {
    values[key] = value
  }

  async function confirmDeleteOverride(key: string, title: string): Promise<void> {
    try {
      await ElMessageBox.confirm(`从配置文件移除「${title}」，回到默认值/环境变量？`, '移除配置项', {
        type: 'warning',
        confirmButtonText: '移除',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
    try {
      await deleteOverrideMutation.mutateAsync(key)
      ElMessage.success('已从配置文件移除')
    } catch {
      // 全局错误提示
    }
  }

  function deleteOverride(entry: SettingEntry): void {
    void confirmDeleteOverride(entry.key, entry.title)
  }

  function clearKey(key: string): void {
    void confirmDeleteOverride(key, schemaByKey.value[key]?.title ?? key)
  }

  async function saveSettings(): Promise<void> {
    if (changedKeys.value.length === 0) {
      ElMessage.info('没有改动需要保存')
      return
    }
    // 保存成功后 query 数据会刷新并重置 values，这里先快照，避免提示里变成 0 项。
    const count = changedKeys.value.length
    const mirrorTouched = changedKeys.value.some((key) => key.startsWith('scrcpy.'))
    const payload: Record<string, unknown> = {}
    for (const key of changedKeys.value) payload[key] = values[key]
    try {
      await updateMutation.mutateAsync(payload)
      const file = settings.value?.config_file ?? 'config.json'
      ElMessage.success(
        `已写入 ${file}（${count} 项）${mirrorTouched ? ' · 镜像参数需停止并重新启动镜像生效' : ''}`,
      )
    } catch {
      // 全局错误提示
    }
  }

  // 放弃本地编辑：回到最近一次加载/保存后的值，不请求后端。
  async function discardChanges(): Promise<void> {
    const count = changedKeys.value.length
    if (count === 0) return
    try {
      await ElMessageBox.confirm(`放弃 ${count} 项未保存的改动，恢复为上次加载的值？`, '放弃更改', {
        type: 'warning',
        confirmButtonText: '放弃更改',
        cancelButtonText: '继续编辑',
      })
    } catch {
      return
    }
    restoreLoadedValues()
    ElMessage.info('已放弃未保存的更改')
  }

  async function resetSettings(): Promise<void> {
    try {
      await ElMessageBox.confirm('清空 DB/文件层覆盖，所有配置回到代码默认值。', '恢复默认设置', {
        type: 'warning',
        confirmButtonText: '恢复默认',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
    try {
      await resetMutation.mutateAsync()
      ElMessage.success('已恢复默认设置')
    } catch {
      // 全局错误提示
    }
  }

  async function reloadSettings(): Promise<void> {
    if (changedKeys.value.length > 0) {
      try {
        await ElMessageBox.confirm(
          `有 ${changedKeys.value.length} 项未保存的改动，重新载入会丢弃这些改动。`,
          '重新载入配置文件',
          { type: 'warning', confirmButtonText: '重新载入', cancelButtonText: '取消' },
        )
      } catch {
        return
      }
    }
    try {
      const result = await reloadMutation.mutateAsync()
      const pending = result.restart_required ?? []
      if (pending.length > 0) {
        ElMessage.warning(`已重新载入；${pending.join('、')} 是启动项，需重启 AutoTest 才生效`)
      } else {
        ElMessage.success('已重新载入配置文件')
      }
    } catch {
      // 全局错误提示
    }
  }

  function onMoreCommand(command: string): void {
    if (command === 'reset') {
      void resetSettings()
      return
    }
    if (command === 'reload') void reloadSettings()
  }

  return {
    values,
    settings,
    sources,
    schemaByKey,
    entriesFor,
    sourceOf,
    configFile,
    scrcpyVersion,
    scrcpyJar,
    scrcpyStatus,
    changedKeys,
    showSavebar,
    showSavedTag,
    overrideCount,
    hasOverrides,
    overrideTooltip,
    setValue,
    deleteOverride,
    clearKey,
    discardChanges,
    saveSettings,
    resetSettings,
    reloadSettings,
    onMoreCommand,
    resetPending: computed(() => resetMutation.isPending.value),
    savePending: computed(() => updateMutation.isPending.value),
  }
}

const DRAFT_KEY: InjectionKey<SettingsDraft> = Symbol('settings-draft')

export function provideSettingsDraft(draft: SettingsDraft): void {
  provide(DRAFT_KEY, draft)
}

export function useSettingsDraftContext(): SettingsDraft {
  const draft = inject(DRAFT_KEY, null)
  if (!draft) throw new Error('useSettingsDraftContext() must be used inside <SettingsPage>')
  return draft
}
