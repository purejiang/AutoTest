// 键盘输入 -> Android 文本/返回键（契约 §7.1）。
//
// 规则：
//   - 焦点在镜像面板时，可打印字符进本地缓冲，最多 120ms 就 POST .../text {text}；
//   - 回车立即发送缓冲文本；Esc / 空缓冲时的 Backspace 映射为 key=back；
//   - 所有文本/按键请求串行发送，避免并发 HTTP 打乱输入顺序；
//   - 面板内输入框（录制名称等）获得焦点时不拦截。

import { onBeforeUnmount, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'

import { errorMessage, post } from '../lib/api'

const TEXT_FLUSH_INTERVAL_MS = 120
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
  // 输入必须按顺序落到设备：前一个 POST 不结束，后一个不发出
  let sendChain: Promise<void> = Promise.resolve()

  function clearTimer(): void {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  function enqueueSend(task: () => Promise<void>): void {
    sendChain = sendChain.then(task).catch(() => {
      /* 单个请求失败已由 task 内部提示，链继续 */
    })
  }

  function postKey(key: string): void {
    const targetSerial = toValue(options.serial)
    enqueueSend(async () => {
      try {
        await post<void>(devicePath(targetSerial, 'key'), { key })
      } catch (err) {
        options.onError?.(`按键 ${key} 失败：${errorMessage(err)}`)
      }
    })
  }

  function flush(): void {
    clearTimer()
    const text = pending
    if (!text) return
    pending = ''
    pendingText.value = ''
    const targetSerial = toValue(options.serial)
    enqueueSend(async () => {
      try {
        await post<void>(devicePath(targetSerial, 'text'), { text })
      } catch (err) {
        options.onError?.(`文本发送失败：${errorMessage(err)}`)
      }
    })
  }

  // 节流而不是防抖：持续打字时也每 120ms 落一次，不等用户停手
  function scheduleFlush(): void {
    if (timer !== null) return
    timer = setTimeout(() => {
      timer = null
      flush()
    }, TEXT_FLUSH_INTERVAL_MS)
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
