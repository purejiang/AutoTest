// 脚本编辑器里的「当前脚本变量」上下文：变量按钮/字段/未声明提示都从这里取。
// 只有 ScriptEditorPage 提供；没有 provider 时（比如独立复用的组件）变量按钮自动隐藏。
import { inject, provide, type ComputedRef, type InjectionKey } from 'vue'

export interface ScriptVariablesContext {
  /** 脚本声明的 variables（默认值，随草稿实时变化）。 */
  variables: ComputedRef<Record<string, string>>
  /** 整份草稿里出现过 `{{x}}` 的变量名（排序去重）。 */
  used: ComputedRef<string[]>
  /** used 里未在 variables 声明的名字。 */
  missing: ComputedRef<string[]>
  /** 往脚本 variables 里补一个空值（标记未保存）。 */
  addVariable: (name: string) => void
  /** 打开脚本设置抽屉（变量声明入口）。 */
  openSettings: () => void
}

export const SCRIPT_VARIABLES_KEY: InjectionKey<ScriptVariablesContext> = Symbol('autotest.scriptVariables')

export function provideScriptVariables(context: ScriptVariablesContext): void {
  provide(SCRIPT_VARIABLES_KEY, context)
}

export function useScriptVariables(): ScriptVariablesContext | null {
  return inject(SCRIPT_VARIABLES_KEY, null)
}
