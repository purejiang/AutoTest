<script setup lang="ts">
import {
  CopyDocument,
  Delete,
  Download,
  MoreFilled,
  Plus,
  Refresh,
  Search,
  Upload,
  VideoPlay,
} from '@element-plus/icons-vue'
import { useDebounceFn } from '@vueuse/core'
import { ElMessage, ElMessageBox, type TableInstance } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { describeApiError } from '@/api/client'
import { useRuns } from '@/api/runs'
import {
  exportScript,
  useBulkDeleteScripts,
  useCreateScript,
  useDeleteScript,
  useDuplicateScript,
  useImportScript,
  useScripts,
} from '@/api/scripts'
import { formatShortDateTime, safeParseJson, sourceMeta } from '@/lib/format'
import { notifyBulkDeleteResult } from '@/lib/bulk'
import type { Recording, Script } from '@/types/api'
import StatusTag from '@/components/StatusTag.vue'

import { scriptStepCount } from './nodeDefaults'
import RunHistoryPanel from './RunHistoryPanel.vue'
import CreateRecordingDialog from '@/features/recorder/CreateRecordingDialog.vue'

type ScriptTab = 'library' | 'runs'

const router = useRouter()
const route = useRoute()
const PAGE_SIZE = 20

const activeTab = computed<ScriptTab>(() => (route.query.tab === 'runs' ? 'runs' : 'library'))

/**
 * 缺省 / 非法 tab 归一到 script library，用 replace 避免污染浏览器历史。
 * tab 切换走 push，因此浏览器前进/后退能回到对应 tab。
 */
watch(
  () => route.query.tab,
  (tab) => {
    if (tab === 'library' || tab === 'runs') return
    void router.replace({ path: '/scripts', query: { ...route.query, tab: 'library' } })
  },
  { immediate: true },
)

function switchTab(tab: ScriptTab): void {
  if (activeTab.value === tab) return
  void router.push({ path: '/scripts', query: { ...route.query, tab } })
}

const searchInput = ref('')
const search = ref('')
const page = ref(1)
const updateSearch = useDebounceFn((value: string) => {
  search.value = value.trim()
  page.value = 1
}, 250)

// tab 数量来自接口 total：脚本用 limit 1 单独取总数，不受当前搜索/分页影响。
const scriptsCountQuery = useScripts(computed(() => ({ limit: 1 })))
const scriptsQuery = useScripts(
  computed(() => ({ query: search.value, limit: PAGE_SIZE, offset: (page.value - 1) * PAGE_SIZE })),
)
const runsCountQuery = useRuns(computed(() => ({ limit: 1 })), { refetchInterval: false })
const createMutation = useCreateScript()
const duplicateMutation = useDuplicateScript()
const deleteMutation = useDeleteScript()
const importMutation = useImportScript()

const scriptTotal = computed(() => scriptsCountQuery.data.value?.total ?? 0)
const runTotal = computed(() => runsCountQuery.data.value?.total ?? 0)
const scripts = computed(() => scriptsQuery.data.value?.items ?? [])
const filteredTotal = computed(() => scriptsQuery.data.value?.total ?? 0)

// 进入运行记录 tab（含浏览器前进/后退）时补一次运行总数，避免 tab 数字过期。
watch(activeTab, (tab) => {
  if (tab === 'runs') void runsCountQuery.refetch()
})

const manualOpen = ref(false)
const manualName = ref('')
const manualNotes = ref('')
const physicalOpen = ref(false)
const importInput = ref<HTMLInputElement | null>(null)
const scriptsTableRef = ref<TableInstance>()
const selectedScripts = ref<Script[]>([])
const bulkDeleteMutation = useBulkDeleteScripts()

function handleSelectionChange(rows: Script[]): void {
  selectedScripts.value = rows
}

function clearSelection(): void {
  scriptsTableRef.value?.clearSelection()
  selectedScripts.value = []
}

function onSearchInput(value: string): void {
  clearSelection()
  void updateSearch(value)
}

function onRowClick(row: Script, column?: { type?: string }): void {
  if (column?.type === 'selection') return
  void router.push(`/scripts/${row.id}`)
}

async function refreshScripts(): Promise<void> {
  clearSelection()
  await Promise.all([scriptsQuery.refetch(), scriptsCountQuery.refetch()])
}

async function bulkDeleteScripts(): Promise<void> {
  const ids = selectedScripts.value.map((script) => script.id)
  if (ids.length === 0) return
  try {
    await ElMessageBox.confirm(
      `删除选中的 ${ids.length} 个脚本？删除后不可恢复，但不会删除历史运行记录；单条失败不会阻塞其他条。`,
      '批量删除脚本',
      { type: 'warning', confirmButtonText: '删除选中', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const result = await bulkDeleteMutation.mutateAsync(ids)
    notifyBulkDeleteResult(result, '脚本')
    clearSelection()
    await scriptsQuery.refetch()
  } catch {
    // 全局错误提示
  }
}

watch([page, search], () => clearSelection())

function openManual(): void {
  manualName.value = ''
  manualNotes.value = ''
  manualOpen.value = true
}

function runInConsole(script: Script): void {
  void router.push({ path: '/console', query: { tab: 'auto', script: script.id } })
}

function openMirrorRecord(): void {
  void router.push({ path: '/console', query: { tab: 'manual', manual: 'record' } })
}

function handleCreateCommand(command: string): void {
  if (command === 'manual') {
    openManual()
  } else if (command === 'mirror') {
    openMirrorRecord()
  } else if (command === 'physical') {
    physicalOpen.value = true
  }
}

async function createManual(): Promise<void> {
  const name = manualName.value.trim()
  if (!name) {
    ElMessage.warning('请输入脚本名称')
    return
  }
  try {
    const script = await createMutation.mutateAsync({ name, notes: manualNotes.value.trim() })
    ElMessage.success('脚本已创建')
    manualOpen.value = false
    await router.push(`/scripts/${script.id}`)
  } catch {
    // 全局错误提示
  }
}

function onPhysicalCreated(recording: Recording): void {
  void router.push(`/recorder/${recording.id}`)
}

async function handleRowCommand(command: string, script: Script): Promise<void> {
  if (command === 'run') {
    runInConsole(script)
    return
  }
  if (command === 'duplicate') {
    try {
      const created = await duplicateMutation.mutateAsync({ id: script.id })
      ElMessage.success(`已复制为「${created.name}」`)
      await router.push(`/scripts/${created.id}`)
    } catch {
      // 全局错误提示
    }
    return
  }
  if (command === 'export') {
    exportScript(script.id, script.name)
    return
  }
  if (command === 'delete') {
    try {
      await ElMessageBox.confirm(
        `删除「${script.name || script.id}」后不可恢复（不会删除历史运行记录）。`,
        '删除脚本',
        { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    try {
      await deleteMutation.mutateAsync(script.id)
      ElMessage.success(`已删除「${script.name || script.id}」`)
      clearSelection()
    } catch {
      // 全局错误提示
    }
  }
}

function triggerImport(): void {
  importInput.value?.click()
}

async function handleImportFile(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  let text: string
  try {
    text = await file.text()
  } catch {
    ElMessage.error('读取文件失败')
    return
  }
  const parsed = safeParseJson<unknown>(text)
  if (!parsed.ok) {
    ElMessage.error(`JSON 解析失败：${parsed.error}`)
    return
  }
  try {
    const script = await importMutation.mutateAsync({ data: parsed.value })
    ElMessage.success(`导入成功：${script.name}`)
    await router.push(`/scripts/${script.id}`)
  } catch {
    // 全局错误提示
  }
}
</script>

<template>
  <div class="page scripts-page">
    <div class="scripts-head">
      <div class="seg" role="tablist" aria-label="脚本视图">
        <button
          type="button"
          role="tab"
          :class="{ active: activeTab === 'library' }"
          :aria-selected="activeTab === 'library'"
          @click="switchTab('library')"
        >
          脚本库<span class="tab-count">{{ scriptTotal }}</span>
        </button>
        <button
          type="button"
          role="tab"
          :class="{ active: activeTab === 'runs' }"
          :aria-selected="activeTab === 'runs'"
          @click="switchTab('runs')"
        >
          运行记录<span class="tab-count">{{ runTotal }}</span>
        </button>
      </div>
      <span class="spacer" />

      <div v-if="activeTab === 'library'" class="scripts-actions">
        <el-input
          v-model="searchInput"
          placeholder="搜索脚本"
          clearable
          :prefix-icon="Search"
          style="width: 210px"
          @update:model-value="onSearchInput"
        />
        <el-tooltip content="导入 JSON" placement="bottom">
          <el-button :icon="Upload" :loading="importMutation.isPending.value" @click="triggerImport" />
        </el-tooltip>
        <el-tooltip content="刷新脚本列表" placement="bottom">
          <el-button :icon="Refresh" :loading="scriptsQuery.isFetching.value" @click="refreshScripts" />
        </el-tooltip>
        <el-dropdown split-button type="primary" trigger="click" @click="openManual" @command="handleCreateCommand">
          <el-icon style="margin-right: 4px"><Plus /></el-icon>新建脚本
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="manual">手动创建</el-dropdown-item>
              <el-dropdown-item command="mirror">从镜像录制</el-dropdown-item>
              <el-dropdown-item command="physical">从真机录制</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <input ref="importInput" type="file" accept=".json,application/json" hidden @change="handleImportFile" />
      </div>
    </div>

    <template v-if="activeTab === 'library'">
      <el-alert
        v-if="scriptsQuery.isError.value"
        type="error"
        :closable="false"
        show-icon
        title="脚本列表加载失败"
      >
        <template #default>
          <div class="row wrap">
            <span>{{ describeApiError(scriptsQuery.error.value) }}</span>
            <el-button size="small" @click="refreshScripts">重试</el-button>
          </div>
        </template>
      </el-alert>

      <div class="card table-card">
        <div class="library-toolbar">
          <span class="hint">脚本是可编辑的编排定义；运行记录是脚本在设备上的一次执行。</span>
        </div>
        <div v-if="selectedScripts.length > 0" class="batch-bar">
          <strong>已选 {{ selectedScripts.length }} 个</strong>
          <span class="small">删除脚本不会删除历史运行记录</span>
          <span class="spacer" />
          <el-button
            size="small"
            type="danger"
            :loading="bulkDeleteMutation.isPending.value"
            @click="bulkDeleteScripts"
          >
            删除选中
          </el-button>
          <el-button size="small" @click="clearSelection">取消</el-button>
        </div>
        <el-table
          ref="scriptsTableRef"
          v-loading="scriptsQuery.isLoading.value"
          :data="scripts"
          row-key="id"
          :show-header="true"
          @selection-change="handleSelectionChange"
          @row-click="onRowClick"
        >
          <el-table-column type="selection" width="36" />
          <el-table-column label="名称 / ID" min-width="260">
            <template #default="{ row }">
              <button type="button" class="cell-link" @click.stop="router.push(`/scripts/${row.id}`)">
                {{ row.name || row.id }}
              </button>
              <div class="cell-sub">{{ row.id }}</div>
            </template>
          </el-table-column>
          <el-table-column label="节点" width="76">
            <template #default="{ row }">{{ scriptStepCount(row) }}</template>
          </el-table-column>
          <el-table-column label="来源" width="104">
            <template #default="{ row }">
              <StatusTag :tone="sourceMeta(row.source).tone">{{ sourceMeta(row.source).label }}</StatusTag>
            </template>
          </el-table-column>
          <el-table-column label="来源录制" width="150">
            <template #default="{ row }">
              <button
                v-if="row.origin_recording_id"
                type="button"
                class="cell-link code-text"
                @click.stop="router.push(`/recorder/${row.origin_recording_id}`)"
              >
                {{ row.origin_recording_id }}
              </button>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="更新时间" width="150">
            <template #default="{ row }"><span class="small muted">{{ formatShortDateTime(row.updated_at) }}</span></template>
          </el-table-column>
          <el-table-column label="操作" width="176" align="right">
            <template #default="{ row }">
              <div class="actions-cell" @click.stop>
                <el-button link type="primary" @click="router.push(`/scripts/${row.id}`)">编辑</el-button>
                <el-button link type="primary" :icon="VideoPlay" @click="runInConsole(row)">运行</el-button>
                <el-dropdown trigger="click" @command="(command: string) => handleRowCommand(command, row)">
                  <el-button link :icon="MoreFilled" title="更多" />
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item command="duplicate" :icon="CopyDocument">复制</el-dropdown-item>
                      <el-dropdown-item command="export" :icon="Download">导出</el-dropdown-item>
                      <el-dropdown-item command="delete" :icon="Delete" divided>删除</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </div>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty :description="search ? '未找到匹配的脚本' : '还没有脚本，手动新建或从录制生成'">
              <div class="row" style="justify-content: center">
                <el-button type="primary" :icon="Plus" @click="openManual">手动创建</el-button>
                <el-button :icon="Upload" @click="triggerImport">导入 JSON</el-button>
              </div>
            </el-empty>
          </template>
        </el-table>
        <div class="table-foot">
          <span>共 {{ filteredTotal }} 个脚本</span>
          <el-pagination
            v-model:current-page="page"
            :page-size="PAGE_SIZE"
            :total="filteredTotal"
            layout="prev, pager, next"
            small
            background
          />
        </div>
      </div>
    </template>

    <RunHistoryPanel v-else @browse-library="switchTab('library')" />

    <el-dialog v-model="manualOpen" title="新建脚本" width="520px" append-to-body>
      <el-form label-position="top" size="small">
        <el-form-item label="脚本名称" required>
          <el-input v-model="manualName" placeholder="例如：登录并打开设置" maxlength="200" @keyup.enter="createManual" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="manualNotes" type="textarea" :rows="2" placeholder="可选" />
        </el-form-item>
        <div class="hint">创建后进入编辑器，默认带一个 click 节点（由后端 empty_script 提供）。</div>
      </el-form>
      <template #footer>
        <el-button @click="manualOpen = false">取消</el-button>
        <el-button type="primary" :loading="createMutation.isPending.value" @click="createManual">创建并编辑</el-button>
      </template>
    </el-dialog>

    <CreateRecordingDialog
      :open="physicalOpen"
      mode="physical"
      @close="physicalOpen = false"
      @created="onPhysicalCreated"
    />
  </div>
</template>

<style scoped>
.scripts-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.scripts-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.seg {
  display: inline-flex;
  padding: 2px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-2);
}

.seg button {
  height: 24px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  font-size: 11.5px;
  white-space: nowrap;
  cursor: pointer;
}

.seg button:hover {
  color: var(--ink);
}

.seg button.active {
  background: var(--surface);
  color: var(--ink);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
}

.tab-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 16px;
  margin-left: 5px;
  padding: 0 4px;
  border-radius: 8px;
  background: var(--surface-3);
  color: var(--muted);
  font-family: var(--mono);
  font-size: 9px;
  font-weight: 600;
  line-height: 1;
}

.seg button.active .tab-count {
  background: var(--accent-soft);
  color: var(--accent);
}

.library-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
}
</style>
