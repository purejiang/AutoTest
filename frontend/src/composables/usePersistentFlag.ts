// localStorage 布尔开关：统一处理 '1'/'0' 编码、隐私模式/禁用 storage 的降级。
// 返回可写 computed，模板里可直接 v-model；写入时同步持久化。
import { computed, ref, type WritableComputedRef } from 'vue'

function readStoredFlag(key: string, defaultValue: boolean): boolean {
  try {
    const stored = window.localStorage.getItem(key)
    if (stored === '0') return false
    if (stored === '1') return true
  } catch {
    // 隐私模式 / 禁用 storage：用默认值
  }
  return defaultValue
}

function writeStoredFlag(key: string, value: boolean): void {
  try {
    window.localStorage.setItem(key, value ? '1' : '0')
  } catch {
    // 存不下就只在本次会话生效
  }
}

export function usePersistentFlag(key: string, defaultValue = true): WritableComputedRef<boolean> {
  const value = ref(readStoredFlag(key, defaultValue))
  return computed({
    get: () => value.value,
    set: (next: boolean) => {
      value.value = next
      writeStoredFlag(key, next)
    },
  })
}
