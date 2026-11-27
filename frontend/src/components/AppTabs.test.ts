import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AppTabs from './AppTabs.vue'
import type { AppTabItem } from './appTabs'

const items: AppTabItem[] = [
  { key: 'library', label: '脚本库', count: 3 },
  { key: 'runs', label: '运行记录', count: 5 },
]

const stubs = { 'el-icon': { template: '<i class="el-icon"><slot /></i>' } }

const iconItems: AppTabItem[] = [
  { key: 'manual', label: '手动控制', icon: { template: '<svg class="fake-icon" />' } },
  { key: 'auto', label: '自动控制', badge: { text: '运行中', tone: 'green' } },
]

describe('AppTabs', () => {
  it('renders variant, tabs and active/aria state', () => {
    const wrapper = mount(AppTabs, {
      props: { modelValue: 'library', items, variant: 'segmented', ariaLabel: '脚本视图' },
      global: { stubs },
    })
    expect(wrapper.classes()).toContain('app-tabs--segmented')
    expect(wrapper.attributes('role')).toBe('tablist')
    expect(wrapper.attributes('aria-label')).toBe('脚本视图')
    const tabs = wrapper.findAll('[role="tab"]')
    expect(tabs).toHaveLength(2)
    expect(tabs[0]?.classes()).toContain('active')
    expect(tabs[0]?.attributes('aria-selected')).toBe('true')
    expect(tabs[1]?.attributes('aria-selected')).toBe('false')
    expect(tabs[0]?.text()).toContain('脚本库')
    expect(wrapper.find('.app-tabs__count').text()).toBe('3')
  })

  it('emits update:modelValue with the clicked key', async () => {
    const wrapper = mount(AppTabs, { props: { modelValue: 'library', items }, global: { stubs } })
    await wrapper.findAll('[role="tab"]')[1]?.trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([['runs']])
  })

  it('renders icons and dock badge', () => {
    const wrapper = mount(AppTabs, {
      props: { modelValue: 'manual', items: iconItems, variant: 'dock' },
      global: { stubs },
    })
    expect(wrapper.classes()).toContain('app-tabs--dock')
    expect(wrapper.find('.fake-icon').exists()).toBe(true)
    expect(wrapper.find('.app-tabs__badge').text()).toBe('运行中')
    expect(wrapper.find('.app-tabs__badge').classes()).toContain('green')
  })

  it('defaults to segmented and a generic aria label', () => {
    const wrapper = mount(AppTabs, { props: { modelValue: 'library', items }, global: { stubs } })
    expect(wrapper.classes()).toContain('app-tabs--segmented')
    expect(wrapper.attributes('aria-label')).toBe('视图切换')
  })
})
