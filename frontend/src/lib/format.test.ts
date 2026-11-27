import { describe, expect, it } from 'vitest'

import {
  formatBytes,
  formatDuration,
  formatShortDateTime,
  isActiveRunStatus,
  isTerminalRunStatus,
  numberOrNull,
  runDurationMs,
  runEventLabel,
  runEventTone,
  runProgress,
  runStatusMeta,
  settingSourceMeta,
  sourceMeta,
  stepStatusMeta,
  summarizeRunEvent,
} from './format'

describe('formatDuration', () => {
  it('handles empty/invalid values', () => {
    expect(formatDuration()).toBe('—')
    expect(formatDuration(null)).toBe('—')
    expect(formatDuration(Number.NaN)).toBe('—')
  })

  it('formats ms / seconds / minutes / hours', () => {
    expect(formatDuration(0)).toBe('0 ms')
    expect(formatDuration(999)).toBe('999 ms')
    expect(formatDuration(1000)).toBe('1.00 s')
    expect(formatDuration(1500)).toBe('1.50 s')
    expect(formatDuration(65_000)).toBe('1m 05s')
    expect(formatDuration(3_661_000)).toBe('1h 01m')
    expect(formatDuration(-5)).toBe('0 ms')
  })
})

describe('formatBytes', () => {
  it('handles empty values', () => {
    expect(formatBytes()).toBe('—')
    expect(formatBytes(null)).toBe('—')
  })

  it('formats B/KB/MB with fixed precision', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1023)).toBe('1023 B')
    expect(formatBytes(1024)).toBe('1.00 KB')
    expect(formatBytes(10 * 1024)).toBe('10.0 KB')
    expect(formatBytes(100 * 1024)).toBe('100 KB')
    expect(formatBytes(1024 * 1024)).toBe('1.00 MB')
  })
})

describe('runProgress', () => {
  it('returns zero for empty totals', () => {
    expect(runProgress({ total_steps: 0 })).toEqual({ current: 0, total: 0, percent: 0 })
    expect(runProgress({})).toEqual({ current: 0, total: 0, percent: 0 })
  })

  it('counts the current node as processed', () => {
    expect(runProgress({ status: 'running', total_steps: 10, step_index: 0 })).toEqual({
      current: 1,
      total: 10,
      percent: 10,
    })
    expect(runProgress({ status: 'running', total_steps: 10, step_index: 99 })).toEqual({
      current: 10,
      total: 10,
      percent: 100,
    })
  })

  it('special-cases queued / succeeded', () => {
    expect(runProgress({ status: 'queued', total_steps: 5, step_index: 2 })).toEqual({
      current: 0,
      total: 5,
      percent: 0,
    })
    expect(runProgress({ status: 'succeeded', total_steps: 5, step_index: 2 })).toEqual({
      current: 5,
      total: 5,
      percent: 100,
    })
  })

  it('falls back to summary.total_steps', () => {
    expect(runProgress({ status: 'running', summary: { total_steps: 4 }, step_index: 1 })).toEqual({
      current: 2,
      total: 4,
      percent: 50,
    })
  })
})

describe('status meta', () => {
  it('maps known run / step statuses and keeps unknown labels', () => {
    expect(runStatusMeta('succeeded')).toEqual({ label: '成功', tone: 'green' })
    expect(runStatusMeta('weird')).toEqual({ label: 'weird', tone: 'gray' })
    expect(runStatusMeta()).toEqual({ label: '未知', tone: 'gray' })
    expect(stepStatusMeta('failed')).toEqual({ label: '失败', tone: 'red' })
    expect(stepStatusMeta('running')).toEqual({ label: '执行中', tone: 'blue' })
  })

  it('maps script source and setting source', () => {
    expect(sourceMeta('mirror')).toEqual({ label: '镜像录制', tone: 'green' })
    expect(sourceMeta('unknown')).toEqual({ label: 'unknown', tone: 'gray' })
    expect(settingSourceMeta('file')).toEqual({ label: '配置文件', tone: 'blue' })
    expect(settingSourceMeta()).toEqual({ label: '默认值', tone: 'gray' })
  })

  it('separates active / terminal statuses', () => {
    expect(isActiveRunStatus('paused')).toBe(true)
    expect(isActiveRunStatus('succeeded')).toBe(false)
    expect(isTerminalRunStatus('succeeded')).toBe(true)
    expect(isTerminalRunStatus('running')).toBe(false)
  })
})

describe('run events', () => {
  it('picks tone by event type and payload', () => {
    expect(runEventTone('step_finished', { status: 'failed' })).toBe('red')
    expect(runEventTone('step_finished', { status: 'passed' })).toBe('green')
    expect(runEventTone('assert_result', { passed: false })).toBe('red')
    expect(runEventTone('when_result', { met: false })).toBe('amber')
    expect(runEventTone('unknown')).toBe('gray')
  })

  it('summarizes run_started and labels special events', () => {
    expect(runEventLabel('when_started')).toBe('条件检查')
    expect(runEventLabel('step_started')).toBe('step_started')
    expect(
      summarizeRunEvent({
        type: 'run_started',
        payload: { script_name: '登录', device_serial: 'fake-1', total_steps: 3 },
      }),
    ).toBe('登录 → fake-1（共 3 步）')
  })
})

describe('misc formatters', () => {
  it('formats short dates defensively', () => {
    expect(formatShortDateTime()).toBe('—')
    expect(formatShortDateTime('not-a-date')).toBe('not-a-date')
  })

  it('parses numbers defensively', () => {
    expect(numberOrNull('12')).toBe(12)
    expect(numberOrNull('abc')).toBeNull()
    expect(numberOrNull('')).toBeNull()
  })

  it('prefers summary.duration_ms and falls back to timestamps', () => {
    expect(runDurationMs({ summary: { duration_ms: 123 } })).toBe(123)
    expect(runDurationMs({ started_at: '2026-01-01T00:00:00Z', ended_at: '2026-01-01T00:00:02Z' })).toBe(2000)
    expect(runDurationMs({})).toBeNull()
  })
})
