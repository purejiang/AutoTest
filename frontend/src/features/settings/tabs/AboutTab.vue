<script setup lang="ts">
// 设置 → 关于：前后端版本、数据目录与健康信息。
import { Refresh } from '@element-plus/icons-vue'

import { describeApiError } from '@/api/client'
import { useHealth } from '@/api/settings'
import { formatDateTime } from '@/lib/format'
import StatusTag from '@/components/StatusTag.vue'

const APP_VERSION = __APP_VERSION__
const APP_COMMIT = __APP_COMMIT__
const APP_BUILD_TIME = __APP_BUILD_TIME__

const healthQuery = useHealth()
const health = healthQuery.data
</script>

<template>
  <div class="settings-pane">
    <section class="card settings-card">
      <div class="card-head">
        <h2>关于 AutoTest</h2>
        <span class="card-hint">单机 Android UI 自动化平台</span>
        <StatusTag :tone="health?.status === 'ok' ? 'green' : 'amber'">
          {{ health?.status === 'ok' ? '正常' : (health?.status ?? '未知') }}
        </StatusTag>
      </div>
      <div class="card-body">
        <div class="kv-grid">
          <div class="kv">
            <span>前端版本</span><strong>{{ APP_VERSION }}</strong>
          </div>
          <div class="kv">
            <span>前端提交</span><strong class="code-text">{{ APP_COMMIT }}</strong>
          </div>
          <div class="kv">
            <span>页面构建</span><strong>{{ formatDateTime(APP_BUILD_TIME) }}</strong>
          </div>
          <div class="kv">
            <span>后端版本</span><strong>{{ health?.version ?? '—' }}</strong>
          </div>
          <div class="kv">
            <span>数据目录</span><strong class="code-text">{{ health?.data_dir ?? '—' }}</strong>
          </div>
          <div class="kv">
            <span>配置优先级</span><strong>默认 &lt; 配置文件 &lt; 环境变量</strong>
          </div>
        </div>
      </div>
    </section>

    <section class="card">
      <div class="card-head">
        <h2>健康信息</h2>
        <span class="card-hint" title="状态来自后端 /api/health，缓存 5 秒。">后端状态 · adb · 目录</span>
        <el-button size="small" :icon="Refresh" :loading="healthQuery.isFetching.value" @click="healthQuery.refetch()">
          刷新
        </el-button>
      </div>
      <div class="card-body">
        <el-skeleton v-if="healthQuery.isLoading.value" :rows="3" animated />
        <el-alert
          v-else-if="healthQuery.isError.value || !health"
          type="warning"
          :closable="false"
          show-icon
          title="后端未连接"
          :description="describeApiError(healthQuery.error.value)"
        />
        <el-descriptions v-else :column="1" border size="small">
          <el-descriptions-item label="状态">
            <StatusTag :tone="health.status === 'ok' ? 'green' : 'amber'">
              {{ health.status === 'ok' ? '正常' : health.status }}
            </StatusTag>
          </el-descriptions-item>
          <el-descriptions-item label="版本">{{ health.version }}</el-descriptions-item>
          <el-descriptions-item label="存储状态">
            <span class="code-text">{{ health.db }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="adb">
            <span class="code-text">
              {{ health.adb?.path || '—' }}
              {{ health.adb?.version ? `· ${health.adb.version}` : '' }}
              · 设备 {{ health.adb?.devices ?? 0 }}
            </span>
          </el-descriptions-item>
          <el-descriptions-item label="数据目录">
            <span class="code-text">{{ health.data_dir }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="后端构建">
            <span class="code-text">
              {{ health.build?.commit ?? '未知' }}{{ health.build?.dirty ? ' (+未提交改动)' : '' }} ·
              {{ formatDateTime(health.build?.built_at) }}
            </span>
          </el-descriptions-item>
        </el-descriptions>
      </div>
    </section>
  </div>
</template>
