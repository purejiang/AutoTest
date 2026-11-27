<script setup lang="ts">
// 设置页容器：tab 与 URL 同步、草稿状态（provide 给各 tab）、未保存操作条、
// 快捷键 / 离开确认。各 tab 的界面在 ./tabs/ 下，共用 ./settings.css。
import { Check, MoreFilled, Refresh, RefreshLeft } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'

import { describeApiError } from '@/api/client'
import { useSettings } from '@/api/settings'
import StatusTag from '@/components/StatusTag.vue'
import AppTabs from '@/components/AppTabs.vue'

import AboutTab from './tabs/AboutTab.vue'
import ExecutionTab from './tabs/ExecutionTab.vue'
import GeneralTab from './tabs/GeneralTab.vue'
import StorageTab from './tabs/StorageTab.vue'
import ToolchainTab from './tabs/ToolchainTab.vue'
import { provideSettingsDraft, useSettingsDraft } from './useSettingsDraft'
import './settings.css'

type SettingsTab = 'general' | 'toolchain' | 'execution' | 'storage' | 'about'

const TABS: Array<{ key: SettingsTab; label: string }> = [
  { key: 'general', label: '通用' },
  { key: 'toolchain', label: '工具链' },
  { key: 'execution', label: '执行' },
  { key: 'storage', label: '存储' },
  { key: 'about', label: '关于' },
]

const route = useRoute()
const router = useRouter()

const settingsQuery = useSettings()
const draft = useSettingsDraft()
provideSettingsDraft(draft)

const {
  changedKeys,
  showSavebar,
  showSavedTag,
  hasOverrides,
  overrideCount,
  overrideTooltip,
  saveSettings,
  discardChanges,
  resetSettings,
  reloadSettings,
  onMoreCommand,
  savePending,
  resetPending,
} = draft

const settings = computed(() => settingsQuery.data.value ?? null)

function isSettingsTab(value: unknown): value is SettingsTab {
  return typeof value === 'string' && TABS.some((tab) => tab.key === value)
}

// tab 与 URL 同步，便于刷新/深链回测；非法值归一到通用。
const activeTab = computed<SettingsTab>({
  get: () => (isSettingsTab(route.query.tab) ? route.query.tab : 'general'),
  set: (tab) => {
    if (route.query.tab === tab) return
    void router.replace({ path: '/settings', query: { ...route.query, tab } })
  },
})

function onTabChange(key: string): void {
  if (isSettingsTab(key)) activeTab.value = key
}

function onGlobalKeydown(event: KeyboardEvent): void {
  if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === 's') {
    event.preventDefault()
    if (showSavebar.value && !savePending.value) void saveSettings()
  }
}

function onBeforeUnload(event: BeforeUnloadEvent): void {
  if (!showSavebar.value) return
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => {
  window.addEventListener('keydown', onGlobalKeydown)
  window.addEventListener('beforeunload', onBeforeUnload)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKeydown)
  window.removeEventListener('beforeunload', onBeforeUnload)
})

onBeforeRouteLeave(async () => {
  if (!showSavebar.value) return true
  try {
    await ElMessageBox.confirm(
      `有 ${changedKeys.value.length} 项未保存的改动，离开后会丢失。`,
      '离开设置',
      { type: 'warning', confirmButtonText: '离开', cancelButtonText: '继续编辑' },
    )
    return true
  } catch {
    return false
  }
})
</script>

<template>
  <div class="page">
    <h1 class="visually-hidden">设置</h1>
    <div class="settings-actions">
      <span class="settings-priority">配置优先级：默认 &lt; 配置文件 &lt; 环境变量</span>
      <span class="spacer" />
      <StatusTag v-if="showSavedTag" tone="green" dot>已保存</StatusTag>
      <el-tooltip
        v-if="hasOverrides"
        :content="overrideTooltip"
        placement="bottom-end"
        popper-class="settings-override-tooltip"
      >
        <StatusTag tone="blue">{{ overrideCount }} 项使用覆盖值</StatusTag>
      </el-tooltip>
      <el-dropdown trigger="click" placement="bottom-end" @command="onMoreCommand">
        <el-button
          size="small"
          class="page-more"
          :icon="MoreFilled"
          title="更多设置操作"
          aria-label="更多设置操作"
        />
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="reset" :icon="RefreshLeft" :disabled="resetPending">
              恢复默认
            </el-dropdown-item>
            <el-dropdown-item command="reload" :icon="Refresh">重新载入配置文件</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>

    <div v-if="settingsQuery.isLoading.value" class="card">
      <div class="card-body"><el-skeleton :rows="8" animated /></div>
    </div>
    <el-result
      v-else-if="settingsQuery.isError.value || !settings"
      icon="warning"
      title="设置加载失败"
      :sub-title="describeApiError(settingsQuery.error.value)"
    >
      <template #extra>
        <el-button type="primary" @click="settingsQuery.refetch()">重试</el-button>
      </template>
    </el-result>

    <div v-else class="settings-layout">
      <aside class="card settings-nav">
        <AppTabs
          :model-value="activeTab"
          :items="TABS"
          variant="sidebar"
          aria-label="设置分组"
          @update:model-value="onTabChange"
        />
        <div class="settings-note" title="配置优先级：默认 &lt; 配置文件 &lt; 环境变量">
          外观仅本机 · 其余保存后生效
        </div>
      </aside>

      <div class="settings-main" :class="{ 'has-unsaved': showSavebar }">
        <GeneralTab v-show="activeTab === 'general'" />
        <ToolchainTab v-show="activeTab === 'toolchain'" />
        <ExecutionTab v-show="activeTab === 'execution'" />
        <StorageTab v-show="activeTab === 'storage'" />
        <AboutTab v-show="activeTab === 'about'" />

        <!-- ============================================================ 未保存操作条 -->
        <div v-if="showSavebar" class="savebar" role="region" aria-label="未保存的设置更改">
          <div class="savebar-summary">
            <div class="savebar-title">
              <span class="savebar-dot" aria-hidden="true" />
              未保存 {{ changedKeys.length }} 项
            </div>
          </div>
          <div class="savebar-actions">
            <el-button size="small" :disabled="savePending" @click="discardChanges">放弃更改</el-button>
            <el-button
              type="primary"
              size="small"
              :icon="Check"
              :loading="savePending"
              title="保存更改（⌘S / Ctrl+S）"
              aria-keyshortcuts="Control+S Meta+S"
              @click="saveSettings"
            >
              保存更改
            </el-button>
            <el-dropdown
              trigger="click"
              placement="top-end"
              :disabled="savePending"
              @command="onMoreCommand"
            >
              <el-button
                size="small"
                class="savebar-more"
                :icon="MoreFilled"
                title="更多：恢复默认 / 重新载入配置文件"
                aria-label="更多设置操作"
              />
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="reset" :icon="RefreshLeft" :disabled="resetPending">
                    恢复默认
                  </el-dropdown-item>
                  <el-dropdown-item command="reload" :icon="Refresh">重新载入配置文件</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 页头常驻的「更多」入口：无未保存改动时也能恢复默认 / 重新加载。 */
.page-more {
  width: 26px;
  padding: 0;
  margin-left: 2px;
}

.settings-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-height: 30px;
}

.settings-actions .spacer {
  flex: 1;
}

.settings-priority {
  color: var(--muted-2);
  font-size: 10px;
  line-height: 1.4;
}

.settings-layout {
  display: grid;
  grid-template-columns: 184px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.settings-nav {
  position: sticky;
  top: calc(var(--top) + 16px);
  padding: 6px;
}

.settings-note {
  margin-top: 8px;
  padding: 8px 9px;
  border-top: 1px solid var(--line-soft);
  color: var(--muted-2);
  font-size: 10px;
  line-height: 1.5;
}

.settings-main {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.settings-main.has-unsaved {
  min-height: calc(100vh - var(--top) - 84px);
}

/* 给 sticky 操作条预留滚动余量，滚到末尾时最后一张卡片/alert 不会被盖住。 */
.settings-main.has-unsaved :deep(.settings-pane) {
  padding-bottom: 96px;
}

/* ---- 未保存操作条 */
.savebar {
  position: sticky;
  bottom: 12px;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: auto;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface);
  box-shadow: var(--shadow-md);
}

.savebar-summary {
  min-width: 0;
}

.savebar-title {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--amber);
  font-size: 12px;
  font-weight: 650;
  line-height: 1.3;
}

.savebar-dot {
  flex: none;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--amber);
  box-shadow: 0 0 0 3px var(--amber-soft);
}

.savebar-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
  margin-left: auto;
}

.savebar-actions :deep(.el-button) {
  height: 28px;
  padding: 0 11px;
  margin-left: 0;
  font-size: 11.5px;
}

.savebar-actions :deep(.savebar-more) {
  width: 28px;
  padding: 0;
}

@media (max-width: 900px) {
  .settings-layout {
    grid-template-columns: 1fr;
  }

  .settings-nav {
    position: static;
    display: flex;
    gap: 3px;
    overflow-x: auto;
    padding: 5px;
  }

  .settings-note {
    display: none;
  }
}

@media (max-width: 640px) {
  .settings-priority {
    display: none;
  }

  /* 窄屏操作条可能折成两行，多留一些滚动余量。 */
  .settings-main.has-unsaved :deep(.settings-pane) {
    padding-bottom: 128px;
  }

  .savebar {
    gap: 8px;
    padding: 10px;
  }
}
</style>
