// 镜像诊断日志：定位“不跟手/卡顿”发生在哪一环。
//
// 每 5 秒打一行（浏览器控制台 + 内存环形缓冲）。这行数据把四个数放在一起：
//   设备出帧  —— 后端从设备读到多少帧（= 编码器实际出帧，输入量）
//   渲染      —— 画布实际画了多少帧（输出量）
//   绘制 Xms  —— 单帧 drawImage 耗时（判断瓶颈是否在“画”）
//   队列 N    —— WebCodecs decodeQueueSize（>2 说明解码排队、画面滞后）
// 判读：队列涨且绘制便宜 → 卡解码（降分辨率/限帧到屏幕刷新率）；绘制耗时长 → 卡绘制/GPU 上传。
//
// 控制台里执行 `__autotestMirrorDiag()` 可一次性拿到最近 120 行，方便直接贴出来对比。
// 纯函数 + 无 DOM 依赖，`__selfTest()` 可在 node 里跑。

export interface MirrorDiagSample {
  serial: string
  width?: number
  height?: number
  /** 后端每秒从设备读到的帧数 */
  serverFps?: number
  /** 画布实际渲染帧率 */
  renderFps: number
  /** 单帧 drawImage 平均耗时（ms） */
  drawMs: number
  /** WebCodecs decodeQueueSize */
  queueSize: number
  /** 服务端丢帧 + 解码队列丢帧累计 */
  dropped: number
  /** 设置页的帧率上限，0 = 不限 */
  maxFps?: number
  /** 本机显示器刷新率（Hz）：60Hz 屏幕上渲染到 60 就已经到顶，不能算瓶颈 */
  displayHz?: number | null
}

/** 一行诊断文本（字段顺序固定，便于 grep / 贴到 issue）。 */
export function formatMirrorDiag(sample: MirrorDiagSample): string {
  const size = sample.width && sample.height ? `${sample.width}x${sample.height}` : '-'
  const serverFps = sample.serverFps === undefined ? '-' : Math.round(sample.serverFps)
  const maxFps = sample.maxFps === undefined ? '-' : sample.maxFps === 0 ? '不限' : String(sample.maxFps)
  const displayHz = sample.displayHz ? `${sample.displayHz}Hz` : '-'
  const drawMs = Number.isFinite(sample.drawMs) ? sample.drawMs.toFixed(1) : '-'
  return (
    `[mirror-diag] serial=${sample.serial} stream=${size} ` +
    `设备出帧=${serverFps} 渲染=${Math.round(sample.renderFps)} ` +
    `绘制=${drawMs}ms 队列=${sample.queueSize} 丢帧=${sample.dropped} 上限=${maxFps} 屏幕=${displayHz}`
  )
}

/** 用 rAF 估算本机屏幕刷新率（60/120Hz）。隐藏页/无 rAF 时返回 null。 */
export function measureDisplayHz(samples = 30, timeoutMs = 1500): Promise<number | null> {
  if (typeof requestAnimationFrame !== 'function') return Promise.resolve(null)
  return new Promise((resolve) => {
    const times: number[] = []
    let done = false
    const finish = (value: number | null): void => {
      if (done) return
      done = true
      resolve(value)
    }
    const timer = setTimeout(() => finish(null), timeoutMs)
    const tick = (now: number): void => {
      times.push(now)
      if (times.length < samples + 1) {
        if (!done) requestAnimationFrame(tick)
        return
      }
      clearTimeout(timer)
      const deltas = times.slice(1).map((value, index) => value - times[index])
      const avg = deltas.reduce((sum, value) => sum + value, 0) / deltas.length
      finish(avg > 0 ? Math.round(1000 / avg) : null)
    }
    requestAnimationFrame(tick)
  })
}

const BUFFER_LIMIT = 120
const buffer: string[] = []

/** 记录一行到环形缓冲（并保证控制台可直接复制）。 */
export function recordMirrorDiag(line: string): void {
  buffer.push(line)
  if (buffer.length > BUFFER_LIMIT) buffer.splice(0, buffer.length - BUFFER_LIMIT)
  installAccessor()
}

/** 已记录的诊断行（测试/导出用）。 */
export function mirrorDiagLines(): string[] {
  return [...buffer]
}

export function __selfTest(): string[] {
  const failures: string[] = []
  const check = (cond: boolean, msg: string): void => {
    if (!cond) failures.push(msg)
  }

  const line = formatMirrorDiag({
    serial: 'demo-serial',
    width: 840,
    height: 1869,
    serverFps: 120,
    renderFps: 58.4,
    drawMs: 2.34,
    queueSize: 1,
    dropped: 7,
    maxFps: 0,
  })
  check(line.startsWith('[mirror-diag] serial=demo-serial stream=840x1869'), `前缀错误: ${line}`)
  check(line.includes('设备出帧=120'), `设备出帧应为 120: ${line}`)
  check(line.includes('渲染=58'), `渲染应四舍五入为 58: ${line}`)
  check(line.includes('绘制=2.3ms'), `绘制应保留一位小数: ${line}`)
  check(line.includes('队列=1'), `队列字段错误: ${line}`)
  check(line.includes('丢帧=7'), `丢帧字段错误: ${line}`)
  check(line.includes('上限=不限'), `0 应显示为“不限”: ${line}`)
  check(line.includes('屏幕=-'), `未测到刷新率时显示 -: ${line}`)

  const capped = formatMirrorDiag({
    serial: 'x',
    renderFps: 30,
    drawMs: 0,
    queueSize: 0,
    dropped: 0,
    maxFps: 60,
  })
  check(capped.includes('stream=-'), `缺分辨率时应显示 -: ${capped}`)
  check(capped.includes('设备出帧=-'), `缺 serverFps 时应显示 -: ${capped}`)
  check(capped.includes('上限=60'), `上限 60 原样显示: ${capped}`)
  check(capped.includes('绘制=0.0ms'), `绘制 0 也要有单位: ${capped}`)

  buffer.length = 0
  for (let i = 0; i < BUFFER_LIMIT + 10; i++) recordMirrorDiag(`line-${i}`)
  const kept = mirrorDiagLines()
  check(kept.length === BUFFER_LIMIT, `环形缓冲应只保留 ${BUFFER_LIMIT} 行，实际 ${kept.length}`)
  check(kept[kept.length - 1] === `line-${BUFFER_LIMIT + 9}`, '环形缓冲应保留最新的行')
  buffer.length = 0

  return failures
}

function installAccessor(): void {
  if (typeof window === 'undefined') return
  const scope = window as unknown as { __autotestMirrorDiag?: () => string }
  if (typeof scope.__autotestMirrorDiag === 'function') return
  scope.__autotestMirrorDiag = () => buffer.join('\n')
}
