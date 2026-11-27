import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import StatusTag from './StatusTag.vue'

describe('StatusTag', () => {
  it('applies tone class and renders slot text', () => {
    const wrapper = mount(StatusTag, { props: { tone: 'green' }, slots: { default: '成功' } })
    expect(wrapper.classes()).toContain('tag')
    expect(wrapper.classes()).toContain('green')
    expect(wrapper.text()).toBe('成功')
  })

  it('defaults to gray and supports dot / mono', () => {
    const wrapper = mount(StatusTag, { props: { dot: true, mono: true }, slots: { default: 'x' } })
    expect(wrapper.classes()).toContain('gray')
    expect(wrapper.classes()).toContain('tag-mono')
    expect(wrapper.find('.tag-dot').exists()).toBe(true)
  })

  it('does not render a dot by default', () => {
    const wrapper = mount(StatusTag, { slots: { default: 'x' } })
    expect(wrapper.find('.tag-dot').exists()).toBe(false)
  })
})
