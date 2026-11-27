import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { SettingEntry } from '@/types/api'

import SettingField from './SettingField.vue'

const inputStub = {
  props: ['placeholder'],
  template: '<input class="fake-input" :placeholder="placeholder" />',
}

const helpStub = {
  props: ['text'],
  template: '<span class="fake-help">{{ text }}</span>',
}

function mountField(entry: SettingEntry, modelValue: string | number | boolean | null) {
  return mount(SettingField, {
    props: { entry, modelValue, source: 'default' },
    global: {
      stubs: {
        'el-button': true,
        'el-input': inputStub,
        'el-input-number': true,
        'el-select': true,
        'el-option': true,
        'el-switch': true,
        HelpTip: helpStub,
      },
    },
  })
}

describe('SettingField', () => {
  it('字符串项用 default_display 作占位符，不再重复默认行', () => {
    const wrapper = mountField(
      {
        key: 'runtime.data_dir',
        type: 'string',
        title: '数据目录',
        default_display: '/home/user/.autotest/data',
      },
      '',
    )
    expect(wrapper.find('input.fake-input').attributes('placeholder')).toBe(
      '/home/user/.autotest/data',
    )
    expect(wrapper.find('.setting-default').exists()).toBe(false)
  })

  it('短说明用统一 .hint；长说明进标题右侧 HelpTip', () => {
    const wrapper = mountField(
      {
        key: 'runtime.data_dir',
        type: 'string',
        title: '数据目录',
        desc: '启动项；改动后需重启',
        help: '默认 ~/.autotest/data；也可写 config.json 或设置环境变量。',
      },
      '',
    )
    expect(wrapper.find('.field-desc').classes()).toContain('hint')
    expect(wrapper.find('.field-desc').text()).toBe('启动项；改动后需重启')
    expect(wrapper.find('.fake-help').text()).toContain('config.json')
  })

  it('数字项保留默认行，供空值时参考', () => {
    const wrapper = mountField(
      { key: 'engine.default_timeout_ms', type: 'int', title: '默认节点超时', default: 8000 },
      0,
    )
    expect(wrapper.find('.setting-default').text()).toContain('8000')
  })
})
