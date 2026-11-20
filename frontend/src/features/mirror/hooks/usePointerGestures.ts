// 镜像画布指针手势 -> 归一化坐标 -> REST 手动控制（契约 §6.2/§7.1）。
//
// 坐标约定：nx,ny ∈ [0,1]，相对视频帧（不是相对 canvas 元素）。因为 canvas 已经是
// contain 后的内容盒，直接按 canvas.getBoundingClientRect() 归一化即可。
//
// 手势判定：
//   - 按下到抬起 <500ms 且位移 <10px        -> tap        POST /api/devices/{serial}/tap {nx,ny}
//   - 按下后 ≥500ms 保持不动                -> long_press POST .../long_press {nx,ny,duration_ms}
//   - 位移 ≥10px（任意时长）                -> swipe      POST .../swipe {nx1,ny1,nx2,ny2,duration_ms}
//   - 滚轮：按竖直方向 ffi 上下滑动（可选）  -> swipe
// 约束：
//   - pointer capture 防止拖出画布丢失事件；
//   - in-flight 标记（busyRef）防止重复提交；长按已提交后抬起不再补 tap；
//   - 面板 enabled=false（未出流）时完全忽略输入。

import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { errorMessage, post } from '../lib/api'

const TAP_MAX_MS = 500
const LONG_PRESS_MS = 500
const MOVE_THRESHOLD_PX = 10
const WHEEL_DEBOUNCE_MS = 150
const WHEEL_SWIPE_DURATION_MS = 220

export interface PointerGesturesOptions {
  serial: string
  canvasRef: React.RefObject<HTMLCanvasElement>
  /** 仅在出流（streaming）时接受输入 */
  enabled: boolean
  onError?: (message: string) => void
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
  /** 长按已经提交，抬起时不再判定 tap/swipe */
  committed: boolean
}

interface NormalizedPoint {
  nx: number
  ny: number
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0
  return Math.min(1, Math.max(0, value))
}

function devicePath(serial: string, action: string): string {
  return `/devices/${encodeURIComponent(serial)}/${action}`
}

export function usePointerGestures(options: PointerGesturesOptions): {
  handlers: PointerGestureHandlers
  busy: boolean
} {
  const { serial, canvasRef, enabled, onError } = options

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

  /** 发一条手动控制 REST；用 busy 标记串行化，避免连点/重复提交。 */
  const fire = useCallback(
    async (action: string, body: Record<string, unknown>): Promise<void> => {
      busyRef.current = true
      setBusy(true)
      try {
        await post<void>(devicePath(serialRef.current, action), body)
      } catch (err) {
        onErrorRef.current?.(`${action} 失败：${errorMessage(err)}`)
      } finally {
        busyRef.current = false
        setBusy(false)
      }
    },
    [],
  )

  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLCanvasElement>): void => {
      if (!enabledRef.current) return
      if (event.pointerType === 'mouse' && event.button !== 0) return
      if (dragRef.current || busyRef.current) return
      const point = normalize(event.clientX, event.clientY)
      if (!point) return
      event.preventDefault()
      dragRef.current = {
        pointerId: event.pointerId,
        startClientX: event.clientX,
        startClientY: event.clientY,
        startedAt: typeof performance !== 'undefined' ? performance.now() : Date.now(),
        nx: point.nx,
        ny: point.ny,
        moved: false,
        committed: false,
      }
      busyRef.current = true
      setBusy(true)
      try {
        event.currentTarget.setPointerCapture(event.pointerId)
      } catch {
        /* 某些环境不支持 capture，忽略 */
      }
      longPressTimerRef.current = setTimeout(() => {
        longPressTimerRef.current = null
        const drag = dragRef.current
        if (!drag || drag.moved || !enabledRef.current) return
        drag.committed = true
        const now = typeof performance !== 'undefined' ? performance.now() : Date.now()
        const duration = Math.max(LONG_PRESS_MS, Math.round(now - drag.startedAt))
        void fire('long_press', { nx: drag.nx, ny: drag.ny, duration_ms: duration })
      }, LONG_PRESS_MS)
    },
    [normalize, fire],
  )

  const onPointerMove = useCallback((event: ReactPointerEvent<HTMLCanvasElement>): void => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    if (drag.moved) return
    const dx = event.clientX - drag.startClientX
    const dy = event.clientY - drag.startClientY
    if (Math.hypot(dx, dy) >= MOVE_THRESHOLD_PX) {
      drag.moved = true
      clearLongPressTimer()
    }
  }, [clearLongPressTimer])

  const finish = useCallback(
    (event: ReactPointerEvent<HTMLCanvasElement>, cancelled: boolean): void => {
      const drag = dragRef.current
      if (!drag || drag.pointerId !== event.pointerId) return
      dragRef.current = null
      clearLongPressTimer()
      try {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
          event.currentTarget.releasePointerCapture(event.pointerId)
        }
      } catch {
        /* noop */
      }

      if (drag.committed) {
        // 长按已在计时器里提交；抬起只清状态（busy 由那次 fire 的 finally 复位）
        return
      }

      const now = typeof performance !== 'undefined' ? performance.now() : Date.now()
      const elapsed = now - drag.startedAt
      const distance = Math.hypot(
        event.clientX - drag.startClientX,
        event.clientY - drag.startClientY,
      )
      const end = normalize(event.clientX, event.clientY)

      // 先释放手势锁，再交给 fire 接管（fire 内部会重新置 busy）
      busyRef.current = false
      setBusy(false)

      if (cancelled) return
      if (!drag.moved && distance < MOVE_THRESHOLD_PX) {
        if (elapsed < TAP_MAX_MS) {
          if (end) void fire('tap', { nx: end.nx, ny: end.ny })
        } else {
          // 事件循环迟滞导致计时器没来得及触发：补发长按
          if (end) {
            void fire('long_press', {
              nx: end.nx,
              ny: end.ny,
              duration_ms: Math.min(10_000, Math.max(LONG_PRESS_MS, Math.round(elapsed))),
            })
          }
        }
        return
      }
      if (end) {
        void fire('swipe', {
          nx1: drag.nx,
          ny1: drag.ny,
          nx2: end.nx,
          ny2: end.ny,
          // 后端 SwipeRequest.duration_ms 约束 [50,10000]，快速滑动也不能传 <50
          duration_ms: Math.min(10_000, Math.max(50, Math.round(elapsed))),
        })
      }
    },
    [clearLongPressTimer, normalize, fire],
  )

  const onPointerUp = useCallback(
    (event: ReactPointerEvent<HTMLCanvasElement>): void => finish(event, false),
    [finish],
  )
  const onPointerCancel = useCallback(
    (event: ReactPointerEvent<HTMLCanvasElement>): void => finish(event, true),
    [finish],
  )

  // ---- 滚轮 -> 上下 swipe（可选交互） ----
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    let accumulated = 0
    let timer: ReturnType<typeof setTimeout> | null = null
    let lastClientX = 0
    let lastClientY = 0

    const handleWheel = (event: WheelEvent): void => {
      if (!enabledRef.current) return
      lastClientX = event.clientX
      lastClientY = event.clientY
      event.preventDefault()
      accumulated += event.deltaY
      if (timer !== null) return
      timer = setTimeout(() => {
        timer = null
        const delta = accumulated
        accumulated = 0
        if (!delta || busyRef.current) return
        const point = normalize(lastClientX, lastClientY)
        if (!point) return
        const direction = delta > 0 ? 1 : -1 // 向下滚 = 手指上划（内容向上）
        const ny1 = Math.min(0.85, Math.max(0.15, point.ny))
        const ny2 = Math.min(0.98, Math.max(0.02, ny1 + direction * 0.3))
        void fire('swipe', {
          nx1: point.nx,
          ny1,
          nx2: point.nx,
          ny2,
          duration_ms: WHEEL_SWIPE_DURATION_MS,
        })
      }, WHEEL_DEBOUNCE_MS)
    }

    canvas.addEventListener('wheel', handleWheel, { passive: false })
    return () => {
      canvas.removeEventListener('wheel', handleWheel)
      if (timer !== null) clearTimeout(timer)
    }
  }, [canvasRef, normalize, fire])

  // 卸载时清掉长按计时器
  useEffect(() => {
    return () => {
      clearLongPressTimer()
    }
  }, [clearLongPressTimer])

  return {
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel },
    busy,
  }
}
