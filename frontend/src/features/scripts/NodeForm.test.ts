import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { StepNode } from '@/types/api'

import NodeForm from './NodeForm.vue'
import { createStep } from './nodeDefaults'

const passthrough = { template: '<div><slot /></div>' }
const switchStub = {
  name: 'ElSwitch',
  props: {
    modelValue: { type: Boolean, default: false },
    disabled: { type: Boolean, default: false },
  },
  emits: ['update:modelValue'],
  template:
    '<button class="fake-switch" :data-on="String(modelValue)" :data-disabled="String(disabled)" @click="$emit(\'update:modelValue\', !modelValue)" />',
}
const numberStub = {
  name: 'ElInputNumber',
  props: { modelValue: { type: Number, default: null }, disabled: { type: Boolean, default: false } },
  template: '<input class="fake-number" :value="modelValue" :disabled="disabled" />',
}
const selectStub = {
  name: 'ElSelect',
  props: { modelValue: { type: [String, Array], default: null } },
  emits: ['update:modelValue'],
  template: '<div class="fake-select"><slot /></div>',
}
const optionStub = {
  name: 'ElOption',
  props: { label: { type: String, default: '' }, value: { type: String, default: '' } },
  template: '<span class="fake-option">{{ label }}</span>',
}
const buttonStub = {
  name: 'ElButton',
  emits: ['click'],
  template: '<button class="fake-button" @click="$emit(\'click\')"><slot /></button>',
}

function makeNodes(partial: Partial<StepNode> = {}) {
  const first = { ...createStep('click', []), ...partial }
  const second = { ...createStep('wait', [first.id]), name: '等待收尾' }
  return { first, second, steps: [first, second] }
}

function mountForm(
  partial: Partial<StepNode> = {},
  props: {
    defaultOnFail?: 'abort' | 'continue'
    defaultRetry?: { count: number; interval_ms: number } | null
    scriptScreenshotOnFail?: boolean
  } = {},
) {
  const { first, steps } = makeNodes(partial)
  return mount(NodeForm, {
    props: {
      node: first,
      total: steps.length,
      steps,
      defaultOnFail: props.defaultOnFail ?? 'abort',
      defaultTimeoutMs: 8000,
      defaultDelayBeforeMs: 0,
      defaultDelayAfterMs: 0,
      defaultRetry: props.defaultRetry ?? null,
      scriptScreenshotOnFail: props.scriptScreenshotOnFail ?? true,
    },
    global: {
      stubs: {
        'el-form': passthrough,
        'el-form-item': passthrough,
        'el-collapse': passthrough,
        'el-collapse-item': passthrough,
        'el-alert': passthrough,
        'el-input': true,
        'el-input-number': numberStub,
        'el-select': selectStub,
        'el-option': optionStub,
        'el-switch': switchStub,
        'el-button': buttonStub,
        ParamsEditor: true,
        WhenEditor: true,
      },
    },
  })
}

describe('NodeForm 失败处理', () => {
  it('脚本默认重试开启时，节点显示为继承+开关开，而不是关着+一行文案', () => {
    const wrapper = mountForm({ retry: null }, { defaultRetry: { count: 1, interval_ms: 1000 } })

    const retrySwitch = wrapper.find('[data-test="node-retry"]')
    expect(retrySwitch.attributes('data-on')).toBe('true')
    expect(wrapper.text()).toContain('继承脚本默认')
    expect(wrapper.find('[data-test="retry-count"]').attributes('value')).toBe('1')
    expect(wrapper.find('[data-test="retry-interval"]').attributes('value')).toBe('1000')
    expect(wrapper.text()).toContain('失败 → 继承脚本默认：重试 1 次')
  })

  it('关闭继承的重试会写成显式 count 0，而不是回到 null', async () => {
    const wrapper = mountForm({ retry: null }, { defaultRetry: { count: 1, interval_ms: 1000 } })

    await wrapper.find('[data-test="node-retry"]').trigger('click')

    const changed = wrapper.emitted('change')?.at(-1)?.[0] as StepNode
    expect(changed.retry).toEqual({ count: 0, interval_ms: 1000 })
  })

  it('节点显式关闭时显示已覆盖，并可恢复继承', async () => {
    const wrapper = mountForm(
      { retry: { count: 0, interval_ms: 1000 } },
      { defaultRetry: { count: 2, interval_ms: 500 } },
    )

    expect(wrapper.find('[data-test="node-retry"]').attributes('data-on')).toBe('false')
    expect(wrapper.text()).toContain('已覆盖：不重试')
    await wrapper.find('[data-test="retry-restore"]').trigger('click')

    const changed = wrapper.emitted('change')?.at(-1)?.[0] as StepNode
    expect(changed.retry).toBeNull()
  })

  it('失败策略把继承值直接写进选项，不再在右侧重复一行', () => {
    const wrapper = mountForm({ on_fail: null }, { defaultOnFail: 'continue' })

    expect(wrapper.text()).toContain('继承脚本默认（继续下一步）')
    expect(wrapper.text()).toContain('继承脚本默认：继续下一步')
  })

  it('goto 标为高级并显示目标节点', () => {
    const wrapper = mountForm(
      { on_fail: { action: 'goto', target_id: '' } },
      { defaultOnFail: 'abort' },
    )

    expect(wrapper.text()).toContain('跳转到节点（高级）')
    expect(wrapper.find('[data-test="goto-target"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('重入会重置目标及之后节点的重试计数')
  })

  it('脚本关闭失败截图时，节点开关置灰并说明不生效', () => {
    const wrapper = mountForm({ screenshot_on_fail: true }, { scriptScreenshotOnFail: false })

    const shot = wrapper.find('[data-test="node-screenshot"]')
    expect(shot.attributes('data-disabled')).toBe('true')
    expect(shot.attributes('data-on')).toBe('false')
    expect(wrapper.text()).toContain('脚本设置里已关闭「失败时自动截图」')
  })
})
