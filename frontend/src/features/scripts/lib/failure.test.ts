import { describe, expect, it } from 'vitest'

import { describeFailureFlow } from './failure'

describe('describeFailureFlow', () => {
  it('不重试 + 中止 + 截图', () => {
    const flow = describeFailureFlow({
      retry: null,
      retryInherited: false,
      onFail: 'abort',
      onFailInherited: false,
      screenshot: true,
    })
    expect(flow.summary).toBe('失败 → 不重试 → 中止整个运行 → 保存失败截图')
  })

  it('继承脚本默认的重试/策略会显式标注来源', () => {
    const flow = describeFailureFlow({
      retry: { count: 1, interval_ms: 1000 },
      retryInherited: true,
      onFail: 'continue',
      onFailInherited: true,
      screenshot: true,
    })
    expect(flow.retryText).toBe('继承脚本默认：重试 1 次（最多尝试 2 次，间隔 1000 ms）')
    expect(flow.actionText).toBe('继承脚本默认：继续下一步')
    expect(flow.summary).toContain('失败 → 继承脚本默认：重试 1 次')
    expect(flow.summary).toContain('仍失败')
  })

  it('goto 带目标名称', () => {
    const flow = describeFailureFlow({
      retry: null,
      retryInherited: false,
      onFail: 'goto',
      onFailInherited: false,
      gotoLabel: '清理环境',
      screenshot: false,
    })
    expect(flow.actionText).toBe('跳转到「清理环境」')
    expect(flow.screenshotText).toBe('不保存失败截图')
  })

  it('脚本主开关关闭时说明原因', () => {
    const flow = describeFailureFlow({
      retry: { count: 0, interval_ms: 1000 },
      retryInherited: false,
      onFail: 'abort',
      onFailInherited: false,
      screenshot: false,
      screenshotBlocked: true,
    })
    expect(flow.retryText).toBe('不重试')
    expect(flow.screenshotText).toBe('不保存失败截图（脚本已关闭）')
  })
})
