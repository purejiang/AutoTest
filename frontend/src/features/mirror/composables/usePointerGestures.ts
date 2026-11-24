// 镜像画布指针手势 → 手动控制。
//
// 两种模式（自动选择）：
//  1) 流式触摸（首选）：pointerdown 立即下发 down，pointermove 持续下发 move（合并最新位置，
//     保证同一时刻只有一个请求在飞），pointerup 收尾 up。设备端实时跟手。
//  2) 一次性手势（回落）：无镜像会话/后端不支持时，在本地判定 tap / long_press / swipe。
//
// 坐标始终是相对视频帧的归一化 nx,ny ∈ [0,1]（后端换算，前端不做设备像素换算）。

import { computed, onBeforeUnmount, ref, toValue, watch, type MaybeRefOrGetter, type Ref } from 'vue'

import { errorMessage, post } from '../lib/api'

const TAP_MAX_MS = 500
const LONG_PRESS_MS = 500
const MOVE_THRESHOLD_PX = 10
const UP_FLUSH_TIMEOUT_MS = 600
const PUMP_INTERVAL_MS = 8
const MAX_MOVE_QUEUE = 12

export interface PointerGesturesOptions {
  serial: MaybeRefOrGetter<string>
  canvasRef: Ref<HTMLCanvasElement | null>
  enabled: MaybeRefOrGetter<boolean>
  onError?: (message: string) => void
  wsTouch?: (action: 'down' | 'move' | 'up', nx: number, ny: number) => boolean
}

export interface PointerGestureHandlers {
  onPointerDown: (event: PointerEvent) => void
  onPointerMove: (event: PointerEvent) => void
  onPointerUp: (event: PointerEvent) => void
  onPointerCancel: (event: PointerEvent) => void
}

interface DragState {
  pointerId: number
  startClientX: number
  startClientY: number
  startedAt: number
  nx: number
  ny: number
  moved: boolean
  committed: boolean
  streaming: boolean
  /** pointerdown 时量到的画布位置：整段手势复用，避免每个 pointermove 都强制布局。 */
  rect: DOMRect | null
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

export function usePointerGestures(options: PointerGesturesOptions) {
  const serial = computed(() => toValue(options.serial))
  const enabled = computed(() => toValue(options.enabled))

  const busy = ref(false)
  let busyValue = false
  let drag: DragState | null = null
  let longPressTimer: ReturnType<typeof setTimeout> | null = null
  let streamSupported: boolean | null = null
  let inFlight = false
  const moveQueue: NormalizedPoint[] = []
  let pumpTimer: ReturnType<typeof setInterval> | null = null

  watch(serial, () => {
    streamSupported = null
    moveQueue.length = 0
    inFlight = false
  })

  function setBusyBoth(value: boolean): void {
    busyValue = value
    busy.value = value
  }

  function clearLongPressTimer(): void {
    if (longPressTimer !== null) {
      clearTimeout(longPressTimer)
      longPressTimer = null
    }
  }

  function normalize(
    clientX: number,
    clientY: number,
    measured?: DOMRect | null,
  ): NormalizedPoint | null {
    const canvas = options.canvasRef.value
    if (!canvas) return null
    const rect = measured ?? canvas.getBoundingClientRect()
    if (rect.width <= 0 || rect.height <= 0) return null
    return {
      nx: clamp01((clientX - rect.left) / rect.width),
      ny: clamp01((clientY - rect.top) / rect.height),
    }
  }

  async function fire(action: string, body: Record<string, unknown>): Promise<void> {
    setBusyBoth(true)
    try {
      await post<void>(devicePath(serial.value, action), body)
    } catch (err) {
      options.onError?.(`${action} 失败：${errorMessage(err)}`)
    } finally {
      setBusyBoth(false)
    }
  }

  function tryWsTouch(action: 'down' | 'move' | 'up', point: NormalizedPoint): boolean {
    const fn = options.wsTouch
    if (!fn) return false
    try {
      return fn(action, Number(point.nx.toFixed(5)), Number(point.ny.toFixed(5)))
    } catch {
      return false
    }
  }

  async function sendTouch(action: StreamAction, point: NormalizedPoint): Promise<boolean> {
    try {
      await post<void>(devicePath(serial.value, 'touch'), {
        action,
        nx: Number(point.nx.toFixed(5)),
        ny: Number(point.ny.toFixed(5)),
      })
      return true
    } catch (err) {
      streamSupported = false
      moveQueue.length = 0
      options.onError?.(
        `实时拖动不可用（${errorMessage(err)}），已回落到松手后执行手势；启动镜像后可恢复实时跟手。`,
      )
      return false
    }
  }

  async function pumpRest(): Promise<void> {
    if (inFlight || streamSupported === false) return
    const next = moveQueue.shift()
    if (!next) return
    inFlight = true
    try {
      await sendTouch('move', next)
    } finally {
      inFlight = false
    }
    if (moveQueue.length > 0) void pump()
  }

  async function pump(): Promise<void> {
    if (streamSupported === false) return
    if (options.wsTouch) {
      while (moveQueue.length > 0) {
        const next = moveQueue.shift()
        if (!next) break
        if (!tryWsTouch('move', next)) {
          moveQueue.unshift(next)
          break
        }
      }
      if (moveQueue.length > 0) await pumpRest()
      return
    }
    await pumpRest()
  }

  function startPump(): void {
    if (pumpTimer !== null) return
    pumpTimer = setInterval(() => {
      void pump()
    }, PUMP_INTERVAL_MS)
  }

  function stopPump(): void {
    if (pumpTimer !== null) {
      clearInterval(pumpTimer)
      pumpTimer = null
    }
  }

  async function waitIdle(): Promise<void> {
    const deadline = Date.now() + UP_FLUSH_TIMEOUT_MS
    while (inFlight && Date.now() < deadline) {
      await sleep(2)
    }
  }

  async function fireBuffered(dragState: DragState, end: NormalizedPoint): Promise<void> {
    const durationMs = Math.max(0, Date.now() - dragState.startedAt)
    const rect = dragState.rect
    const dx = rect ? (end.nx - dragState.nx) * rect.width : 0
    const dy = rect ? (end.ny - dragState.ny) * rect.height : 0
    const distance = Math.hypot(dx, dy)
    if (distance >= MOVE_THRESHOLD_PX) {
      await fire('swipe', {
        nx1: dragState.nx,
        ny1: dragState.ny,
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
  }

  function onPointerDown(event: PointerEvent): void {
    if (!enabled.value) return
    const rect = options.canvasRef.value?.getBoundingClientRect() ?? null
    const point = normalize(event.clientX, event.clientY, rect)
    if (!point) return
    if (drag) return
    try {
      ;(event.currentTarget as HTMLCanvasElement | null)?.setPointerCapture(event.pointerId)
    } catch {
      /* 某些浏览器对合成事件会抛，忽略 */
    }
    const streaming = streamSupported !== false
    const dragState: DragState = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startedAt: Date.now(),
      nx: point.nx,
      ny: point.ny,
      moved: false,
      committed: false,
      streaming,
      rect,
    }
    drag = dragState

    if (streaming) {
      if (tryWsTouch('down', point)) {
        startPump()
        return
      }
      inFlight = true
      void (async () => {
        const ok = await sendTouch('down', point)
        inFlight = false
        if (!ok) {
          if (drag) drag.streaming = false
          setBusyBoth(false)
          return
        }
        startPump()
      })()
      return
    }

    longPressTimer = setTimeout(() => {
      const current = drag
      if (!current || current.moved) return
      current.committed = true
      clearLongPressTimer()
      void fire('long_press', {
        nx: current.nx,
        ny: current.ny,
        duration_ms: Math.max(LONG_PRESS_MS, Date.now() - current.startedAt),
      })
    }, LONG_PRESS_MS)
  }

  function onPointerMove(event: PointerEvent): void {
    const dragState = drag
    if (!dragState || dragState.pointerId !== event.pointerId) return
    const point = normalize(event.clientX, event.clientY, dragState.rect)
    if (!point) return
    const rect = dragState.rect
    const dx = rect ? (point.nx - dragState.nx) * rect.width : 0
    const dy = rect ? (point.ny - dragState.ny) * rect.height : 0
    if (Math.hypot(dx, dy) >= MOVE_THRESHOLD_PX) {
      dragState.moved = true
      if (dragState.streaming) clearLongPressTimer()
    }
    if (dragState.streaming) {
      let added = false
      const native = event as PointerEvent & { getCoalescedEvents?: () => PointerEvent[] }
      if (typeof native.getCoalescedEvents === 'function') {
        for (const sample of native.getCoalescedEvents()) {
          const p = normalize(sample.clientX, sample.clientY, dragState.rect)
          if (p) {
            moveQueue.push(p)
            added = true
          }
        }
      }
      if (!added) moveQueue.push(point)
      if (moveQueue.length > MAX_MOVE_QUEUE) moveQueue.splice(0, moveQueue.length - MAX_MOVE_QUEUE)
      void pump()
      return
    }
    if (dragState.moved) clearLongPressTimer()
  }

  function finishGesture(event: PointerEvent, cancelled: boolean): void {
    const dragState = drag
    if (!dragState || dragState.pointerId !== event.pointerId) return
    drag = null
    clearLongPressTimer()
    const point = normalize(event.clientX, event.clientY, dragState.rect) ?? {
      nx: dragState.nx,
      ny: dragState.ny,
    }
    try {
      ;(event.currentTarget as HTMLCanvasElement | null)?.releasePointerCapture(event.pointerId)
    } catch {
      /* ignore */
    }

    if (dragState.streaming) {
      if (options.wsTouch) {
        stopPump()
        while (moveQueue.length > 0) {
          const next = moveQueue.shift()
          if (!next) break
          if (!tryWsTouch('move', next)) {
            moveQueue.unshift(next)
            break
          }
        }
        tryWsTouch('up', point)
        return
      }
      void (async () => {
        stopPump()
        moveQueue.push(point)
        const deadline = Date.now() + UP_FLUSH_TIMEOUT_MS
        while (moveQueue.length > 0 && Date.now() < deadline) {
          await pump()
        }
        await waitIdle()
        inFlight = true
        await sendTouch('up', point)
        inFlight = false
      })()
      return
    }
    // 浏览器取消（系统手势/焦点丢失/上下文菜单）不是一次用户输入：
    // 流式模式上面已经补发了 up（防止设备端指针悬空），回落模式不要再补一个 tap/long_press。
    if (cancelled) return
    if (dragState.committed) return
    void fireBuffered(dragState, point)
  }

  function onPointerUp(event: PointerEvent): void {
    finishGesture(event, false)
  }

  function onPointerCancel(event: PointerEvent): void {
    finishGesture(event, true)
  }

  onBeforeUnmount(() => {
    clearLongPressTimer()
    stopPump()
  })

  return {
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel } satisfies PointerGestureHandlers,
    busy,
    busyValue: () => busyValue,
  }
}
