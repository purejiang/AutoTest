import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import AppLayout from '@/layouts/AppLayout.vue'
import DevicesPage from '@/features/devices/DevicesPage.vue'
import ScriptsPage from '@/features/scripts/ScriptsPage.vue'
import ScriptEditorPage from '@/features/scripts/ScriptEditorPage.vue'
import RunsPage from '@/features/runs/RunsPage.vue'
import RunDetailPage from '@/features/runs/RunDetailPage.vue'
import RecordingsPage from '@/features/recorder/RecordingsPage.vue'
import RecordingDetailPage from '@/features/recorder/RecordingDetailPage.vue'
import SettingsPage from '@/features/settings/SettingsPage.vue'
import NotFoundPage from '@/features/NotFoundPage.vue'

export const NAV_ITEMS = [
  { path: '/devices', label: '设备', section: '设备', page: '工作台' },
  { path: '/scripts', label: '脚本', section: '脚本', page: '列表' },
  { path: '/runs', label: '运行', section: '运行', page: '列表' },
  { path: '/recorder', label: '录制', section: '录制', page: '草稿' },
  { path: '/settings', label: '设置', section: '设置', page: '配置' },
] as const

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: AppLayout,
    children: [
      { path: '', redirect: '/devices' },
      {
        path: 'devices',
        name: 'devices',
        component: DevicesPage,
        meta: { title: '设备工作台', section: '设备', page: '工作台' },
      },
      {
        path: 'scripts',
        name: 'scripts',
        component: ScriptsPage,
        meta: { title: '脚本', section: '脚本', page: '列表' },
      },
      {
        path: 'scripts/:id',
        name: 'script-editor',
        component: ScriptEditorPage,
        meta: { title: '脚本编辑器', section: '脚本', page: '编辑器' },
      },
      {
        path: 'runs',
        name: 'runs',
        component: RunsPage,
        meta: { title: '运行', section: '运行', page: '列表' },
      },
      {
        path: 'runs/:id',
        name: 'run-detail',
        component: RunDetailPage,
        meta: { title: '运行详情', section: '运行', page: '详情' },
      },
      {
        path: 'recorder',
        name: 'recordings',
        component: RecordingsPage,
        meta: { title: '录制草稿', section: '录制', page: '草稿' },
      },
      {
        path: 'recorder/:id',
        name: 'recording-detail',
        component: RecordingDetailPage,
        meta: { title: '录制草稿', section: '录制', page: '详情' },
      },
      {
        path: 'settings',
        name: 'settings',
        component: SettingsPage,
        meta: { title: '设置', section: '设置', page: '配置' },
      },
      { path: ':pathMatch(.*)*', name: 'not-found', component: NotFoundPage },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' ? to.meta.title : 'AutoTest'
  document.title = `${title} · AutoTest`
})

export default router
