// 镜像画布指针手势 → 手动控制。
//
// 两种模式（自动选择）：
//  1) 流式触摸（首选）：pointerdown 立即下发 down，pointermove 持续下发 move（合并最新位置，
//     保证同一时刻只有一个请求在飞），pointerup 收尾 up。设备端实时跟手。
//     Chrome 下拖动优先走 pointerrawupdate：它不像 pointermove 那样对齐到 rAF（60Hz 下平均晚
//     8ms、最多一帧），样本一到主线程就下发；随后的 pointermove 会把同样的样本再带一遍，
//     这里按时间戳去重。没有 pointerrawupdate 的浏览器自动回到 pointermove。
//  2) 一次性手势（回落）：无镜像会话/后端不支持时，在本地判定 tap / long_press / swipe。
//
// 手势必须有始有终：设备端只认 down/move/up，漏掉 up 就会一直保持按压/拖拽。
// 所以 pointerup 之外还有三道兜底：画布捕获失效时由调用方在 window 上补收尾
// （MirrorStage）、窗口失焦/页面隐藏时 abortGesture() 用最后位置补 UP、
// WS 的 up 发不出去时再静默走一次 REST。
//
// 坐标始终是相对视频帧的归一化 nx,ny ∈ [0,1]（后端换算，前端不做设备像素换算）。

import { computed, onBeforeUnmount, ref, toValue, watch, type MaybeRefOrGetter, type Ref } from 'vue'

import { errorMessage, post } from '../lib/api'

const TAP_MAX_MS = 500
const LONG_PRESS_MS = 500
const MOVE_THRESHOLD_PX = 10
const UP_FLUSH_TIMEOUT_MS = 600
const PUMP_INTERVAL_MS = 8
const MAX_MOVE_QUEUE = 6

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
  /**
   * `pointerrawupdate`（Chrome）：未对齐 rAF 的原始指针移动。不在 Vue 的 Events 类型里，
   * 需要用 addEventListener 手动绑定；不绑定时自动回落到 pointermove。
   */
  onPointerRawUpdate: (event: PointerEvent) => void
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
  /** 最近一次移动的位置：pointerup 丢失时用它补 UP（拖动期间不等于 nx/ny，后者是手势起点）。 */
  lastNx: number
  lastNy: number
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
  // 最近一条经 pointerrawupdate 实时下发的样本时间戳（-1 = 本手势还没有 raw 样本）；
  // pointermove 的 coalesced 样本里 ≤ 它的都是重复，跳过。
  let lastRawTs = -1

  watch(serial, () => {
    streamSupported = null
    moveQueue.length = 0
    inFlight = false
    lastRawTs = -1
    // 换设备：旧手势的 UP 由后端在镜像 WS 断开时按 owner 补（mirror.py 的 release_stream_touch），
    // 这里只丢掉本地状态，避免把 up 发到新设备上。
    drag = null
    clearLongPressTimer()
    stopPump()
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

  /** 超过阈值即视为移动（两种模式共用）：流式模式用来判定是否取消本地长按。 */
  function trackMovement(dragState: DragState, point: NormalizedPoint): void {
    const rect = dragState.rect
    const dx = rect ? (point.nx - dragState.nx) * rect.width : 0
    const dy = rect ? (point.ny - dragState.ny) * rect.height : 0
    if (Math.hypot(dx, dy) >= MOVE_THRESHOLD_PX) {
      dragState.moved = true
      clearLongPressTimer()
    }
  }

  function trimMoveQueue(): void {
    if (moveQueue.length > MAX_MOVE_QUEUE) moveQueue.splice(0, moveQueue.length - MAX_MOVE_QUEUE)
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
    // 上一次手势的 UP 丢了（在窗口外松手、浏览器吞掉事件…）：先补 UP 收尾再接受这次按下，
    // 否则新按下的 down 会被丢掉，设备端就一直停在上一次的按压上。
    if (drag) abortGesture()
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
      lastNx: point.nx,
      lastNy: point.ny,
      moved: false,
      committed: false,
      streaming,
      rect,
    }
    drag = dragState
    lastRawTs = -1

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

  /**
   * Chrome `pointerrawupdate`：样本一到主线程就下发，不等下一个 rAF。
   * 只在流式拖动期间生效；本地长按/回落模式仍以 pointermove 为准。
   */
  function onPointerRawUpdate(event: PointerEvent): void {
    const dragState = drag
    if (!dragState || dragState.pointerId !== event.pointerId || !dragState.streaming) return
    if (event.timeStamp <= lastRawTs) return
    const point = normalize(event.clientX, event.clientY, dragState.rect)
    if (!point) return
    lastRawTs = event.timeStamp
    dragState.lastNx = point.nx
    dragState.lastNy = point.ny
    trackMovement(dragState, point)
    moveQueue.push(point)
    trimMoveQueue()
    void pump()
  }

  function onPointerMove(event: PointerEvent): void {
    const dragState = drag
    if (!dragState || dragState.pointerId !== event.pointerId) return
    const point = normalize(event.clientX, event.clientY, dragState.rect)
    if (!point) return
    dragState.lastNx = point.nx
    dragState.lastNy = point.ny
    trackMovement(dragState, point)
    if (!dragState.streaming) return

    const native = event as PointerEvent & { getCoalescedEvents?: () => PointerEvent[] }
    const samples: PointerEvent[] =
      typeof native.getCoalescedEvents === 'function' ? [...native.getCoalescedEvents()] : []
    if (samples.length === 0) samples.push(event)
    // rawupdate 已经实时发过的样本不再重复下发（同样的物理移动会在 pointermove 里再出现一次）
    let added = false
    for (const sample of samples) {
      if (sample.timeStamp <= lastRawTs) continue
      const p = normalize(sample.clientX, sample.clientY, dragState.rect)
      if (p) {
        moveQueue.push(p)
        added = true
      }
    }
    if (!added) return
    trimMoveQueue()
    void pump()
  }

  function finishGesture(event: PointerEvent, cancelled: boolean): void {
    const dragState = drag
    if (!dragState || dragState.pointerId !== event.pointerId) return
    const point = normalize(event.clientX, event.clientY, dragState.rect) ?? {
      nx: dragState.lastNx,
      ny: dragState.lastNy,
    }
    endDrag(dragState, point, cancelled)
  }

  /**
   * 用最后位置主动收尾当前手势（补一条 UP）。
   * 拖动中窗口失焦 / 页面切到后台 / 换设备时浏览器可能永远不发 pointerup，
   * 不补 UP 设备端就会一直保持按压。
   */
  function abortGesture(): void {
    const dragState = drag
    if (!dragState) return
    endDrag(dragState, { nx: dragState.lastNx, ny: dragState.lastNy }, true)
  }

  /** 指针捕获可能已被浏览器丢掉（元素被替换、合成事件…），失败不影响收尾。 */
  function releaseCapture(pointerId: number): void {
    try {
      options.canvasRef.value?.releasePointerCapture(pointerId)
    } catch {
      /* ignore */
    }
  }

  /** WS 上发不出 UP 时的兜底：静默走一次 REST（后端 WS 断线也会按 owner 释放，失败不再弹提示）。 */
  function sendFallbackUp(point: NormalizedPoint): void {
    void (async () => {
      try {
        await post<void>(devicePath(serial.value, 'touch'), {
          action: 'up',
          nx: Number(point.nx.toFixed(5)),
          ny: Number(point.ny.toFixed(5)),
        })
      } catch {
        /* ignore */
      }
    })()
  }

  function endDrag(dragState: DragState, point: NormalizedPoint, cancelled: boolean): void {
    // 同一段手势只收尾一次（window 兜底与画布事件会重复进入）
    if (drag !== dragState) return
    drag = null
    clearLongPressTimer()
    releaseCapture(dragState.pointerId)

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
        if (!tryWsTouch('up', point)) sendFallbackUp(point)
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
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerRawUpdate,
      onPointerUp,
      onPointerCancel,
    } satisfies PointerGestureHandlers,
    busy,
    busyValue: () => busyValue,
    /** 是否有手势还没收尾；传 pointerId 时只匹配那段手势（供调用方兜底判断）。 */
    isDragging: (pointerId?: number) =>
      drag !== null && (pointerId === undefined || drag.pointerId === pointerId),
    /** 用最后位置补 UP 并本地收尾（窗口失焦/页面隐藏/换设备时调用）。 */
    abortGesture,
  }
}
