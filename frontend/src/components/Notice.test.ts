import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import Notice from './Notice.vue'

const alertStub = {
  name: 'ElAlert',
  props: {
    type: { type: String, default: 'info' },
    title: { type: String, default: '' },
    closable: { type: Boolean, default: false },
    showIcon: { type: Boolean, default: true },
  },
  emits: ['close'],
  template: '<div class="fake-alert" :data-type="type" :data-title="title"><slot /></div>',
}

function mountNotice(props: Record<string, unknown> = {}, slots: Record<string, string> = {}) {
  return mount(Notice, {
    props,
    slots,
    global: { stubs: { 'el-alert': alertStub } },
  })
}

describe('Notice', () => {
  it('danger 映射为 el-alert 的 error，并保留语义 data 属性', () => {
    const wrapper = mountNotice({ severity: 'danger', title: '运行失败' })
    const alert = wrapper.find('.fake-alert')
    expect(alert.attributes('data-type')).toBe('error')
    expect(alert.attributes('data-title')).toBe('运行失败')
    expect(wrapper.find('.app-notice').attributes('data-severity')).toBe('danger')
    expect(wrapper.find('.app-notice').attributes('data-variant')).toBe('block')
  })

  it('compact 变体只改密度，不减语义', () => {
    const wrapper = mountNotice({ severity: 'warning', variant: 'compact', title: '注意' })
    const root = wrapper.find('.app-notice')
    expect(root.classes()).toContain('app-notice--warning')
    expect(root.classes()).toContain('app-notice--compact')
  })

  it('支持正文与操作区', () => {
    const wrapper = mountNotice(
      { severity: 'warning' },
      {
        default: '<span class="body">依赖未开启</span>',
        actions: '<button class="action">去开启</button>',
      },
    )
    expect(wrapper.find('.body').text()).toBe('依赖未开启')
    expect(wrapper.find('.action').text()).toBe('去开启')
  })

  it('closable 透传并向上抛 close', () => {
    const wrapper = mountNotice({ severity: 'info', closable: true })
    expect(wrapper.find('.fake-alert').attributes('data-title')).toBe('')
    wrapper.findComponent(alertStub).vm.$emit('close')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
})
