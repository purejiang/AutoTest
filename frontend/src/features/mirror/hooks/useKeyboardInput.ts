// 键盘输入 -> Android 文本/返回键（契约 §7.1）。
//
// 规则：
//   - 焦点在镜像面板时，可打印字符进本地缓冲，600ms 防抖后 POST .../text {text}；
//   - 回车立即发送缓冲文本；
//   - Esc / 空缓冲时的 Backspace 映射为 key=back（POST .../key {key:"back"}）；
//   - 面板内输入框（录制名称等）获得焦点时不拦截，避免和表单输入冲突。

import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { errorMessage, post } from '../lib/api'

const TEXT_DEBOUNCE_MS = 600

export interface KeyboardInputOptions {
  serial: string
  enabled: boolean
  onError?: (message: string) => void
}

export interface KeyboardInputApi {
  onKeyDown: (event: ReactKeyboardEvent<HTMLElement>) => void
  /** 立即发送缓冲中的文本（回车/失焦时调用） */
  flush: () => void
  /** 当前未发送的文本（UI 提示用） */
  pendingText: string
}

const EDITABLE_SELECTOR =
  'input, textarea, select, [contenteditable="true"], [contenteditable=""]'

function devicePath(serial: string, action: string): string {
  return `/devices/${encodeURIComponent(serial)}/${action}`
}

export function useKeyboardInput(options: KeyboardInputOptions): KeyboardInputApi {
  const { serial, enabled, onError } = options

  const [pendingText, setPendingText] = useState('')
  const pendingRef = useRef('')
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const enabledRef = useRef(enabled)
  enabledRef.current = enabled
  const serialRef = useRef(serial)
  serialRef.current = serial
  const onErrorRef = useRef(onError)
  onErrorRef.current = onError

  const clearTimer = useCallback((): void => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const postKey = useCallback((key: string): void => {
    void (async () => {
      try {
        await post<void>(devicePath(serialRef.current, 'key'), { key })
      } catch (err) {
        onErrorRef.current?.(`按键 ${key} 失败：${errorMessage(err)}`)
      }
    })()
  }, [])

  const flush = useCallback((): void => {
    clearTimer()
    const text = pendingRef.current
    if (!text) return
    pendingRef.current = ''
    setPendingText('')
    void (async () => {
      try {
        await post<void>(devicePath(serialRef.current, 'text'), { text })
      } catch (err) {
        onErrorRef.current?.(`文本发送失败：${errorMessage(err)}`)
      }
    })()
  }, [clearTimer])

  const scheduleFlush = useCallback((): void => {
    clearTimer()
    timerRef.current = setTimeout(() => {
      timerRef.current = null
      flush()
    }, TEXT_DEBOUNCE_MS)
  }, [clearTimer, flush])

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLElement>): void => {
      if (!enabledRef.current) return
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
        if (pendingRef.current.length > 0) {
          pendingRef.current = pendingRef.current.slice(0, -1)
          setPendingText(pendingRef.current)
          scheduleFlush()
        } else {
          postKey('back')
        }
        return
      }
      // 可打印字符（含空格/标点）；中文输入法的组合输入 key 通常是 'Process'，不拦截
      if (key.length === 1) {
        event.preventDefault()
        pendingRef.current += key
        setPendingText(pendingRef.current)
        scheduleFlush()
      }
    },
    [flush, postKey, scheduleFlush],
  )

  // 卸载时把缓冲文本尽力发出去，避免用户最后几个字符丢失
  useEffect(() => {
    return () => {
      if (pendingRef.current) flush()
      else clearTimer()
    }
  }, [flush, clearTimer])

  // 切换设备时丢弃旧设备未发送的文本
  useEffect(() => {
    pendingRef.current = ''
    setPendingText('')
    clearTimer()
  }, [serial, clearTimer])

  return { onKeyDown, flush, pendingText }
}
