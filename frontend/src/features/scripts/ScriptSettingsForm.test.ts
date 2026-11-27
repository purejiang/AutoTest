import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import type { ScriptData } from '@/types/api'

import { normalizeDraft } from './nodeDefaults'
import ScriptSettingsForm from './ScriptSettingsForm.vue'

// 全局采集开关都关闭：脚本里的两个采集开关必须置灰。
vi.mock('@/api/settings', () => ({
  useSettings: () => ({
    data: {
      value: {
        effective: {
          'capture.logcat_auto_attach': false,
          'capture.network.enabled': false,
        },
      },
    },
  }),
}))

const passthrough = { template: '<div><slot /></div>' }
const switchStub = {
  name: 'ElSwitch',
  props: { disabled: { type: Boolean, default: false } },
  template: '<button class="fake-switch" :data-disabled="String(disabled)" />',
}

function mountForm(data: ScriptData) {
  return mount(ScriptSettingsForm, {
    props: { data },
    global: {
      stubs: {
        'el-form': passthrough,
        'el-form-item': passthrough,
        'el-input': true,
        'el-input-number': true,
        'el-select': true,
        'el-option': true,
        'el-switch': switchStub,
        KeyValueEditor: true,
        RouterLink: true,
      },
    },
  })
}

describe('ScriptSettingsForm 采集开关', () => {
  it('全局关闭时 logcat / 抓包开关置灰，其它开关不受影响', () => {
    const wrapper = mountForm(normalizeDraft({ steps: [] }))

    expect(wrapper.findAll('.fake-switch')).toHaveLength(4)
    expect(wrapper.find('[data-test="capture-logcat"]').attributes('data-disabled')).toBe('true')
    expect(wrapper.find('[data-test="capture-network"]').attributes('data-disabled')).toBe('true')
    expect(wrapper.find('[data-test="script-screenshot"]').attributes('data-disabled')).toBe('false')
    expect(wrapper.find('[data-test="script-retry"]').attributes('data-disabled')).toBe('false')
  })
})

describe('ScriptSettingsForm 失败处理默认值', () => {
  it('默认重试/失败策略变化会出现在流程摘要里', () => {
    const data = normalizeDraft({
      steps: [],
      settings: {
        retry: { count: 1, interval_ms: 1000 },
        on_fail: 'continue',
        screenshot_on_fail: true,
      } as ScriptData['settings'],
    })
    const wrapper = mountForm(data)

    const flow = wrapper.find('.failure-flow').text()
    expect(flow).toContain('重试 1 次（最多尝试 2 次，间隔 1000 ms）')
    expect(flow).toContain('继续下一步')
    expect(flow).toContain('保存失败截图')
    expect(wrapper.text()).toContain('节点未单独配置时继承此值')
  })
})
