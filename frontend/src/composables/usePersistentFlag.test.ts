import { effectScope } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { usePersistentFlag } from './usePersistentFlag'

function setup(key: string, defaultValue?: boolean) {
  const scope = effectScope()
  const flag = scope.run(() => usePersistentFlag(key, defaultValue))!
  return { flag, scope }
}

afterEach(() => {
  window.localStorage.clear()
})

describe('usePersistentFlag', () => {
  it('uses the default when nothing is stored', () => {
    const { flag, scope } = setup('test.flag.default')
    expect(flag.value).toBe(true)
    scope.stop()
    const { flag: off, scope: offScope } = setup('test.flag.default-off', false)
    expect(off.value).toBe(false)
    offScope.stop()
  })

  it("reads '1' / '0' and ignores other values", () => {
    window.localStorage.setItem('test.flag.one', '1')
    window.localStorage.setItem('test.flag.zero', '0')
    window.localStorage.setItem('test.flag.bad', 'yes')
    expect(setup('test.flag.one').flag.value).toBe(true)
    expect(setup('test.flag.zero').flag.value).toBe(false)
    expect(setup('test.flag.bad', false).flag.value).toBe(false)
  })

  it('writes the flag back to localStorage', () => {
    const { flag, scope } = setup('test.flag.write')
    flag.value = false
    expect(window.localStorage.getItem('test.flag.write')).toBe('0')
    flag.value = true
    expect(window.localStorage.getItem('test.flag.write')).toBe('1')
    scope.stop()
  })

  it('degrades silently when storage throws', () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied')
    })
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied')
    })
    const { flag, scope } = setup('test.flag.denied')
    expect(flag.value).toBe(true)
    expect(() => {
      flag.value = false
    }).not.toThrow()
    expect(flag.value).toBe(false)
    scope.stop()
    getItem.mockRestore()
    setItem.mockRestore()
  })
})
