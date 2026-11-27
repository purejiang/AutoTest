import type { Component } from 'vue'

/** AppTabs 的单项；key 由父级状态（路由 query / Pinia / ref）决定。 */
export interface AppTabItem {
  key: string
  label: string
  icon?: Component
  count?: number | string
  badge?: { text: string; tone?: 'green' | 'amber' }
}
