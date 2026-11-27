<script setup lang="ts">
// 设置 → 工具链：ADB / uiautomator2 / scrcpy-server 状态与相关配置项。
import { Cellphone, Connection, Cpu } from '@element-plus/icons-vue'

import { useHealth } from '@/api/settings'
import StatusTag from '@/components/StatusTag.vue'

import SettingField from '../SettingField.vue'
import { useSettingsDraftContext } from '../useSettingsDraft'

const healthQuery = useHealth()
const health = healthQuery.data

const { values, entriesFor, sourceOf, configFile, setValue, deleteOverride, scrcpyVersion, scrcpyJar, scrcpyStatus } =
  useSettingsDraftContext()
</script>

<template>
  <div class="settings-pane">
    <div class="tool-grid">
      <section class="card tool-card">
        <div class="card-head">
          <h2><el-icon :size="15"><Connection /></el-icon> ADB</h2>
          <span class="card-hint" title="设备连接、命令执行与在线设备数。">设备连接与命令</span>
          <StatusTag :tone="health?.adb?.version ? 'green' : health ? 'amber' : 'gray'">
            {{ health?.adb?.version ? '正常' : health ? '待检查' : '未知' }}
          </StatusTag>
        </div>
        <div class="card-body">
          <div class="tool-row">
            <span>路径</span><code>{{ health?.adb?.path || '—' }}</code>
          </div>
          <div class="tool-row">
            <span>版本</span><code>{{ health?.adb?.version || '—' }}</code>
          </div>
          <div class="tool-row">
            <span>在线设备</span><code>{{ health?.adb?.devices ?? '—' }}</code>
          </div>
          <SettingField
            v-for="entry in entriesFor('adb')"
            :key="entry.key"
            :entry="entry"
            :model-value="values[entry.key]"
            :source="sourceOf(entry)"
            :readonly="entry.layer === 'bootstrap'"
            :config-file="configFile"
            @update:model-value="setValue(entry.key, $event)"
            @clear="deleteOverride(entry)"
          />
          <div class="tool-actions">
            <el-button size="small" :loading="healthQuery.isFetching.value" @click="healthQuery.refetch()">
              重新探测
            </el-button>
          </div>
        </div>
      </section>

      <section class="card tool-card">
        <div class="card-head">
          <h2><el-icon :size="15"><Cpu /></el-icon> uiautomator2</h2>
          <span
            class="card-hint"
            title="层级树、元素定位、文本输入都依赖该驱动；设备端服务缺失时由后端在首次操作时提示。"
          >
            元素定位与输入
          </span>
          <StatusTag :tone="health?.status === 'ok' ? 'green' : health ? 'amber' : 'gray'">
            {{ health?.status === 'ok' ? '可用' : health ? '降级' : '未知' }}
          </StatusTag>
        </div>
        <div class="card-body">
          <div class="tool-row"><span>驱动</span><code>U2Driver</code></div>
          <div class="tool-row"><span>设备服务</span><code>首次操作时检查/安装</code></div>
          <div class="tool-row"><span>安装入口</span><code>不在此页触发</code></div>
        </div>
      </section>

      <section class="card tool-card">
        <div class="card-head">
          <h2><el-icon :size="15"><Cellphone /></el-icon> scrcpy-server</h2>
          <span class="card-hint" title="镜像画质、帧率和码率在「执行 → 镜像」配置。">
            镜像与录制服务
          </span>
          <StatusTag :tone="scrcpyStatus.tone">{{ scrcpyStatus.label }}</StatusTag>
        </div>
        <div class="card-body">
          <div class="tool-row">
            <span>版本</span><code>{{ scrcpyVersion }}</code>
          </div>
          <div class="tool-row">
            <span>内置 jar</span><code>{{ scrcpyJar }}</code>
          </div>
          <div class="tool-row"><span>校验</span><code>启动前 SHA256 校验/必要时重推</code></div>
          <SettingField
            v-for="entry in entriesFor('scrcpyJar')"
            :key="entry.key"
            :entry="entry"
            :model-value="values[entry.key]"
            :source="sourceOf(entry)"
            :readonly="entry.layer === 'bootstrap'"
            :config-file="configFile"
            @update:model-value="setValue(entry.key, $event)"
            @clear="deleteOverride(entry)"
          />
        </div>
      </section>
    </div>
  </div>
</template>
