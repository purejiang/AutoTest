import { describe, expect, it } from 'vitest'

import {
  buildToken,
  collectUsedVariables,
  declaredVariables,
  diffOverrides,
  insertToken,
  loadRememberedRunVars,
  mergeVariableValues,
  missingVariables,
  parseVariableNames,
  saveRememberedRunVars,
  clearRememberedRunVars,
  validVariableName,
} from './scriptVars'

describe('parseVariableNames', () => {
  it('extracts names, trims spaces and de-duplicates preserving order', () => {
    expect(parseVariableNames('{{a}} {{ b }} {{a}} {{c.d}}')).toEqual(['a', 'b', 'c.d'])
    expect(parseVariableNames('no vars here')).toEqual([])
    expect(parseVariableNames('{{bad name}}')).toEqual([])
  })
})

describe('collectUsedVariables', () => {
  it('walks nested objects/arrays and returns sorted unique names', () => {
    const data = {
      text: '{{b}}',
      list: ['{{a}}', { nested: '{{b}}', n: 3 }],
      when: { condition: { text: '{{c}}' } },
    }
    expect(collectUsedVariables(data)).toEqual(['a', 'b', 'c'])
  })

  it('returns empty for non-string JSON', () => {
    expect(collectUsedVariables({ a: 1, b: true, c: null })).toEqual([])
  })
})

describe('variable helpers', () => {
  it('validates names with the same charset as the backend', () => {
    expect(validVariableName('user.name-1_x')).toBe(true)
    expect(validVariableName('user name')).toBe(false)
    expect(validVariableName('')).toBe(false)
  })

  it('normalizes declared variables to strings only', () => {
    expect(declaredVariables({ a: '1', b: 2, c: 'x' })).toEqual({ a: '1', c: 'x' })
    expect(declaredVariables(null)).toEqual({})
  })

  it('finds missing and merges overrides', () => {
    expect(missingVariables({ a: '' }, ['a', 'b'])).toEqual(['b'])
    expect(mergeVariableValues({ a: '1' }, { a: '2', b: '3' })).toEqual({ a: '2', b: '3' })
  })

  it('only keeps real overrides', () => {
    expect(diffOverrides({ a: '1' }, { a: '1', b: '2', c: '' })).toEqual({ b: '2', c: '' })
  })
})

describe('insertToken', () => {
  it('builds the token', () => {
    expect(buildToken('user')).toBe('{{user}}')
  })

  it('inserts at a selection and places the caret after the token', () => {
    expect(insertToken('hello', 'x', 2, 4)).toEqual({ text: 'he{{x}}o', caret: 7 })
  })

  it('clamps out-of-range selection and swaps reversed ranges', () => {
    expect(insertToken('abc', 'v', 10, 20)).toEqual({ text: 'abc{{v}}', caret: 8 })
    expect(insertToken('abc', 'v', 2, 1)).toEqual({ text: 'a{{v}}c', caret: 6 })
  })

  it('appends when no selection is given', () => {
    expect(insertToken('abc', 'v')).toEqual({ text: 'abc{{v}}', caret: 8 })
  })
})

describe('remembered run vars', () => {
  it('saves / loads / clears per script id', () => {
    saveRememberedRunVars('s1', { a: '1' })
    expect(loadRememberedRunVars('s1')).toEqual({ a: '1' })
    clearRememberedRunVars('s1')
    expect(loadRememberedRunVars('s1')).toEqual({})
  })

  it('removes storage when there is no override', () => {
    saveRememberedRunVars('s2', { a: '1' })
    saveRememberedRunVars('s2', {})
    expect(loadRememberedRunVars('s2')).toEqual({})
  })

  it('ignores blank ids', () => {
    expect(loadRememberedRunVars('')).toEqual({})
    expect(() => saveRememberedRunVars('', { a: '1' })).not.toThrow()
  })
})
