// 镜像画布指针手势 → 手动控制。
//
// 两种模式（自动选择）：
//  1) **流式触摸（首选）**：pointerdown 立即下发 down，pointermove 持续下发 move（合并最新位置，
//     保证同一时刻只有一个请求在飞），pointerup 收尾 up。设备端实时跟手，拖动过程有画面反馈。
//     后端 `POST /devices/{serial}/touch` 直接把消息写进 scrcpy control socket（~5ms/条）。
//  2) **一次性手势（回落）**：无镜像会话/后端不支持时，沿用旧行为——在本地判定
//     tap / long_press / swipe / drag，松手后一次性发 `tap|long_press|swipe`。
//
// 坐标始终是相对视频帧的归一化 nx,ny ∈ [0,1]（后端换算，前端不做设备像素换算）。

import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { errorMessage, post } from '../lib/api'

const TAP_MAX_MS = 500
const LONG_PRESS_MS = 500
const MOVE_THRESHOLD_PX = 10
const WHEEL_DEBOUNCE_MS = 150
const WHEEL_SWIPE_DURATION_MS = 220
const UP_FLUSH_TIMEOUT_MS = 600
// 流式触摸投递节拍：pointermove 只记位置（零 React 状态更新），由定时器按 8ms 投递，
// 保证手机上收到的是**等间隔、连续**的移动采样（滚动量/惯性与真实手势一致）。
const PUMP_INTERVAL_MS = 8
// 位置队列：拖动很快时保留最近 N 个采样点，队列满才丢最旧的（而不是只留最后一个）
const MAX_MOVE_QUEUE = 12

export interface PointerGesturesOptions {
  serial: string
  canvasRef: React.RefObject<HTMLCanvasElement>
  /** 仅在出流（streaming）时接受输入 */
  enabled: boolean
  onError?: (message: string) => void
  /**
   * 首选触摸通道：镜像 WS（"down"|"move"|"up", nx, ny）→ 返回 false 表示不可用。
   * 提供时优先使用（无 HTTP 开销、严格有序）；否则回落 REST /touch。
   */
  wsTouch?: (action: 'down' | 'move' | 'up', nx: number, ny: number) => boolean
}

export interface PointerGestureHandlers {
  onPointerDown: (event: ReactPointerEvent<HTMLCanvasElement>) => void
  onPointerMove: (event: ReactPointerEvent<HTMLCanvasElement>) => void
  onPointerUp: (event: ReactPointerEvent<HTMLCanvasElement>) => void
  onPointerCancel: (event: ReactPointerEvent<HTMLCanvasElement>) => void
}

interface DragState {
  pointerId: number
  startClientX: number
  startClientY: number
  startedAt: number
  nx: number
  ny: number
  moved: boolean
  /** 长按已经提交，抬起时不再判定 tap/swipe（仅回落模式使用） */
  committed: boolean
  /** 本次手势是否走流式触摸 */
  streaming: boolean
}

interface NormalizedPoint {
  nx: number
  ny: number
}

type StreamAction = 'down' | 'move' | 'up'

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0
  return Math.min(1, Math.max(0, value))
}

function devicePath(serial: string, action: string): string {
  return `/devices/${encodeURIComponent(serial)}/${action}`
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export function usePointerGestures(options: PointerGesturesOptions): {
  handlers: PointerGestureHandlers
  busy: boolean
} {
  const { serial, canvasRef, enabled, onError, wsTouch } = options
  const wsTouchRef = useRef(wsTouch)
  wsTouchRef.current = wsTouch

  const enabledRef = useRef(enabled)
  enabledRef.current = enabled
  const serialRef = useRef(serial)
  serialRef.current = serial
  const onErrorRef = useRef(onError)
  onErrorRef.current = onError

  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const dragRef = useRef<DragState | null>(null)
  const longPressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  /** 流式触摸是否可用：null=未知（尚未失败过） */
  const streamSupportedRef = useRef<boolean | null>(null)
  /** 同一时刻只允许一个 touch 请求在飞（保证 down→move→up 顺序） */
  const inFlightRef = useRef(false)
  /** 待发送的 move 采样队列（按时间顺序，投递时逐条发出） */
  const moveQueueRef = useRef<NormalizedPoint[]>([])
  /** 投递定时器（拖动期间运行） */
  const pumpTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 切设备时重置能力探测
  useEffect(() => {
    streamSupportedRef.current = null
    moveQueueRef.current = []
    inFlightRef.current = false
  }, [serial])

  const setBusyBoth = useCallback((value: boolean): void => {
    busyRef.current = value
    setBusy(value)
  }, [])

  const clearLongPressTimer = useCallback((): void => {
    if (longPressTimerRef.current !== null) {
      clearTimeout(longPressTimerRef.current)
      longPressTimerRef.current = null
    }
  }, [])

  const normalize = useCallback(
    (clientX: number, clientY: number): NormalizedPoint | null => {
      const canvas = canvasRef.current
      if (!canvas) return null
      const rect = canvas.getBoundingClientRect()
      if (rect.width <= 0 || rect.height <= 0) return null
      return {
        nx: clamp01((clientX - rect.left) / rect.width),
        ny: clamp01((clientY - rect.top) / rect.height),
      }
    },
    [canvasRef],
  )

  /** 一次性手动控制（回落模式）：tap / long_press / swipe */
  const fire = useCallback(
    async (action: string, body: Record<string, unknown>): Promise<void> => {
      setBusyBoth(true)
      try {
        await post<void>(devicePath(serialRef.current, action), body)
      } catch (err) {
        onErrorRef.current?.(`${action} 失败：${errorMessage(err)}`)
      } finally {
        setBusyBoth(false)
      }
    },
    [setBusyBoth],
  )

  /** 首选通道：镜像 WS（同步、有序、无 HTTP 开销）。返回 false 表示没发出去。 */
  const tryWsTouch = useCallback((action: 'down' | 'move' | 'up', point: NormalizedPoint): boolean => {
    const fn = wsTouchRef.current
    if (!fn) return false
    try {
      return fn(action, Number(point.nx.toFixed(5)), Number(point.ny.toFixed(5)))
    } catch {
      return false
    }
  }, [])

  /** REST 兜底：流式触摸单条消息；失败则永久降级到一次性手势 */
  const sendTouch = useCallback(async (action: StreamAction, point: NormalizedPoint): Promise<boolean> => {
    try {
      await post<void>(devicePath(serialRef.current, 'touch'), {
        action,
        nx: Number(point.nx.toFixed(5)),
        ny: Number(point.ny.toFixed(5)),
      })
      return true
    } catch (err) {
      streamSupportedRef.current = false
      moveQueueRef.current = []
      onErrorRef.current?.(
        `实时拖动不可用（${errorMessage(err)}），已回落到松手后执行手势；启动镜像后可恢复实时跟手。`,
      )
      return false
    }
  }, [])

  /** 投递一条排队中的 move（同一时刻只有一条在飞，保证顺序） */
  const pump = useCallback(async (): Promise<void> => {
    if (streamSupportedRef.current === false) return
    // 首选 WS：同步写出即可，队列一次排空（WS 本身有序，不需要 in-flight 串行）
    if (wsTouchRef.current) {
      while (moveQueueRef.current.length > 0) {
        const next = moveQueueRef.current.shift()
        if (!next) break
        if (!tryWsTouch('move', next)) {
          moveQueueRef.current.unshift(next) // 放回，交给 REST 兜底
          break
        }
      }
      if (moveQueueRef.current.length > 0) {
        // WS 失败 → 走 REST
        await pumpRest()
      }
      return
    }
    await pumpRest()
  }, [tryWsTouch])

  const pumpRest = useCallback(async (): Promise<void> => {
    if (inFlightRef.current || streamSupportedRef.current === false) return
    const next = moveQueueRef.current.shift()
    if (!next) return
    inFlightRef.current = true
    try {
      await sendTouch('move', next)
    } finally {
      inFlightRef.current = false
    }
    if (moveQueueRef.current.length > 0) {
      void pump()
    }
  }, [sendTouch, pump])

  const startPump = useCallback((): void => {
    if (pumpTimerRef.current !== null) return
    pumpTimerRef.current = setInterval(() => {
      void pump()
    }, PUMP_INTERVAL_MS)
  }, [pump])

  const stopPump = useCallback((): void => {
    if (pumpTimerRef.current !== null) {
      clearInterval(pumpTimerRef.current)
      pumpTimerRef.current = null
    }
  }, [])

  /** 等待在飞请求结束（最多 UP_FLUSH_TIMEOUT_MS），保证 up 顺序在 move 之后 */
  const waitIdle = useCallback(async (): Promise<void> => {
    const deadline = Date.now() + UP_FLUSH_TIMEOUT_MS
    while (inFlightRef.current && Date.now() < deadline) {
      await sleep(2)
    }
  }, [])

  /** 回落模式：根据位移/时长判定手势并一次性下发 */
  const fireBuffered = useCallback(
    async (drag: DragState, end: NormalizedPoint): Promise<void> => {
      const durationMs = Math.max(0, Date.now() - drag.startedAt)
      const rect = canvasRef.current?.getBoundingClientRect()
      const dx = rect ? (end.nx - drag.nx) * rect.width : 0
      const dy = rect ? (end.ny - drag.ny) * rect.height : 0
      const distance = Math.hypot(dx, dy)
      if (distance >= MOVE_THRESHOLD_PX) {
        await fire('swipe', {
          nx1: drag.nx,
          ny1: drag.ny,
          nx2: end.nx,
          ny2: end.ny,
          duration_ms: Math.min(10_000, Math.max(50, Math.round(durationMs))),
        })
        return
      }
      if (durationMs >= TAP_MAX_MS) {
        await fire('long_press', { nx: end.nx, ny: end.ny, duration_ms: Math.min(10_000, durationMs) })
        return
      }
      await fire('tap', { nx: end.nx, ny: end.ny })
    },
    [canvasRef, fire],
  )

  // ---- 事件处理 ----
  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLCanvasElement>): void => {
      if (!enabledRef.current) return
      const point = normalize(event.clientX, event.clientY)
      if (!point) return
      // 只处理主指针（忽略多指，避免拖动时第二个手指打乱状态）
      if (dragRef.current) return
      try {
        event.currentTarget.setPointerCapture(event.pointerId)
      } catch {
        /* 某些浏览器对合成事件会抛，忽略 */
      }
      const streaming = streamSupportedRef.current !== false
      const drag: DragState = {
        pointerId: event.pointerId,
        startClientX: event.clientX,
        startClientY: event.clientY,
        startedAt: Date.now(),
        nx: point.nx,
        ny: point.ny,
        moved: false,
        committed: false,
        streaming,
      }
      dragRef.current = drag

      if (streaming) {
        // 立即下发 down：设备端马上产生按下效果（WS 优先，毫秒级）
        if (tryWsTouch('down', point)) {
          startPump()
          return
        }
        inFlightRef.current = true
        void (async () => {
          const ok = await sendTouch('down', point)
          inFlightRef.current = false
          if (!ok) {
            // 降级到一次性手势：继续走 pointerup 判定
            const current = dragRef.current
            if (current) current.streaming = false
            setBusyBoth(false)
            return
          }
          startPump()
        })()
        return
      }

      // 回落模式：长按由定时器兜底
      longPressTimerRef.current = setTimeout(() => {
        const current = dragRef.current
        if (!current || current.moved) return
        current.committed = true
        clearLongPressTimer()
        void fire('long_press', {
          nx: current.nx,
          ny: current.ny,
          duration_ms: Math.max(LONG_PRESS_MS, Date.now() - current.startedAt),
        })
      }, LONG_PRESS_MS)
    },
    [clearLongPressTimer, fire, normalize, pump, sendTouch, setBusyBoth, startPump],
  )

  const onPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLCanvasElement>): void => {
      const drag = dragRef.current
      if (!drag || drag.pointerId !== event.pointerId) return
      const point = normalize(event.clientX, event.clientY)
      if (!point) return
      const rect = canvasRef.current?.getBoundingClientRect()
      const dx = rect ? (point.nx - drag.nx) * rect.width : 0
      const dy = rect ? (point.ny - drag.ny) * rect.height : 0
      if (Math.hypot(dx, dy) >= MOVE_THRESHOLD_PX) {
        drag.moved = true
        if (drag.streaming) clearLongPressTimer()
      }
      if (drag.streaming) {
        // 只入队 + 尝试投递：不触发 React 状态更新，避免 60Hz 重渲染把 pointermove 采样拖丢。
        // 浏览器会在主线程忙时合并 pointermove（帧对齐），getCoalescedEvents() 能拿到被合并掉的
        // 原始采样点——把这些点也发给手机，拖动/滚动轨迹才与手指一致。
        const queue = moveQueueRef.current
        const native = event.nativeEvent as PointerEvent & {
          getCoalescedEvents?: () => PointerEvent[]
        }
        let added = false
        if (typeof native.getCoalescedEvents === 'function') {
          for (const sample of native.getCoalescedEvents()) {
            const p = normalize(sample.clientX, sample.clientY)
            if (p) {
              queue.push(p)
              added = true
            }
          }
        }
        if (!added) queue.push(point)
        if (queue.length > MAX_MOVE_QUEUE) queue.splice(0, queue.length - MAX_MOVE_QUEUE)
        void pump()
        return
      }
      if (drag.moved) clearLongPressTimer()
    },
    [canvasRef, clearLongPressTimer, normalize, pump],
  )

  const finishGesture = useCallback(
    (event: ReactPointerEvent<HTMLCanvasElement>, cancelled: boolean): void => {
      const drag = dragRef.current
      if (!drag || drag.pointerId !== event.pointerId) return
      dragRef.current = null
      clearLongPressTimer()
      const point = normalize(event.clientX, event.clientY) ?? { nx: drag.nx, ny: drag.ny }
      try {
        event.currentTarget.releasePointerCapture(event.pointerId)
      } catch {
        /* ignore */
      }

      if (drag.streaming) {
        // 先把剩余采样发完，再发 up（WS 通道同步发即可；REST 通道需要等待）
        if (wsTouchRef.current) {
          stopPump()
          while (moveQueueRef.current.length > 0) {
            const next = moveQueueRef.current.shift()
            if (!next) break
            if (!tryWsTouch('move', next)) {
              moveQueueRef.current.unshift(next)
              break
            }
          }
          tryWsTouch('up', point)
          return
        }
        void (async () => {
          stopPump()
          // 尾部落点入队，保证松开位置也发给手机（否则滚动会"提前结束"）
          moveQueueRef.current.push(point)
          const deadline = Date.now() + UP_FLUSH_TIMEOUT_MS
          while (moveQueueRef.current.length > 0 && Date.now() < deadline) {
            await pump()
          }
          await waitIdle()
          inFlightRef.current = true
          await sendTouch('up', point)
          inFlightRef.current = false
          void cancelled
        })()
        return
      }
      // 回落模式
      if (drag.committed) return
      void fireBuffered(drag, point)
    },
    [clearLongPressTimer, fireBuffered, normalize, sendTouch, stopPump, waitIdle],
  )

  const onPointerUp = useCallback(
    (event: ReactPointerEvent<HTMLCanvasElement>): void => {
      finishGesture(event, false)
    },
    [finishGesture],
  )

  const onPointerCancel = useCallback(
    (event: ReactPointerEvent<HTMLCanvasElement>): void => {
      finishGesture(event, true)
    },
    [finishGesture],
  )

  // ---- 滚轮：上下滑动（保持一次性 swipe 语义，滚轮本身是离散的） ----
  const wheelTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wheelAccumRef = useRef(0)
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !enabled) return
    const onWheel = (event: WheelEvent): void => {
      event.preventDefault()
      wheelAccumRef.current += event.deltaY
      if (wheelTimerRef.current !== null) return
      wheelTimerRef.current = setTimeout(() => {
        wheelTimerRef.current = null
        const accum = wheelAccumRef.current
        wheelAccumRef.current = 0
        if (!Number.isFinite(accum) || Math.abs(accum) < 1) return
        const rect = canvas.getBoundingClientRect()
        if (rect.height <= 0) return
        const cx = 0.5
        const cy = 0.5
        const ratio = Math.min(0.35, Math.max(0.08, Math.abs(accum) / 1200))
        const dy = accum > 0 ? ratio : -ratio
        void fire('swipe', {
          nx1: cx,
          ny1: Math.min(0.95, Math.max(0.05, cy + dy / 2)),
          nx2: cx,
          ny2: Math.min(0.95, Math.max(0.05, cy - dy / 2)),
          duration_ms: WHEEL_SWIPE_DURATION_MS,
        })
      }, WHEEL_DEBOUNCE_MS)
    }
    canvas.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      canvas.removeEventListener('wheel', onWheel)
      if (wheelTimerRef.current !== null) {
        clearTimeout(wheelTimerRef.current)
        wheelTimerRef.current = null
      }
    }
  }, [canvasRef, enabled, fire])

  // 卸载时清定时器
  useEffect(() => {
    return () => {
      clearLongPressTimer()
      stopPump()
      if (wheelTimerRef.current !== null) clearTimeout(wheelTimerRef.current)
    }
  }, [clearLongPressTimer, stopPump])

  return {
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel },
    busy,
  }
}
