<script setup lang="ts">
import {
  ArrowLeft,
  Bell,
  Delete,
  Document,
  Folder,
  Grid,
  HomeFilled,
  Menu,
  TurnOff,
  Promotion,
  Refresh,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, ref } from 'vue'

import {
  useDeviceAppAction,
  useDeviceKey,
  useDeviceShell,
  useDeviceText,
  useMirrorStatus,
  useScreenRecordStart,
  useScreenRecordStop,
} from '@/api/devices'
import type { Device } from '@/types/api'
import { numberOrNull, prettyJson } from '@/lib/format'
import StatusTag from '@/components/StatusTag.vue'

const props = defineProps<{ device: Device }>()
const activeTab = ref('keys')

const keyMutation = useDeviceKey(props.device.serial)
const textMutation = useDeviceText(props.device.serial)
const shellMutation = useDeviceShell(props.device.serial)
const appMutation = useDeviceAppAction(props.device.serial)
const screenStart = useScreenRecordStart(props.device.serial)
const screenStop = useScreenRecordStop(props.device.serial)
const mirrorQuery = useMirrorStatus(computed(() => props.device.serial))

const text = ref('')
const clearFirst = ref(false)
const command = ref('getprop ro.product.model')
const shellOutput = ref<string | null>(null)

const appAction = ref<'launch' | 'stop' | 'clear' | 'restart'>('launch')
const packageName = ref('')
const activity = ref('')
const appResult = ref<string | null>(null)

const recName = ref('')
const recBitRate = ref(4_000_000)
const recTimeLimit = ref(180)
const recResult = ref<string | null>(null)

const QUICK_KEYS = [
  { key: 'back', label: '返回', icon: ArrowLeft },
  { key: 'home', label: '桌面', icon: HomeFilled },
  { key: 'recents', label: '最近', icon: Grid },
  { key: 'menu', label: '菜单', icon: Menu },
  { key: 'notification', label: '通知', icon: Bell },
  { key: 'volume_up', label: '音量+', icon: Promotion },
  { key: 'volume_down', label: '音量-', icon: Promotion },
  { key: 'power', label: '电源', icon: TurnOff },
  { key: 'enter', label: '回车', icon: Document },
  { key: 'delete', label: '删除', icon: Delete },
]

const APP_ACTIONS = [
  { value: 'launch', label: '启动 launch' },
  { value: 'stop', label: '停止 stop' },
  { value: 'clear', label: '清数据 clear' },
  { value: 'restart', label: '重启 restart' },
]

const busy = computed(() => props.device.busy)

function sendKey(key: string, label: string): void {
  keyMutation.mutate(
    { key },
    { onSuccess: () => ElMessage.success(`已发送：${label}`) },
  )
}

function sendText(): void {
  if (!text.value) {
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

function startScreenRecord(): void {
  screenStart.mutate(
    {
      name: recName.value.trim() || undefined,
      bit_rate: recBitRate.value,
      time_limit_s: recTimeLimit.value,
    },
    {
      onSuccess: (data) => {
        recResult.value = prettyJson(data)
        ElMessage.success('设备录屏已开始')
      },
    },
  )
}

function stopScreenRecord(): void {
  screenStop.mutate(undefined, {
    onSuccess: (data) => {
      recResult.value = prettyJson(data)
      ElMessage.success('设备录屏已停止')
    },
  })
}

const mirrorLabel = computed(() => {
  const data = mirrorQuery.data.value
  if (!data) return mirrorQuery.isError.value ? '状态不可用' : '查询中…'
  if (!data.active) return '未镜像'
  return `镜像中 ${data.width ?? '?'}×${data.height ?? '?'}`
})
</script>

<template>
  <el-tabs v-model="activeTab" class="control-tabs">
    <el-tab-pane label="按键" name="keys">
      <div class="key-grid">
        <button
          v-for="item in QUICK_KEYS"
          :key="item.key"
          type="button"
          class="key-btn"
          :disabled="keyMutation.isPending.value || busy"
          :title="item.label"
          @click="sendKey(item.key, item.label)"
        >
          <el-icon :size="14"><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </button>
      </div>
      <el-alert
        v-if="busy"
        type="warning"
        :closable="false"
        show-icon
        :title="`设备被占用：${device.busy_reason ?? '运行/录制中'}`"
        style="margin-top: 8px"
      />
    </el-tab-pane>

    <el-tab-pane label="文本" name="text">
      <div class="ctrl-field">
        <label>文本输入</label>
        <div class="ctrl-actions">
          <el-input
            v-model="text"
            size="small"
            placeholder="发送到当前焦点输入框"
            :disabled="busy"
            @keyup.enter="sendText"
          />
          <el-button size="small" :loading="textMutation.isPending.value" :disabled="busy" @click="sendText">
            发送
          </el-button>
        </div>
        <div class="row" style="margin-top: 5px">
          <el-switch v-model="clearFirst" size="small" />
          <span class="hint">发送前清空输入框；中文需设备端输入法配合</span>
        </div>
      </div>
      <div class="divider" />
      <div class="ctrl-field">
        <label>Shell 命令</label>
        <div class="ctrl-actions">
          <el-input v-model="command" size="small" class="code-text" placeholder="adb shell 命令（不含前缀）" @keyup.enter="runShell" />
          <el-button size="small" :loading="shellMutation.isPending.value" @click="runShell">执行</el-button>
        </div>
      </div>
      <el-alert
        v-if="shellMutation.isError.value"
        type="error"
        :closable="false"
        show-icon
        title="Shell 执行失败"
        style="margin-bottom: 8px"
      />
      <pre v-if="shellOutput" class="pre-block">{{ shellOutput }}</pre>
    </el-tab-pane>

    <el-tab-pane label="应用" name="apps">
      <div class="ctrl-field">
        <label>应用操作</label>
        <el-select v-model="appAction" size="small" style="width: 100%">
          <el-option v-for="option in APP_ACTIONS" :key="option.value" :value="option.value" :label="option.label" />
        </el-select>
      </div>
      <div class="ctrl-field">
        <label>package</label>
        <el-input v-model="packageName" size="small" class="code-text" placeholder="com.example.app" />
      </div>
      <div v-if="appAction === 'launch' || appAction === 'restart'" class="ctrl-field">
        <label>activity（可选）</label>
        <el-input v-model="activity" size="small" class="code-text" placeholder=".MainActivity" />
      </div>
      <el-button type="primary" size="small" :loading="appMutation.isPending.value" :disabled="busy" @click="runApp">
        执行
      </el-button>
      <div class="hint" style="margin-top: 6px">清数据（clear）会清空应用数据，请谨慎操作。</div>
      <pre v-if="appResult" class="pre-block" style="margin-top: 8px">{{ appResult }}</pre>
    </el-tab-pane>

    <el-tab-pane label="录屏" name="screenrecord">
      <div class="ctrl-field">
        <label>名称（可选）</label>
        <el-input v-model="recName" size="small" placeholder="录屏名称" />
      </div>
      <div class="ctrl-field">
        <label>码率 bit_rate</label>
        <el-input-number
          v-model="recBitRate"
          size="small"
          :min="1000000"
          :max="20000000"
          :step="500000"
          controls-position="right"
          style="width: 100%"
        />
      </div>
      <div class="ctrl-field">
        <label>时长上限 time_limit_s</label>
        <el-input-number v-model="recTimeLimit" size="small" :min="5" :max="180" controls-position="right" style="width: 100%" />
      </div>
      <div class="row">
        <el-button type="primary" size="small" :loading="screenStart.isPending.value" :disabled="busy" @click="startScreenRecord">
          开始录屏
        </el-button>
        <el-button type="danger" size="small" :loading="screenStop.isPending.value" @click="stopScreenRecord">
          停止并保存
        </el-button>
      </div>
      <div class="hint" style="margin-top: 6px">设备端 screenrecord（最长 180s），产物保存为 video artifact。</div>
      <pre v-if="recResult" class="pre-block" style="margin-top: 8px">{{ recResult }}</pre>
    </el-tab-pane>

    <el-tab-pane label="信息" name="info">
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="serial"><span class="code-text">{{ device.serial }}</span></el-descriptions-item>
        <el-descriptions-item label="状态">
          <StatusTag :tone="device.state === 'device' ? 'green' : 'red'">{{ device.state }}</StatusTag>
        </el-descriptions-item>
        <el-descriptions-item label="型号">
          {{ `${device.brand ?? ''} ${device.model ?? ''}`.trim() || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="Android">
          {{ device.android_version ?? '—' }} (SDK {{ device.sdk ?? '—' }})
        </el-descriptions-item>
        <el-descriptions-item label="分辨率">
          {{ device.screen_width }} × {{ device.screen_height }} · 旋转 {{ device.rotation }}°
        </el-descriptions-item>
        <el-descriptions-item label="占用">
          <StatusTag :tone="device.busy ? 'red' : 'green'">{{ device.busy ? device.busy_reason ?? '占用中' : '空闲' }}</StatusTag>
        </el-descriptions-item>
        <el-descriptions-item label="镜像">
          <StatusTag :tone="mirrorQuery.data.value?.active ? 'blue' : 'gray'">{{ mirrorLabel }}</StatusTag>
          <el-button size="small" text :icon="Refresh" :loading="mirrorQuery.isFetching.value" @click="mirrorQuery.refetch()" />
        </el-descriptions-item>
      </el-descriptions>
    </el-tab-pane>
  </el-tabs>
</template>
