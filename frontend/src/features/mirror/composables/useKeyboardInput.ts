// 键盘输入 -> Android 文本/返回键（契约 §7.1）。
//
// 规则：
//   - 焦点在镜像面板时，可打印字符进本地缓冲，600ms 防抖后 POST .../text {text}；
//   - 回车立即发送缓冲文本；Esc / 空缓冲时的 Backspace 映射为 key=back；
//   - 面板内输入框（录制名称等）获得焦点时不拦截。

import { onBeforeUnmount, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'

import { errorMessage, post } from '../lib/api'

const TEXT_DEBOUNCE_MS = 600
const EDITABLE_SELECTOR = 'input, textarea, select, [contenteditable="true"], [contenteditable=""]'

export interface KeyboardInputOptions {
  serial: MaybeRefOrGetter<string>
  enabled: MaybeRefOrGetter<boolean>
  onError?: (message: string) => void
}

function devicePath(serial: string, action: string): string {
  return `/devices/${encodeURIComponent(serial)}/${action}`
}

export function useKeyboardInput(options: KeyboardInputOptions) {
  const pendingText = ref('')
  let pending = ''
  let timer: ReturnType<typeof setTimeout> | null = null

  function clearTimer(): void {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  function postKey(key: string): void {
    void (async () => {
      try {
        await post<void>(devicePath(toValue(options.serial), 'key'), { key })
      } catch (err) {
        options.onError?.(`按键 ${key} 失败：${errorMessage(err)}`)
      }
    })()
  }

  function flush(): void {
    clearTimer()
    const text = pending
    if (!text) return
    pending = ''
    pendingText.value = ''
    void (async () => {
      try {
        await post<void>(devicePath(toValue(options.serial), 'text'), { text })
      } catch (err) {
        options.onError?.(`文本发送失败：${errorMessage(err)}`)
      }
    })()
  }

  function scheduleFlush(): void {
    clearTimer()
    timer = setTimeout(() => {
      timer = null
      flush()
    }, TEXT_DEBOUNCE_MS)
  }

  function onKeyDown(event: KeyboardEvent): void {
    if (!toValue(options.enabled)) return
    const target = event.target as HTMLElement | null
    if (target && typeof target.closest === 'function' && target.closest(EDITABLE_SELECTOR)) {
      return
    }
    if (event.ctrlKey || event.metaKey || event.altKey) return

    const key = event.key
    if (key === 'Enter') {
      event.preventDefault()
      flush()
      return
    }
    if (key === 'Escape') {
      event.preventDefault()
      flush()
      postKey('back')
      return
    }
    if (key === 'Backspace') {
      event.preventDefault()
      if (pending.length > 0) {
        pending = pending.slice(0, -1)
        pendingText.value = pending
        scheduleFlush()
      } else {
        postKey('back')
      }
      return
    }
    if (key.length === 1) {
      event.preventDefault()
      pending += key
      pendingText.value = pending
      scheduleFlush()
    }
  }

  watch(
    () => toValue(options.serial),
    () => {
      pending = ''
      pendingText.value = ''
      clearTimer()
    },
  )

  onBeforeUnmount(() => {
    if (pending) flush()
    else clearTimer()
  })

  return { onKeyDown, flush, pendingText }
}
