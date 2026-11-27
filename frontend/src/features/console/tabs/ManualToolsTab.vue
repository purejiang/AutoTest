<script setup lang="ts">
// 手动控制 → 工具：层级树 / logcat / 网络抓包 / 设备信息抽屉 + shell 命令。
import { DataLine, Document, Grid, InfoFilled, Monitor, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, ref } from 'vue'

import { useDeviceInfo, useDeviceShell, useMirrorStatus } from '@/api/devices'
import type { Device } from '@/types/api'
import { prettyJson } from '@/lib/format'
import StatusTag from '@/components/StatusTag.vue'

const props = defineProps<{ serial: string; device: Device | null; busy: boolean }>()

const emit = defineEmits<{
  hierarchy: []
  logcat: []
  network: []
}>()

// 设备信息是「工具」里的子项，点开走侧边抽屉。
const infoOpen = ref(false)
const infoQuery = useDeviceInfo(computed(() => props.serial))
const mirrorQuery = useMirrorStatus(computed(() => props.serial))
const info = computed<Device | null>(() => infoQuery.data.value ?? props.device)

const mirrorLabel = computed(() => {
  const data = mirrorQuery.data.value
  if (!data) return mirrorQuery.isError.value ? '状态不可用' : '查询中…'
  if (!data.active) return '未镜像'
  return `镜像中 ${data.width ?? '?'}×${data.height ?? '?'}`
})

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

function refreshDeviceInfo(): void {
  void infoQuery.refetch()
  void mirrorQuery.refetch()
}
</script>

<template>
  <section class="manual-page">
    <div class="section-head">
      <h3>
        <el-icon><Grid /></el-icon>更多工具
      </h3>
      <StatusTag>设备侧</StatusTag>
    </div>
    <div class="tool-list">
      <div class="tool-item">
        <span class="tool-ico"><el-icon><Document /></el-icon></span>
        <div class="tool-main">
          <strong>层级树</strong><small>截屏上叠加元素层级，悬停看信息，点击复制选择器</small>
        </div>
        <el-button size="small" @click="emit('hierarchy')">打开</el-button>
      </div>
      <div class="tool-item">
        <span class="tool-ico"><el-icon><Monitor /></el-icon></span>
        <div class="tool-main">
          <strong>logcat</strong><small>查看最近日志，支持自动刷新与复制</small>
        </div>
        <el-button size="small" @click="emit('logcat')">打开</el-button>
      </div>
      <div class="tool-item">
        <span class="tool-ico"><el-icon><DataLine /></el-icon></span>
        <div class="tool-main">
          <strong>网络抓包</strong><small>只记录访问域名，不含 Header 与正文</small>
        </div>
        <el-button size="small" @click="emit('network')">打开</el-button>
      </div>
      <div class="tool-item">
        <span class="tool-ico"><el-icon><InfoFilled /></el-icon></span>
        <div class="tool-main">
          <strong>设备信息</strong><small>型号 / 系统 / 分辨率 / 占用与镜像状态</small>
        </div>
        <el-button size="small" @click="infoOpen = true">打开</el-button>
      </div>
    </div>

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
    <el-button size="small" style="margin-top: 7px" :loading="shellMutation.isPending.value" @click="runShell">
      执行
    </el-button>
    <pre v-if="shellOutput" class="pre-block" style="margin-top: 8px">{{ shellOutput }}</pre>
    <div class="section-sub">以上工具都作用于当前选中设备。</div>

    <el-drawer v-model="infoOpen" title="设备信息" size="min(420px, 94vw)" append-to-body>
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
        style="margin-top: 9px"
        :icon="Refresh"
        :loading="infoQuery.isFetching.value"
        @click="refreshDeviceInfo"
      >
        刷新信息
      </el-button>
    </el-drawer>
  </section>
</template>
