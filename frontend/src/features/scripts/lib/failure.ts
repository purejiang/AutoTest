// 失败处理流程摘要：把“重试 → 重试用尽后的失败策略 → 失败截图”压成一行人话。
// 纯逻辑，NodeForm 与 ScriptSettingsForm 共用；不依赖 Vue。
import type { Retry } from '@/types/api'

export type FailureAction = 'abort' | 'continue' | 'goto'

export interface FailureFlowInput {
  /** 实际生效的重试值；不重试为 null。 */
  retry: Retry | null
  /** 该重试值是否来自脚本默认（用于加“继承脚本默认”前缀）。 */
  retryInherited: boolean
  onFail: FailureAction
  /** 该失败策略是否来自脚本默认。 */
  onFailInherited: boolean
  gotoLabel?: string | null
  /** 实际是否会保存失败截图（节点开关 && 脚本主开关）。 */
  screenshot: boolean
  /** 脚本主开关关闭：即使节点开着也不会截图。 */
  screenshotBlocked?: boolean
}

export interface FailureFlow {
  retryText: string
  actionText: string
  screenshotText: string
  summary: string
}

export function describeFailureFlow(input: FailureFlowInput): FailureFlow {
  const retryText =
    input.retry && input.retry.count > 0
      ? `${input.retryInherited ? '继承脚本默认：' : ''}重试 ${input.retry.count} 次（最多尝试 ${
          input.retry.count + 1
        } 次，间隔 ${input.retry.interval_ms} ms）`
      : '不重试'
  const baseAction =
    input.onFail === 'abort'
      ? '中止整个运行'
      : input.onFail === 'continue'
        ? '继续下一步'
        : `跳转到「${input.gotoLabel || '目标节点'}」`
  const actionText = input.onFailInherited ? `继承脚本默认：${baseAction}` : baseAction
  const screenshotText = input.screenshotBlocked
    ? '不保存失败截图（脚本已关闭）'
    : input.screenshot
      ? '保存失败截图'
      : '不保存失败截图'
  const retryPart = input.retry && input.retry.count > 0 ? `${retryText} → 仍失败` : retryText
  return {
    retryText,
    actionText,
    screenshotText,
    summary: `失败 → ${retryPart} → ${actionText} → ${screenshotText}`,
  }
}
