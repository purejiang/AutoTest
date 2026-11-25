// 镜像画布渲染器：只画“最新一帧”，天然低延迟（旧帧直接 close 丢弃）。
//
// 尺寸策略（等价 CSS object-fit: contain）：
//   - 容器给出可用空间；按视频帧宽高比等比缩放，canvas 的 CSS 尺寸就是缩放后的内容尺寸；
//   - canvas 的 backing store 乘 devicePixelRatio，保证 Retina 清晰；
//   - 因为 canvas 元素本身已经是 contain 后的内容盒，指针坐标映射不需要再做 letterbox 换算。
//
// 关于 requestVideoFrameCallback：
//   它是 HTMLVideoElement 的 API，WebCodecs 输出的是 VideoFrame，没有对应的 frame callback；
//   本渲染器统一用 requestAnimationFrame 合帧（同一帧内只画最新一帧）。若将来改成
//   MediaStream + <video> 的渲染路径，可在 present() 里改走 rVFC（下方 hasVideoFrameCallback 留了探测）。

export interface CanvasFrameInfo {
  /** 视频帧可视尺寸 */
  videoWidth: number
  videoHeight: number
  /** 已累计呈现帧数 */
  frames: number
}

export interface CanvasRendererOptions {
  onFrameDrawn?: (info: CanvasFrameInfo) => void
}

export class CanvasRenderer {
  private readonly options: CanvasRendererOptions
  private canvas: HTMLCanvasElement | null = null
  private container: HTMLElement | null = null
  private ctx: CanvasRenderingContext2D | null = null
  private resizeObserver: ResizeObserver | null = null
  private pendingFrame: VideoFrame | null = null
  private rafId = 0
  private disposed = false
  private drawnFrames = 0
  // drawImage 的单帧耗时（EMA）：解码排队但这里很便宜 → 瓶颈在解码/GPU 上传，不是绘制
  private drawMsEma = 0
  private lastVideoSize = { width: 0, height: 0 }
  private lastCanvasPixels = { width: 0, height: 0 }
  private lastDpr = 1
  // 容器尺寸由 ResizeObserver 量一次后缓存：draw 里再读 clientWidth/Height 会每帧触发强制重排。
  private containerSize = { width: 0, height: 0 }

  /** rVFC 能力探测（当前渲染路径不使用，保留给未来 <video> 路径） */
  static readonly hasVideoFrameCallback: boolean =
    typeof HTMLVideoElement !== 'undefined' && 'requestVideoFrameCallback' in HTMLVideoElement.prototype

  constructor(options: CanvasRendererOptions = {}) {
    this.options = options
  }

  get frameCount(): number {
    return this.drawnFrames
  }

  get videoSize(): { width: number; height: number } {
    return this.lastVideoSize
  }

  /** 最近单帧 drawImage 平均耗时（ms，EMA） */
  get drawMs(): number {
    return Math.round(this.drawMsEma * 100) / 100
  }

  attach(canvas: HTMLCanvasElement, container?: HTMLElement | null): void {
    this.detach()
    this.disposed = false
    this.canvas = canvas
    this.container = container ?? canvas.parentElement
    this.ctx = canvas.getContext('2d', { alpha: false, desynchronized: true })
    if (this.ctx) {
      this.ctx.imageSmoothingEnabled = true
      this.ctx.imageSmoothingQuality = 'low'
    }
    canvas.style.display = 'block'
    canvas.style.touchAction = 'none'
    this.measureContainer()
    if (typeof ResizeObserver !== 'undefined' && this.container) {
      this.resizeObserver = new ResizeObserver(() => {
        if (this.disposed) return
        this.measureContainer()
        // 容器变化后：有等待中的帧就重排一次；否则等下一帧到达时自然重排
        if (this.pendingFrame) this.schedule()
      })
      this.resizeObserver.observe(this.container)
    }
  }

  detach(): void {
    this.resizeObserver?.disconnect()
    this.resizeObserver = null
    if (this.rafId) {
      if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(this.rafId)
      this.rafId = 0
    }
    this.closePending()
    this.ctx = null
    this.canvas = null
    this.container = null
    this.containerSize = { width: 0, height: 0 }
  }

  destroy(): void {
    this.disposed = true
    this.detach()
  }

  /** 丢弃等待中的帧（重连/停止镜像时清掉陈旧画面，避免下一帧到来前闪旧图）。 */
  clear(): void {
    this.closePending()
  }

  /** 非阻塞提交一帧；同一时刻只保留最新一帧，旧的立即 close。 */
  present(frame: VideoFrame): void {
    if (this.disposed) {
      safeClose(frame)
      return
    }
    this.closePending()
    this.pendingFrame = frame
    this.schedule()
  }

  private closePending(): void {
    const frame = this.pendingFrame
    this.pendingFrame = null
    if (frame) safeClose(frame)
  }

  private schedule(): void {
    if (this.disposed || this.rafId) return
    this.rafId = requestAnimationFrame(this.draw)
  }

  private draw = (): void => {
    this.rafId = 0
    const frame = this.pendingFrame
    this.pendingFrame = null
    if (!frame) return
    try {
      if (this.disposed || !this.canvas || !this.ctx) return
      const videoWidth = frame.displayWidth || frame.codedWidth
      const videoHeight = frame.displayHeight || frame.codedHeight
      if (!videoWidth || !videoHeight) return
      this.lastVideoSize = { width: videoWidth, height: videoHeight }
      this.layout(videoWidth, videoHeight)
      const { width, height } = this.canvas
      if (!width || !height) return
      const drawStarted = performance.now()
      this.ctx.drawImage(frame, 0, 0, width, height)
      const drawCost = performance.now() - drawStarted
      this.drawMsEma = this.drawMsEma === 0 ? drawCost : this.drawMsEma * 0.8 + drawCost * 0.2
      this.drawnFrames += 1
      try {
        this.options.onFrameDrawn?.({
          videoWidth,
          videoHeight,
          frames: this.drawnFrames,
        })
      } catch {
        /* 渲染统计回调异常不影响解码 */
      }
    } finally {
      safeClose(frame)
    }
  }

  private measureContainer(): void {
    const container = this.container
    if (!container) return
    this.containerSize = { width: container.clientWidth, height: container.clientHeight }
  }

  /**
   * 按容器可用空间与视频宽高比、DPR 重排 canvas 尺寸（CSS contain 效果）。
   *
   * CSS 尺寸只取决于容器和画面宽高比，与推流分辨率无关：70% 缩放只是把更小的
   * 视频帧放大到同一显示尺寸，观感是变糊而不是变小。
   */
  private layout(videoWidth: number, videoHeight: number): void {
    const canvas = this.canvas
    const container = this.container
    if (!canvas || !container) return
    let availableWidth = this.containerSize.width
    let availableHeight = this.containerSize.height
    if (!availableWidth || !availableHeight) {
      // 容器刚显示/尚未被 observer 量到：补量一次，之后都走缓存。
      this.measureContainer()
      availableWidth = this.containerSize.width
      availableHeight = this.containerSize.height
    }
    if (!availableWidth || !availableHeight) return
    // DPR 上限 1.5：Retina(2x) 下画布像素是 CSS 的 4 倍，1436×3200 的 drawImage 在集显机器上
    // 会明显吃掉帧时间；1.5 在清晰度与流畅度之间取平衡。
    const rawDpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1
    const dpr = Math.min(1.5, Math.max(1, rawDpr))
    const scale = Math.min(availableWidth / videoWidth, availableHeight / videoHeight)
    const cssWidth = Math.max(1, Math.round(videoWidth * scale))
    const cssHeight = Math.max(1, Math.round(videoHeight * scale))
    const pixelWidth = Math.max(1, Math.round(cssWidth * dpr))
    const pixelHeight = Math.max(1, Math.round(cssHeight * dpr))

    const cssChanged = canvas.style.width !== `${cssWidth}px` || canvas.style.height !== `${cssHeight}px`
    if (cssChanged) {
      canvas.style.width = `${cssWidth}px`
      canvas.style.height = `${cssHeight}px`
    }
    // 修改 canvas.width/height 会清空画布，仅在真正变化时改
    if (
      canvas.width !== pixelWidth ||
      canvas.height !== pixelHeight ||
      this.lastCanvasPixels.width !== pixelWidth ||
      this.lastCanvasPixels.height !== pixelHeight ||
      this.lastDpr !== dpr
    ) {
      canvas.width = pixelWidth
      canvas.height = pixelHeight
      this.lastCanvasPixels = { width: pixelWidth, height: pixelHeight }
      this.lastDpr = dpr
      if (this.ctx) {
        this.ctx.imageSmoothingEnabled = true
        this.ctx.imageSmoothingQuality = 'low'
      }
    }
  }
}

function safeClose(frame: VideoFrame): void {
  try {
    frame.close()
  } catch {
    /* 已关闭的 frame 直接忽略 */
  }
}
