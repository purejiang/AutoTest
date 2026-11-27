import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import AppLayout from '@/layouts/AppLayout.vue'
import ConsolePage from '@/features/console/ConsolePage.vue'
import ScriptsPage from '@/features/scripts/ScriptsPage.vue'
import ScriptEditorPage from '@/features/scripts/ScriptEditorPage.vue'
import RunDetailPage from '@/features/runs/RunDetailPage.vue'
import RecordingsPage from '@/features/recorder/RecordingsPage.vue'
import RecordingDetailPage from '@/features/recorder/RecordingDetailPage.vue'
import SettingsPage from '@/features/settings/SettingsPage.vue'
import NotFoundPage from '@/features/NotFoundPage.vue'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: AppLayout,
    children: [
      { path: '', redirect: '/console' },
      {
        path: 'console',
        name: 'console',
        component: ConsolePage,
        meta: { title: '控制台', breadcrumb: ['控制台'] },
      },
      // 旧链接兼容：设备页由控制台替代。
      { path: 'devices', redirect: '/console' },
      {
        path: 'scripts',
        name: 'scripts',
        component: ScriptsPage,
        meta: { title: '脚本', breadcrumb: ['脚本', '脚本库'] },
      },
      {
        path: 'scripts/:id',
        name: 'script-editor',
        component: ScriptEditorPage,
        meta: { title: '脚本编辑器', breadcrumb: ['脚本', '编辑器'] },
      },
      // 运行列表已合并进脚本页的运行记录 tab。
      { path: 'runs', redirect: (to) => ({ path: '/scripts', query: { ...to.query, tab: 'runs' } }) },
      {
        path: 'runs/:id',
        name: 'run-detail',
        component: RunDetailPage,
        meta: { title: '运行详情', breadcrumb: ['脚本', '运行记录', '详情'] },
      },
      {
        path: 'recorder',
        name: 'recordings',
        component: RecordingsPage,
        meta: { title: '录制草稿', breadcrumb: ['录制', '录制草稿'] },
      },
      {
        path: 'recorder/:id',
        name: 'recording-detail',
        component: RecordingDetailPage,
        meta: { title: '录制详情', breadcrumb: ['录制', '录制详情'] },
      },
      {
        path: 'settings',
        name: 'settings',
        component: SettingsPage,
        meta: { title: '设置', breadcrumb: ['设置'] },
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
