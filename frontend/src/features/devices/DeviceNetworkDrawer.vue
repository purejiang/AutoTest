<script setup lang="ts">
import { Connection, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import { describeApiError, toApiError } from '@/api/client'
import { useNetworkStart, useNetworkStatus, useNetworkStop } from '@/api/network'
import { formatBytes } from '@/lib/format'
import StatusTag from '@/components/StatusTag.vue'

const props = defineProps<{
  serial: string | null
  open: boolean
}>()

const emit = defineEmits<{ close: [] }>()

const statusQuery = useNetworkStatus(computed(() => props.serial), computed(() => props.open))
const startMutation = useNetworkStart(computed(() => props.serial))
const stopMutation = useNetworkStop(computed(() => props.serial))

const router = useRouter()

const status = computed(() => statusQuery.data.value ?? null)
/**
 * 抓包默认关闭（capture.network.mode=off），此时 status 接口返回 UNSUPPORTED。
 * 这不是故障，给一句明确的开启指引，而不是把通用错误码文案当成“不可用”。
 */
const disabled = computed(
  () => statusQuery.isError.value && toApiError(statusQuery.error.value).code === 'UNSUPPORTED',
)
const errorText = computed(() => describeApiError(statusQuery.error.value))
const active = computed(() => Boolean(status.value?.active))
const domains = computed(() => status.value?.recent_domains ?? [])

function goSettings(): void {
  void router.push({ path: '/settings', query: { tab: 'execution' } })
}

async function startCapture(): Promise<void> {
  if (!props.serial) return
  try {
    const result = await startMutation.mutateAsync()
    ElMessage.success(`已开始记录域名（127.0.0.1:${result.capture.port ?? '?'}）`)
    await statusQuery.refetch()
  } catch (error) {
    ElMessage.error(describeApiError(error))
  }
}

async function stopCapture(): Promise<void> {
  if (!props.serial) return
  try {
    await stopMutation.mutateAsync()
    ElMessage.success('网络抓包已停止，域名清单已保存')
    await statusQuery.refetch()
  } catch (error) {
    ElMessage.error(describeApiError(error))
  }
}
</script>

<template>
  <el-drawer :model-value="open" size="560px" append-to-body @update:model-value="!$event && emit('close')">
    <template #header>
      <div class="network-drawer-title">
        <el-icon><Connection /></el-icon>
        <strong>网络抓包 · 域名模式</strong>
        <span class="code-text muted">{{ serial ?? '' }}</span>
      </div>
    </template>

    <div class="network-drawer">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="只记录域名，不含正文"
        description="只记录访问过的 host / port / scheme，不抓 Header、请求体、响应体。同一时间只支持一台设备；不走系统代理或使用 QUIC（HTTP/3）的流量可能漏抓。"
      />

      <div class="network-toolbar">
        <StatusTag :tone="disabled ? 'amber' : active ? 'green' : 'gray'" dot>
          {{ disabled ? '未启用' : active ? '抓包中' : '未开始' }}
        </StatusTag>
        <span v-if="status" class="hint">监听 127.0.0.1:{{ status.port ?? '—' }}</span>
        <span class="spacer" />
        <el-button
          size="small"
          :icon="Refresh"
          :loading="statusQuery.isFetching.value"
          :disabled="!serial"
          @click="statusQuery.refetch()"
        >
          刷新
        </el-button>
      </div>

      <div class="network-actions">
        <el-button
          type="primary"
          size="small"
          :loading="startMutation.isPending.value"
          :disabled="!serial || active || disabled"
          @click="startCapture"
        >
          开始抓包
        </el-button>
        <el-button
          type="danger"
          size="small"
          :loading="stopMutation.isPending.value"
          :disabled="!active"
          @click="stopCapture"
        >
          停止并保存
        </el-button>
      </div>

      <el-skeleton v-if="statusQuery.isLoading.value" :rows="6" animated />

      <div v-else-if="disabled" class="network-disabled">
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="网络抓包未启用（默认关闭）"
          description="在「设置 → 执行 → 采集 → 网络域名抓包」选择「仅手动」或「跟随运行」，保存后立即生效，无需重启。"
        />
        <el-button size="small" type="primary" plain @click="goSettings">去设置开启</el-button>
      </div>

      <el-alert
        v-else-if="statusQuery.isError.value"
        type="error"
        :closable="false"
        show-icon
        title="网络抓包状态读取失败"
        :description="errorText"
      />

      <template v-else>
        <div class="network-stats">
          <div class="network-stat">
            <span>域名数</span>
            <strong>{{ status?.domain_count ?? 0 }}</strong>
          </div>
          <div class="network-stat">
            <span>请求数</span>
            <strong>{{ status?.record_count ?? 0 }}</strong>
          </div>
          <div class="network-stat">
            <span>清单大小</span>
            <strong>{{ formatBytes(status?.size ?? 0) }}</strong>
          </div>
        </div>

        <div class="network-path">
          <span>保存路径</span>
          <code>{{ status?.path ?? '—' }}</code>
        </div>

        <div class="network-section-head">
          <h3>最近域名</h3>
          <span class="hint">访问新域名后实时刷新</span>
        </div>
        <el-empty
          v-if="domains.length === 0"
          :image-size="64"
          description="暂无域名：开始抓包后，让设备访问网络即可看到记录"
        />
        <ul v-else class="network-domains">
          <li v-for="(domain, index) in domains" :key="`${domain}-${index}`">
            <span class="domain-index">{{ index + 1 }}</span>
            <span class="domain-name code-text">{{ domain }}</span>
          </li>
        </ul>

        <p class="network-footnote">
          设备代理通过 USB adb reverse 指向本机 127.0.0.1；停止时会恢复设备原来的
          http_proxy。域名模式只用于看访问了哪些主机，无法还原请求内容。
        </p>
      </template>
    </div>
  </el-drawer>
</template>

<style scoped>
.network-drawer-title {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}

.network-drawer-title .el-icon {
  color: var(--muted);
}

.network-drawer-title strong {
  color: var(--ink);
  font-size: 13px;
  font-weight: 650;
  white-space: nowrap;
}

.network-drawer-title .code-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.network-drawer {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
}

.network-disabled {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.network-disabled .el-alert {
  flex: 1;
  min-width: 220px;
}

.network-toolbar {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}

.network-actions {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}

.network-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.network-stat {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  padding: 8px 9px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface-2);
}

.network-stat span {
  color: var(--muted);
  font-size: 10.5px;
}

.network-stat strong {
  overflow: hidden;
  color: var(--ink);
  font-family: var(--mono);
  font-size: 14px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.network-path {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
  padding: 7px 9px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface-2);
}

.network-path > span {
  flex: none;
  color: var(--muted);
  font-size: 10.5px;
  line-height: 1.55;
}

.network-path code {
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--ink);
  font-family: var(--mono);
  font-size: 10.5px;
  line-height: 1.55;
}

.network-section-head {
  display: flex;
  align-items: baseline;
  gap: 7px;
  flex-wrap: wrap;
}

.network-section-head h3 {
  margin: 0;
  color: var(--ink);
  font-size: 12px;
  font-weight: 650;
}

.network-domains {
  display: flex;
  flex-direction: column;
  max-height: 46vh;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  border: 1px solid var(--line);
  border-radius: 7px;
  list-style: none;
}

.network-domains li {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 7px 9px;
  border-bottom: 1px solid var(--line-soft);
}

.network-domains li:last-child {
  border-bottom: 0;
}

.domain-index {
  flex: none;
  width: 22px;
  color: var(--muted-2);
  font-family: var(--mono);
  font-size: 10px;
  text-align: right;
}

.domain-name {
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.network-footnote {
  margin: 0;
  color: var(--muted-2);
  font-size: 10.5px;
  line-height: 1.55;
}

@media (max-width: 640px) {
  .network-stats {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
