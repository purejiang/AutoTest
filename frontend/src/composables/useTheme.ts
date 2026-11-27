// 主题偏好（浅色 / 深色 / 跟随系统）
//
// - 存 localStorage['autotest.theme']，不写后端设置；
// - index.html 里有一段同 key 的 boot script，刷新时在样式加载前就加上 dark class，避免闪白；
// - 顶层单例：顶部栏按钮与设置页主题卡共用同一状态。
import { computed, readonly, ref, watch } from 'vue'

export type ThemeChoice = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'autotest.theme'
// 与 index.html 的 boot script 保持一致：浅/深主题对应的浏览器地址栏颜色。
const THEME_COLOR_LIGHT = '#f2f4f7'
const THEME_COLOR_DARK = '#0f151d'

const media =
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null

function readTheme(): ThemeChoice {
  if (typeof window === 'undefined') return 'light'
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY)
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  } catch {
    // 隐私模式/禁用 storage 时按默认处理
  }
  return 'light'
}

function resolve(choice: ThemeChoice, systemDark: boolean): ResolvedTheme {
  if (choice === 'system') return systemDark ? 'dark' : 'light'
  return choice
}

const choice = ref<ThemeChoice>(readTheme())
const systemDark = ref(media?.matches ?? false)

function applyTheme(): void {
  if (typeof document === 'undefined') return
  const resolved = resolve(choice.value, systemDark.value)
  document.documentElement.classList.toggle('dark', resolved === 'dark')
  // 移动端/浏览器 UI 的地址栏颜色跟随实际主题，避免浅色主题下仍是深色。
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', resolved === 'dark' ? THEME_COLOR_DARK : THEME_COLOR_LIGHT)
}

if (media) {
  const onChange = (event: MediaQueryListEvent): void => {
    systemDark.value = event.matches
  }
  if (typeof media.addEventListener === 'function') media.addEventListener('change', onChange)
  else if (typeof media.addListener === 'function') media.addListener(onChange)
}

watch([choice, systemDark], applyTheme, { immediate: true, flush: 'sync' })

export function setTheme(next: ThemeChoice): void {
  choice.value = next
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, next)
  } catch {
    // 存不下就只在本次会话生效
  }
}

/** 顶部栏快捷切换：按“当前实际生效”的颜色切到另一档，并记住明确选择。 */
export function toggleTheme(): void {
  const current = resolve(choice.value, systemDark.value)
  setTheme(current === 'dark' ? 'light' : 'dark')
}

export function useTheme() {
  return {
    theme: readonly(choice),
    resolvedTheme: computed<ResolvedTheme>(() => resolve(choice.value, systemDark.value)),
    setTheme,
    toggleTheme,
  }
}
