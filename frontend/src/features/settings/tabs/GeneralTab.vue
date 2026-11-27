<script setup lang="ts">
// 设置 → 通用：外观（纯前端偏好）+ 语言（本版仅简体中文）。
import { ref } from 'vue'

import { useTheme, type ThemeChoice } from '@/composables/useTheme'

const { theme, setTheme } = useTheme()

const THEME_CHOICES = [
  { value: 'light', label: '浅色', note: '当前工作区风格' },
  { value: 'dark', label: '深色', note: '夜间 / 低光环境' },
  { value: 'system', label: '跟随系统', note: '自动匹配系统外观' },
] as const

const language = ref('zh-CN')
</script>

<template>
  <div class="settings-pane">
    <section class="card settings-card">
      <div class="card-head">
        <h2>外观</h2>
        <span class="card-hint" title="切换只影响当前浏览器，不写入后端配置；顶部栏主题按钮与此同步。">
          即时预览
        </span>
      </div>
      <div class="card-body">
        <div class="theme-choices">
          <button
            v-for="choice in THEME_CHOICES"
            :key="choice.value"
            type="button"
            class="theme-choice"
            :class="{ active: theme === choice.value }"
            :data-theme-choice="choice.value"
            @click="setTheme(choice.value as ThemeChoice)"
          >
            <span class="theme-preview" aria-hidden="true">
              <span class="tp-rail" />
              <span class="tp-body" />
            </span>
            <strong>{{ choice.label }}</strong>
            <small>{{ choice.note }}</small>
          </button>
        </div>
      </div>
    </section>

    <section class="card settings-card">
      <div class="card-head">
        <h2>语言</h2>
        <span class="card-hint" title="完整中英双语需要抽取全站文案，放到后续版本；本版先保留入口，不做半汉半英。">
          仅简体中文 · 双语后续版本
        </span>
      </div>
      <div class="card-body">
        <div class="field" style="max-width: 360px">
          <span class="field-label">界面语言</span>
          <el-select v-model="language" size="small" style="width: 100%">
            <el-option label="简体中文（zh-CN）" value="zh-CN" />
            <el-option label="English（规划中）" value="en-US" disabled />
          </el-select>
        </div>
      </div>
    </section>
  </div>
</template>
