import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import EmptyState from './EmptyState.vue'

const stubs = { 'el-icon': { template: '<i class="el-icon"><slot /></i>' } }
const icon = { template: '<svg class="fake-icon" />' }

describe('EmptyState', () => {
  it('renders icon, title and description', () => {
    const wrapper = mount(EmptyState, {
      props: { icon, title: '没有设备', description: '先连接一台设备' },
      global: { stubs },
    })
    expect(wrapper.classes()).toContain('empty-state')
    expect(wrapper.classes()).not.toContain('is-compact')
    expect(wrapper.find('.fake-icon').exists()).toBe(true)
    expect(wrapper.find('.empty-state__title').text()).toBe('没有设备')
    expect(wrapper.find('.empty-state__desc').text()).toBe('先连接一台设备')
  })

  it('supports compact and action / extra slots', () => {
    const wrapper = mount(EmptyState, {
      props: { title: '不可用', compact: true },
      slots: {
        extra: '<div class="extra">附加说明</div>',
        actions: '<button class="action">连接</button>',
      },
      global: { stubs },
    })
    expect(wrapper.classes()).toContain('is-compact')
    expect(wrapper.find('.extra').text()).toBe('附加说明')
    expect(wrapper.find('.empty-state__actions .action').text()).toBe('连接')
  })

  it('uses the default slot instead of description', () => {
    const wrapper = mount(EmptyState, {
      props: { title: 'x' },
      slots: { default: '<p class="custom">自定义说明</p>' },
      global: { stubs },
    })
    expect(wrapper.find('.custom').text()).toBe('自定义说明')
  })

  it('omits icon/description/actions when not provided', () => {
    const wrapper = mount(EmptyState, { props: { title: 'x' }, global: { stubs } })
    expect(wrapper.find('.empty-state__icon').exists()).toBe(false)
    expect(wrapper.find('.empty-state__desc').exists()).toBe(false)
    expect(wrapper.find('.empty-state__actions').exists()).toBe(false)
  })
})
