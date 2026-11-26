<script setup lang="ts">
import {
  Camera,
  Connection,
  DataLine,
  Document,
  EditPen,
  Grid,
  InfoFilled,
  Link,
  Refresh,
  Monitor,
  VideoCamera,
  VideoPlay,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  useDeviceAppAction,
  useDeviceApps,
  useDeviceInfo,
  useDeviceShell,
  useDeviceText,
  useMirrorStatus,
  useScreenRecordStart,
  useScreenRecordStatus,
  useScreenRecordStop,
  type ScreenRecordLast,
} from '@/api/devices'
import type { Device } from '@/types/api'
import { formatBytes, prettyJson } from '@/lib/format'
import { useUiStore } from '@/stores/ui'
import StatusTag from '@/components/StatusTag.vue'
import RecordingBar from '@/features/mirror/components/RecordingBar.vue'
import MirrorVideoRecorder from '@/features/mirror/components/MirrorVideoRecorder.vue'

const props = defineProps<{
  serial: string
  device: Device | null
  streaming: boolean
  getSource: () => HTMLCanvasElement | null
  getOverlay: () => HTMLCanvasElement | null
}>()

const emit = defineEmits<{
  screenshot: []
  hierarchy: []
  logcat: []
  network: []
  refreshDevices: []
}>()

const router = useRouter()
const ui = useUiStore()

const MANUAL_TABS = [
  { value: 'video', label: '视频', icon: VideoCamera },
  { value: 'record', label: '镜像', icon: VideoPlay },
  { value: 'text', label: '文本', icon: EditPen },
  { value: 'app', label: '应用', icon: Link },
  { value: 'tools', label: '工具', icon: Grid },
] as const

const activeTab = computed({
  get: () => ui.manualTab,
  set: (value) => ui.setManualTab(value),
})

const busy = computed(() => Boolean(props.device?.busy))

// ---------------------------------------------------------------- 文本
const text = ref('')
const clearFirst = ref(false)
const textMutation = useDeviceText(props.serial)

function sendText(): void {
  if (!text.value.trim()) {
    ElMessage.warning('请输入要发送的文本')
    return
  }
  textMutation.mutate(
    { text: text.value, clear_first: clearFirst.value },
    {
      onSuccess: () => {
        ElMessage.success('文本已发送')
        text.value = ''
      },
    },
  )
}

// ---------------------------------------------------------------- 应用
const appAction = ref<'launch' | 'stop' | 'clear' | 'restart'>('launch')
const packageName = ref('')
const activity = ref('')
const appKeyword = ref('')
const appResult = ref<string | null>(null)
const appMutation = useDeviceAppAction(props.serial)
const appsQuery = useDeviceApps(
  computed(() => props.serial),
  {
    keyword: appKeyword,
    enabled: computed(() => activeTab.value === 'app'),
  },
)
const appOptions = computed(() =>
  (appsQuery.data.value ?? []).map((app) => ({
    value: app.package,
    label: app.label ? `${app.label} · ${app.package}` : app.package,
  })),
)

const APP_ACTIONS = [
  { value: 'launch', label: '启动 launch' },
  { value: 'stop', label: '停止 stop' },
  { value: 'clear', label: '清除数据 clear' },
  { value: 'restart', label: '重启 restart' },
] as const

function runApp(): void {
  if (!packageName.value.trim()) {
    ElMessage.warning('请输入 package（如 com.android.settings）')
    return
  }
  appMutation.mutate(
    {
      action: appAction.value,
      package: packageName.value.trim(),
      activity: activity.value.trim() || undefined,
    },
    {
      onSuccess: (data) => {
        appResult.value = prettyJson(data)
        ElMessage.success(`${appAction.value} 已执行`)
      },
    },
  )
}

// ---------------------------------------------------------------- 设备信息 / 录屏 / shell
const infoQuery = useDeviceInfo(computed(() => props.serial))
const mirrorQuery = useMirrorStatus(computed(() => props.serial))
const info = computed<Device | null>(() => infoQuery.data.value ?? props.device)

const recName = ref('')
const recBitRate = ref(4_000_000)
const recTimeLimit = ref(180)
const screenStart = useScreenRecordStart(props.serial)
const screenStop = useScreenRecordStop(props.serial)
const screenStatus = useScreenRecordStatus(
  computed(() => props.serial),
  computed(() => activeTab.value === 'tools'),
)
const recStatus = computed(() => screenStatus.data.value ?? null)
const recActive = computed(() => Boolean(recStatus.value?.active))
const recPending = computed(() => Boolean(recStatus.value?.pending))
const lastVideo = computed<ScreenRecordLast | null>(() => recStatus.value?.last ?? null)

function formatClock(seconds: number | undefined): string {
  const total = Math.max(0, Math.floor(Number(seconds) || 0))
  const minutes = Math.floor(total / 60)
  return `${String(minutes).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

function startScreenRecord(): void {
  screenStart.mutate(
    {
      name: recName.value.trim() || undefined,
      bit_rate: recBitRate.value,
      time_limit_s: recTimeLimit.value,
    },
    {
      onSuccess: () => {
        ElMessage.success('设备录屏已开始')
        void screenStatus.refetch()
      },
    },
  )
}

function stopScreenRecord(): void {
  screenStop.mutate(undefined, {
    onSuccess: () => {
      ElMessage.success('设备录屏已停止，视频已保存')
      void screenStatus.refetch()
    },
  })
}

const command = ref('getprop ro.product.model')
const shellOutput = ref<string | null>(null)
const shellMutation = useDeviceShell(props.serial)

function runShell(): void {
  if (!command.value.trim()) {
    ElMessage.warning('请输入 shell 命令')
    return
  }
  shellMutation.mutate(
    { command: command.value.trim(), timeout_ms: 15000 },
    {
      onSuccess: (data) => {
        shellOutput.value = prettyJson(data)
        ElMessage.success('命令已执行')
      },
    },
  )
}

const mirrorLabel = computed(() => {
  const data = mirrorQuery.data.value
  if (!data) return mirrorQuery.isError.value ? '状态不可用' : '查询中…'
  if (!data.active) return '未镜像'
  return `镜像中 ${data.width ?? '?'}×${data.height ?? '?'}`
})

function goPhysicalRecording(): void {
  void router.push('/recorder?new=physical')
}

function refreshDeviceInfo(): void {
  void infoQuery.refetch()
  void mirrorQuery.refetch()
}
</script>

<template>
  <div class="manual-panel">
    <div class="manual-tabs" role="tablist" aria-label="手动控制功能">
      <button
        v-for="tab in MANUAL_TABS"
        :key="tab.value"
        type="button"
        :class="['manual-tab', { active: activeTab === tab.value }]"
        role="tab"
        :aria-selected="activeTab === tab.value"
        @click="activeTab = tab.value"
      >
        <el-icon :size="13"><component :is="tab.icon" /></el-icon>
        {{ tab.label }}
      </button>
    </div>

    <div class="manual-pages">
      <!-- 视频录制 -->
      <section v-show="activeTab === 'video'" class="manual-page">
        <div class="section-head">
          <h3>
            <el-icon><VideoCamera /></el-icon>视频录制
          </h3>
          <StatusTag tone="blue">浏览器画面</StatusTag>
          <StatusTag tone="amber">刷新即丢失</StatusTag>
        </div>
        <div class="video-warning">
          <el-icon><InfoFilled /></el-icon>
          <span>
            <strong>视频暂不持久化：</strong
            >只保存在当前浏览器内存，刷新或关闭页面即丢失；需要保留请停止后立即下载。
          </span>
        </div>
        <MirrorVideoRecorder
          :serial="serial"
          :get-source="getSource"
          :overlay="getOverlay"
          :enabled="streaming"
        />
        <div class="section-sub">
          录制当前镜像画面，支持暂停/继续；停止后可预览、拖动进度和下载，下载后才会保留。
        </div>
      </section>

      <!-- 镜像录制 -->
      <section v-show="activeTab === 'record'" class="manual-page">
        <div class="section-head">
          <h3>
            <el-icon><EditPen /></el-icon>镜像录制
          </h3>
          <button type="button" class="cell-link" @click="goPhysicalRecording">新建真机录制 →</button>
        </div>
        <div class="section-sub">
          镜像上的点击/滑动会实时出现在下方；停止后转成正式节点。
        </div>
        <RecordingBar :serial="serial" @device-change="emit('refreshDevices')" />
      </section>

      <!-- 文本 -->
      <section v-show="activeTab === 'text'" class="manual-page">
        <div class="section-head">
          <h3>
            <el-icon><EditPen /></el-icon>输入文本
          </h3>
        </div>
        <div class="rec-row">
          <el-input
            v-model="text"
            size="small"
            placeholder="发送到当前焦点输入框"
            :disabled="busy"
            @keyup.enter="sendText"
          />
          <el-button
            size="small"
            :loading="textMutation.isPending.value"
            :disabled="busy || !text.trim()"
            @click="sendText"
          >
            发送
          </el-button>
        </div>
        <div class="rec-row" style="margin-top: 7px">
          <el-checkbox v-model="clearFirst" size="small">发送前清空输入框</el-checkbox>
        </div>
        <div class="section-sub">中文输入需要设备端输入法配合；不确定时先用英文/数字验证链路。</div>
        <div v-if="busy" class="inline-warning">设备被占用：{{ device?.busy_reason ?? '运行/录制中' }}</div>
      </section>

      <!-- 应用 -->
      <section v-show="activeTab === 'app'" class="manual-page">
        <div class="section-head">
          <h3>
            <el-icon><Link /></el-icon>拉起应用
          </h3>
        </div>
        <div class="field-row">
          <label class="field">
            <span>操作</span>
            <el-select v-model="appAction" size="small" style="width: 100%">
              <el-option
                v-for="option in APP_ACTIONS"
                :key="option.value"
                :value="option.value"
                :label="option.label"
              />
            </el-select>
          </label>
          <label class="field">
            <span>package</span>
            <el-select
              v-model="packageName"
              size="small"
              filterable
              remote
              allow-create
              default-first-option
              clearable
              :remote-method="(value: string) => (appKeyword = value)"
              :loading="appsQuery.isFetching.value"
              placeholder="com.android.settings"
              style="width: 100%"
            >
              <el-option
                v-for="option in appOptions"
                :key="option.value"
                :value="option.value"
                :label="option.label"
              />
            </el-select>
          </label>
        </div>
        <label v-if="appAction === 'launch' || appAction === 'restart'" class="field" style="margin-top: 8px">
          <span>activity（可选）</span>
          <el-input v-model="activity" size="small" class="code-text" placeholder=".MainActivity" />
        </label>
        <el-button
          type="primary"
          size="small"
          style="margin-top: 9px"
          :loading="appMutation.isPending.value"
          :disabled="busy"
          @click="runApp"
        >
          执行
        </el-button>
        <div class="section-sub">清数据（clear）会清空应用数据，请谨慎操作。</div>
        <pre v-if="appResult" class="pre-block" style="margin-top: 8px">{{ appResult }}</pre>
      </section>

      <!-- 工具 -->
      <section v-show="activeTab === 'tools'" class="manual-page">
        <div class="section-head">
          <h3>
            <el-icon><Grid /></el-icon>更多工具
          </h3>
          <StatusTag>设备侧</StatusTag>
        </div>
        <div class="tool-list">
          <div class="tool-item">
            <span class="tool-ico"
              ><el-icon><Document /></el-icon
            ></span>
            <div class="tool-main">
              <strong>层级树</strong><small>截屏上叠加元素层级，悬停看信息，点击复制选择器</small>
            </div>
            <el-button size="small" @click="emit('hierarchy')">打开</el-button>
          </div>
          <div class="tool-item">
            <span class="tool-ico"
              ><el-icon><Monitor /></el-icon
            ></span>
            <div class="tool-main">
              <strong>logcat</strong><small>查看最近日志，支持自动刷新与复制</small>
            </div>
            <el-button size="small" @click="emit('logcat')">打开</el-button>
          </div>
          <div class="tool-item">
            <span class="tool-ico"
              ><el-icon><DataLine /></el-icon
            ></span>
            <div class="tool-main">
              <strong>网络抓包</strong><small>只记录访问域名，不含 Header 与正文</small>
            </div>
            <el-button size="small" @click="emit('network')">打开</el-button>
          </div>
          <div class="tool-item">
            <span class="tool-ico"
              ><el-icon><Camera /></el-icon
            ></span>
            <div class="tool-main"><strong>截图</strong><small>抓取当前设备画面 PNG</small></div>
            <el-button size="small" @click="emit('screenshot')">打开</el-button>
          </div>
          <div class="tool-item">
            <span class="tool-ico"
              ><el-icon><VideoCamera /></el-icon
            ></span>
            <div class="tool-main">
              <strong>设备录屏</strong><small>手机端 screenrecord，最长 180s</small>
            </div>
            <el-button size="small" :loading="screenStatus.isFetching.value" @click="screenStatus.refetch()">
              {{ recActive || recPending ? '刷新状态' : '查看状态' }}
            </el-button>
          </div>
        </div>

        <div class="manual-divider" />

        <div class="tool-form">
          <div class="section-head">
            <h3>
              <el-icon><Connection /></el-icon>设备录屏
            </h3>
          </div>
          <div class="field-row three">
            <label class="field"
              ><span>名称（可选）</span><el-input v-model="recName" size="small" placeholder="录屏名称"
            /></label>
            <label class="field">
              <span>码率</span>
              <el-input-number
                v-model="recBitRate"
                size="small"
                :min="1000000"
                :max="20000000"
                :step="500000"
                controls-position="right"
                style="width: 100%"
              />
            </label>
            <label class="field">
              <span>时长上限(s)</span>
              <el-input-number
                v-model="recTimeLimit"
                size="small"
                :min="5"
                :max="180"
                controls-position="right"
                style="width: 100%"
              />
            </label>
          </div>
          <div v-if="recActive || recPending" class="rec-status">
            <span class="rec-dot" :class="{ 'is-live': recActive }" aria-hidden />
            <StatusTag :tone="recActive ? 'red' : 'amber'">{{
              recActive ? '录制中' : '已到时长上限'
            }}</StatusTag>
            <span class="rec-clock"
              >{{ formatClock(recStatus?.elapsed_s) }} / {{ formatClock(recStatus?.time_limit_s) }}</span
            >
          </div>
          <div class="row wrap" style="margin-top: 7px">
            <el-button
              type="primary"
              size="small"
              :loading="screenStart.isPending.value"
              :disabled="busy || recActive || recPending"
              @click="startScreenRecord"
            >
              开始录屏
            </el-button>
            <el-button
              type="danger"
              size="small"
              :loading="screenStop.isPending.value"
              :disabled="!recActive && !recPending"
              @click="stopScreenRecord"
            >
              停止并保存
            </el-button>
          </div>
          <div v-if="lastVideo?.url" class="rec-player">
            <video :src="lastVideo.url" controls preload="metadata" />
            <div class="rec-player-meta">
              <span class="rec-player-name" :title="lastVideo.name">{{ lastVideo.name }}</span>
              <span class="hint">{{ formatBytes(lastVideo.size) }}</span>
              <a :href="lastVideo.url" :download="lastVideo.name">下载</a>
            </div>
          </div>
          <div class="section-sub">
            手机端 screenrecord 把手机画面录成 mp4；不含浏览器特效，也不生成脚本步骤。
          </div>
        </div>

        <div class="manual-divider" />

        <div class="section-head">
          <h3>
            <el-icon><InfoFilled /></el-icon>设备信息
          </h3>
        </div>
        <div class="info-grid">
          <div class="kv">
            <span>serial</span><strong class="code-text">{{ info?.serial ?? serial }}</strong>
          </div>
          <div class="kv">
            <span>型号</span
            ><strong>{{ [info?.brand, info?.model].filter(Boolean).join(' ') || '—' }}</strong>
          </div>
          <div class="kv">
            <span>Android</span
            ><strong>{{ info?.android_version ?? '—' }} (SDK {{ info?.sdk ?? '—' }})</strong>
          </div>
          <div class="kv">
            <span>分辨率</span
            ><strong>{{ info?.screen_width ?? '—' }} × {{ info?.screen_height ?? '—' }}</strong>
          </div>
          <div class="kv">
            <span>状态</span><strong>{{ busy ? (device?.busy_reason ?? '占用中') : '空闲' }}</strong>
          </div>
          <div class="kv">
            <span>镜像</span><strong>{{ mirrorLabel }}</strong>
          </div>
        </div>
        <el-button
          size="small"
          style="margin-top: 7px"
          :icon="Refresh"
          :loading="infoQuery.isFetching.value"
          @click="refreshDeviceInfo"
        >
          刷新信息
        </el-button>

        <div class="manual-divider" />

        <label class="field">
          <span>Shell 命令</span>
          <el-input
            v-model="command"
            size="small"
            class="code-text"
            placeholder="getprop ro.product.model"
            @keyup.enter="runShell"
          />
        </label>
        <el-button
          size="small"
          style="margin-top: 7px"
          :loading="shellMutation.isPending.value"
          @click="runShell"
          >执行</el-button
        >
        <pre v-if="shellOutput" class="pre-block" style="margin-top: 8px">{{ shellOutput }}</pre>
        <div class="section-sub">层级树、logcat 和 shell 都作用于当前镜像设备。</div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.manual-panel {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

.manual-tabs {
  display: flex;
  align-items: stretch;
  gap: 1px;
  flex: none;
  padding: 0 7px;
  overflow-x: auto;
  border-bottom: 1px solid var(--line);
  scrollbar-width: none;
}

.manual-tabs::-webkit-scrollbar {
  display: none;
}

.manual-tab {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex: none;
  height: 38px;
  padding: 0 7px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--muted);
  font-size: 11.5px;
  white-space: nowrap;
  cursor: pointer;
}

.manual-tab .el-icon {
  color: var(--muted-2);
}

.manual-tab:hover {
  background: var(--surface-2);
  color: var(--ink);
}

.manual-tab.active {
  border-bottom-color: var(--accent);
  color: var(--accent);
  font-weight: 650;
}

.manual-tab.active .el-icon {
  color: var(--accent);
}

.manual-pages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.manual-page {
  display: block;
  padding: 12px 12px 16px;
}

.section-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 9px;
}

.section-head h3 {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
  margin: 0 auto 0 0;
  color: var(--ink);
  font-size: 12px;
  font-weight: 650;
  white-space: nowrap;
}

.section-head h3 .el-icon {
  color: var(--muted);
}

.section-head .tag {
  flex: none;
}

.section-sub {
  margin: 7px 0 8px;
  color: var(--muted-2);
  font-size: 10.5px;
  line-height: 1.45;
}

.video-warning {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-bottom: 8px;
  padding: 7px 9px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--amber-soft);
  color: var(--amber);
  font-size: 10.5px;
  line-height: 1.5;
}

.video-warning .el-icon {
  flex: none;
  margin-top: 1px;
}

.video-warning strong {
  font-weight: 650;
}

.rec-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.rec-row .el-input {
  flex: 1;
  min-width: 120px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.field > span {
  color: var(--muted);
  font-size: 10.5px;
  font-weight: 500;
}

.field + .field {
  margin-top: 8px;
}

.field-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.field-row.three {
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
}

.rec-status {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin: 5px 0 8px;
  padding: 6px 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface-2);
}

.inline-warning {
  margin-top: 7px;
  padding: 6px 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--amber-soft);
  color: var(--amber);
  font-size: 10.5px;
}

.manual-divider {
  height: 1px;
  margin: 15px 0 13px;
  background: var(--line-soft);
}

.manual-link-row {
  display: flex;
  margin-top: 4px;
}

.manual-link-row .cell-link {
  color: var(--accent);
  font-size: 11.5px;
}

.manual-link-row .cell-link:hover {
  color: var(--accent-hover);
  text-decoration: underline;
}

.tool-list {
  display: flex;
  flex-direction: column;
}

.tool-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px 3px;
  border-bottom: 1px solid var(--line-soft);
}

.tool-item:last-child {
  border-bottom: 0;
}

.tool-ico {
  display: grid;
  place-items: center;
  flex: none;
  width: 28px;
  height: 28px;
  border-radius: 7px;
  background: var(--surface-3);
  color: var(--muted);
}

.tool-main {
  flex: 1;
  min-width: 0;
}

.tool-main strong {
  display: block;
  overflow: hidden;
  color: var(--ink);
  font-size: 11.5px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-main small {
  display: block;
  margin-top: 1px;
  color: var(--muted-2);
  font-size: 10px;
  line-height: 1.4;
}

.tool-item .el-button {
  flex: none;
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 0 12px;
}

.info-grid .kv {
  min-width: 0;
}

.info-grid .kv > strong {
  font-family: var(--mono);
  font-size: 11px;
}

.pre-block {
  width: 100%;
  max-height: 220px;
}

/* 嵌入的录制卡片去掉自己的边框与标题，融进手动控制页。 */
.manual-page :deep(.mirror-recording) {
  margin: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}

.manual-page :deep(.mirror-recording > .card-head) {
  display: none;
}

.manual-page :deep(.mirror-recording > .card-body) {
  padding: 0;
}

.manual-page :deep(.mirror-recording .el-alert) {
  margin-top: 8px;
}

.manual-page :deep(.mirror-recording .mirror-video-preview) {
  max-height: 300px;
}

.manual-page :deep(.code-text .el-input__inner) {
  font-family: var(--mono);
}

@media (max-width: 640px) {
  .manual-page {
    padding: 9px 9px 14px;
  }

  .field-row {
    grid-template-columns: minmax(0, 1fr);
  }

  .info-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .tool-main small {
    display: none;
  }
}
</style>
