import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PageHead from './PageHead.vue'

describe('PageHead', () => {
  it('renders title, subtitle and count', () => {
    const wrapper = mount(PageHead, {
      props: { title: '运行 #7', subtitle: '脚本 / 运行记录', count: 3 },
    })
    expect(wrapper.find('h1').text()).toBe('运行 #7')
    expect(wrapper.find('.page-sub').text()).toBe('脚本 / 运行记录')
    expect(wrapper.find('.count-pill').text()).toBe('3')
  })

  it('shows count 0 and title-extra slot', () => {
    const wrapper = mount(PageHead, {
      props: { title: '脚本', count: 0 },
      slots: { 'title-extra': '<span class="extra">extra</span>' },
    })
    expect(wrapper.find('.count-pill').text()).toBe('0')
    expect(wrapper.find('.extra').text()).toBe('extra')
  })

  it('renders the back button and emits back only when slot exists', async () => {
    const withoutBack = mount(PageHead, { props: { title: 'x' } })
    expect(withoutBack.find('.icon-btn').exists()).toBe(false)

    const wrapper = mount(PageHead, {
      props: { title: 'x' },
      slots: { back: '<span class="back-icon">←</span>' },
    })
    await wrapper.find('.icon-btn').trigger('click')
    expect(wrapper.emitted('back')).toHaveLength(1)
  })

  it('prefers the title slot over the title prop', () => {
    const wrapper = mount(PageHead, {
      props: { title: 'prop title' },
      slots: { title: '<h1 class="custom">slot title</h1>' },
    })
    expect(wrapper.find('h1.custom').text()).toBe('slot title')
    expect(wrapper.text()).not.toContain('prop title')
  })

  it('renders actions / below-title slots', () => {
    const wrapper = mount(PageHead, {
      props: { title: 'x' },
      slots: {
        'below-title': '<div class="below">meta</div>',
        actions: '<button class="action">保存</button>',
      },
    })
    expect(wrapper.find('.below').text()).toBe('meta')
    expect(wrapper.find('.page-actions .action').text()).toBe('保存')
  })
})
