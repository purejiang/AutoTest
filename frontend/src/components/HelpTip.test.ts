import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import HelpTip from './HelpTip.vue'

const tooltipStub = {
  name: 'ElTooltip',
  props: { content: { type: String, default: '' }, placement: { type: String, default: 'top' } },
  template: '<div class="fake-tooltip" :data-content="content" :data-placement="placement"><slot /></div>',
}

function mountTip(props: Record<string, unknown> = {}) {
  return mount(HelpTip, {
    props: { text: '默认 ~/.autotest/data；也可写 config.json。', ...props },
    global: { stubs: { 'el-tooltip': tooltipStub, 'el-icon': true } },
  })
}

describe('HelpTip', () => {
  it('渲染可聚焦的帮助按钮，tooltip 文本正确', () => {
    const wrapper = mountTip()
    const button = wrapper.find('button.help-tip')
    expect(button.attributes('type')).toBe('button')
    expect(button.attributes('aria-label')).toBe('查看说明')
    expect(wrapper.find('.fake-tooltip').attributes('data-content')).toContain('~/.autotest/data')
  })

  it('支持自定义 aria 文案和位置', () => {
    const wrapper = mountTip({ label: '查看目录说明', placement: 'bottom' })
    expect(wrapper.find('button.help-tip').attributes('aria-label')).toBe('查看目录说明')
    expect(wrapper.find('.fake-tooltip').attributes('data-placement')).toBe('bottom')
  })
})
