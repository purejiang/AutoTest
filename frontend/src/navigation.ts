// 左侧主导航的唯一来源：路由（router/index.ts）只负责路径映射，
// 导航文案/图标/分组信息集中在这里，避免布局与路由各写一份导致漂移。
import { Document, Monitor, Setting, VideoCamera } from '@element-plus/icons-vue'
import type { Component } from 'vue'

export interface NavItem {
  path: string
  label: string
  section: string
  page: string
  icon: Component
}

/** v0.2.5 起主导航只保留四项，运行记录合并进脚本页。 */
export const NAV_ITEMS: readonly NavItem[] = [
  { path: '/console', label: '控制台', section: '控制台', page: '工作台', icon: Monitor },
  { path: '/scripts', label: '脚本', section: '脚本', page: '列表', icon: Document },
  { path: '/recorder', label: '录制', section: '录制', page: '草稿', icon: VideoCamera },
  { path: '/settings', label: '设置', section: '设置', page: '配置', icon: Setting },
]
