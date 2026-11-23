// 镜像触控特效（点击涟漪 + 滑动轨迹）。
//
// 目的：让操作者在镜像画面上直观看清“点在哪里、滑过哪条线”，便于排查坐标/手势问题。
// 约束：
//  - 只画在独立的覆盖 canvas 上（pointer-events: none），不参与、不拦截镜像手势；
//  - 坐标是覆盖层 CSS 像素，backing store 乘 DPR（上限 2），保证 Retina 清晰且绘制量可控；
//  - 无活动触控且动画播完时停止 rAF，静止状态不产生任何绘制开销；
//  - 这是纯本地视觉反馈，不会把任何数据发给设备/后端。
//
// 生命周期：down() 创建一条轨迹 + 涟漪；move() 追加轨迹点；up()/cancel() 结束轨迹并让它淡出；
// 涟漪/轨迹全部消失后自动停表。clear() 立即清空（断流、关闭开关、切换设备时调用）。

const MAX_TRAIL_POINTS = 300
const MIN_POINT_DISTANCE = 1.5
const MOVE_THRESHOLD = 6
const DPR_CAP = 2
const DEFAULT_TRAIL_FADE_MS = 650
const DEFAULT_RIPPLE_MS = 460

interface Point {
  x: number
  y: number
  t: number
}

interface Trace {
  points: Point[]
  startX: number
  startY: number
  downAt: number
  active: boolean
  releasedAt: number
  moved: boolean
}

interface Ripple {
  x: number
  y: number
  born: number
  kind: 'down' | 'up'
}

export interface TouchEffectRendererOptions {
  /** 松手后轨迹淡出时长（ms） */
  trailFadeMs?: number
  /** 单次涟漪时长（ms） */
  rippleMs?: number
}

function nowMs(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now()
}

function easeOutCubic(value: number): number {
  const inverse = 1 - value
  return 1 - inverse * inverse * inverse
}

export class TouchEffectRenderer {
  private readonly canvas: HTMLCanvasElement
  private readonly ctx: CanvasRenderingContext2D | null
  private readonly trailFadeMs: number
  private readonly rippleMs: number

  private cssWidth = 0
  private cssHeight = 0
  private dpr = 1
  private traces: Trace[] = []
  private ripples: Ripple[] = []
  private rafId = 0
  private enabled = true
  private tapEnabled = true
  private swipeEnabled = true
  private rippleEnabled = true
  private destroyed = false

  constructor(canvas: HTMLCanvasElement, options: TouchEffectRendererOptions = {}) {
    this.canvas = canvas
    this.ctx = canvas.getContext('2d', { alpha: true, desynchronized: true })
    this.trailFadeMs = options.trailFadeMs ?? DEFAULT_TRAIL_FADE_MS
    this.rippleMs = options.rippleMs ?? DEFAULT_RIPPLE_MS
  }

  /** 覆盖层 CSS 尺寸变化时调用：重设 backing store（会清空画面）。 */
  resize(cssWidth: number, cssHeight: number): void {
    if (this.destroyed) return
    const width = Math.max(1, Math.round(cssWidth))
    const height = Math.max(1, Math.round(cssHeight))
    const rawDpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1
    const dpr = Math.min(DPR_CAP, Math.max(1, rawDpr))
    const pixelWidth = Math.max(1, Math.round(width * dpr))
    const pixelHeight = Math.max(1, Math.round(height * dpr))
    this.cssWidth = width
    this.cssHeight = height
    this.dpr = dpr
    if (this.canvas.width !== pixelWidth || this.canvas.height !== pixelHeight) {
      this.canvas.width = pixelWidth
      this.canvas.height = pixelHeight
    }
    this.clear()
  }

  setEnabled(value: boolean): void {
    if (this.destroyed || this.enabled === value) return
    this.enabled = value
    if (!value) this.clear()
  }

  /** 点击特效总闸：触点圆点 + 波纹 + 长按呼吸环（不含滑动轨迹）。 */
  setTapEnabled(value: boolean): void {
    if (this.destroyed || this.tapEnabled === value) return
    this.tapEnabled = value
    if (!value) {
      this.ripples = []
      if (this.traces.length) this.schedule()
      else this.paintEmpty()
    }
  }

  /** 滑动特效总闸：拖动轨迹 + 跟随终点圆点（不含按下/长按反馈）。 */
  setSwipeEnabled(value: boolean): void {
    if (this.destroyed || this.swipeEnabled === value) return
    this.swipeEnabled = value
    if (this.traces.length) this.schedule()
  }

  /**
   * 单独开关波纹（按下/松手的扩散圆环）。
   * 关闭时立即清掉正在扩散的波纹，但保留触点和滑动轨迹；开启后只影响后续手势。
   */
  setRippleEnabled(value: boolean): void {
    if (this.destroyed || this.rippleEnabled === value) return
    this.rippleEnabled = value
    if (!value) {
      this.ripples = []
      if (!this.traces.length) this.paintEmpty()
      else this.schedule()
    }
  }

  down(x: number, y: number): void {
    if (!this.enabled || this.destroyed) return
    const now = nowMs()
    for (const trace of this.traces) {
      if (trace.active) {
        trace.active = false
        trace.releasedAt = now
      }
    }
    this.traces.push({
      points: [{ x, y, t: now }],
      startX: x,
      startY: y,
      downAt: now,
      active: true,
      releasedAt: 0,
      moved: false,
    })
    if (this.tapEnabled && this.rippleEnabled) this.ripples.push({ x, y, born: now, kind: 'down' })
    this.schedule()
  }

  move(x: number, y: number): void {
    const trace = this.activeTrace()
    if (!trace || this.destroyed) return
    const now = nowMs()
    const last = trace.points[trace.points.length - 1]
    if (Math.hypot(x - last.x, y - last.y) < MIN_POINT_DISTANCE && now - last.t < 16) return
    trace.points.push({ x, y, t: now })
    if (trace.points.length > MAX_TRAIL_POINTS) {
      trace.points.splice(0, trace.points.length - MAX_TRAIL_POINTS)
    }
    if (!trace.moved && Math.hypot(x - trace.startX, y - trace.startY) >= MOVE_THRESHOLD) {
      trace.moved = true
    }
    this.schedule()
  }

  up(x: number, y: number): void {
    const trace = this.activeTrace()
    if (!trace || this.destroyed) return
    const now = nowMs()
    const last = trace.points[trace.points.length - 1]
    if (last && Math.hypot(x - last.x, y - last.y) >= 0.5) {
      trace.points.push({ x, y, t: now })
    }
    trace.active = false
    trace.releasedAt = now
    if (this.tapEnabled && this.rippleEnabled) this.ripples.push({ x, y, born: now, kind: 'up' })
    this.schedule()
  }

  cancel(): void {
    const trace = this.activeTrace()
    if (!trace || this.destroyed) return
    trace.active = false
    trace.releasedAt = nowMs()
    this.schedule()
  }

  /** 立即清空画面并停止动画（关闭开关 / 断流 / 切换设备）。 */
  clear(): void {
    this.traces = []
    this.ripples = []
    if (this.rafId) {
      if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(this.rafId)
      this.rafId = 0
    }
    this.paintEmpty()
  }

  destroy(): void {
    this.destroyed = true
    this.clear()
  }

  private activeTrace(): Trace | null {
    for (let index = this.traces.length - 1; index >= 0; index -= 1) {
      if (this.traces[index].active) return this.traces[index]
    }
    return null
  }

  private paintEmpty(): void {
    const ctx = this.ctx
    if (!ctx) return
    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height)
  }

  private schedule(): void {
    if (this.destroyed || !this.enabled || this.rafId) return
    if (typeof requestAnimationFrame !== 'function') return
    this.rafId = requestAnimationFrame(this.draw)
  }

  private draw = (): void => {
    this.rafId = 0
    if (this.destroyed || !this.enabled) return
    const ctx = this.ctx
    if (!ctx) return
    const now = nowMs()

    this.paintEmpty()
    if (!this.cssWidth || !this.cssHeight) return
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0)

    this.ripples = this.ripples.filter((ripple) => now - ripple.born <= this.rippleMs)
    for (const ripple of this.ripples) {
      this.drawRipple(ctx, ripple, now)
    }

    let hasActive = false
    const alive: Trace[] = []
    for (const trace of this.traces) {
      const age = trace.active ? 0 : now - trace.releasedAt
      if (age > this.trailFadeMs) continue
      const alpha = trace.active ? 1 : Math.max(0, 1 - age / this.trailFadeMs)
      this.drawTrace(ctx, trace, alpha, now)
      if (trace.active) hasActive = true
      alive.push(trace)
    }
    this.traces = alive

    if (hasActive || this.traces.length > 0 || this.ripples.length > 0) this.schedule()
  }

  private drawRipple(ctx: CanvasRenderingContext2D, ripple: Ripple, now: number): void {
    const progress = Math.min(1, Math.max(0, (now - ripple.born) / this.rippleMs))
    if (progress >= 1) return
    const eased = easeOutCubic(progress)
    // 收敛后的小波纹：最大半径约 26px（原来 44px），和触点圆点同一个量级，不抢画面。
    const radius = 6 + 20 * eased
    const alpha = (1 - progress) * (ripple.kind === 'down' ? 0.85 : 0.6)

    ctx.save()
    ctx.globalAlpha = alpha
    ctx.lineWidth = 2.2 * (1 - progress) + 0.6
    ctx.strokeStyle = ripple.kind === 'down' ? 'rgba(96, 165, 250, 1)' : 'rgba(255, 255, 255, 0.9)'
    ctx.beginPath()
    ctx.arc(ripple.x, ripple.y, radius, 0, Math.PI * 2)
    ctx.stroke()

    if (ripple.kind === 'down') {
      ctx.globalAlpha = alpha * 0.18
      ctx.fillStyle = '#60a5fa'
      ctx.beginPath()
      ctx.arc(ripple.x, ripple.y, radius * 0.5, 0, Math.PI * 2)
      ctx.fill()
    }
    ctx.restore()
  }

  private drawTrace(ctx: CanvasRenderingContext2D, trace: Trace, alpha: number, now: number): void {
    const points = trace.points
    const isDrag = trace.moved && points.length > 1
    if (this.swipeEnabled && isDrag) {
      ctx.save()
      ctx.globalAlpha = alpha
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'

      const path = new Path2D()
      path.moveTo(points[0].x, points[0].y)
      for (let index = 1; index < points.length; index += 1) {
        path.lineTo(points[index].x, points[index].y)
      }
      // 深色描边保证在浅色画面上也可见；白色内芯保证在深色画面上也可见。
      ctx.strokeStyle = 'rgba(15, 23, 42, 0.35)'
      ctx.lineWidth = 11
      ctx.stroke(path)
      ctx.strokeStyle = 'rgba(96, 165, 250, 0.85)'
      ctx.lineWidth = 6.5
      ctx.stroke(path)
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.95)'
      ctx.lineWidth = 2.5
      ctx.stroke(path)
      ctx.restore()
    }

    // 拖动时长点跟随手指；只有点击特效时，拖动期间把长点冻结在按下位置，不显示轨迹。
    const head = isDrag
      ? this.swipeEnabled
        ? points[points.length - 1]
        : this.tapEnabled
          ? points[0]
          : null
      : this.tapEnabled
        ? points[points.length - 1]
        : null
    if (!head) return
    ctx.save()
    ctx.globalAlpha = alpha
    ctx.fillStyle = 'rgba(37, 99, 235, 0.3)'
    ctx.beginPath()
    ctx.arc(head.x, head.y, 10, 0, Math.PI * 2)
    ctx.fill()
    ctx.fillStyle = '#ffffff'
    ctx.beginPath()
    ctx.arc(head.x, head.y, 5.5, 0, Math.PI * 2)
    ctx.fill()
    ctx.lineWidth = 1.5
    ctx.strokeStyle = 'rgba(37, 99, 235, 0.95)'
    ctx.stroke()

    // 长按未移动：给一个缓慢呼吸的提示环，和拖动轨迹区分开。
    if (this.tapEnabled && trace.active && !trace.moved) {
      const held = now - trace.downAt
      if (held > 300) {
        const pulse = 0.5 + 0.5 * Math.sin((held - 300) / 180)
        ctx.globalAlpha = alpha * (0.35 + 0.3 * pulse)
        ctx.strokeStyle = 'rgba(96, 165, 250, 1)'
        ctx.lineWidth = 2
        ctx.beginPath()
        ctx.arc(head.x, head.y, 14 + 3 * pulse, 0, Math.PI * 2)
        ctx.stroke()
      }
    }
    ctx.restore()
  }
}
