// 镜像“运行参数 vs 设置页当前值”的差异描述。
//
// 关键坑：后端 hello/config 里的 `max_size` 是**已经算过缩放的结果**（设备长边 × scale，
// 再受设置上限约束；例如 2670 × 70% = 1868），而设置页的 `scrcpy.max_size` 是**上限本身**
// （默认 4096）。直接比较这两个数永远不相等，于是每次开着镜像都会常驻一条
// “最大边 1868 → 4096” 的假提示。
//
// 正确做法：新后端会同时下发 `scale_pct`（本次缩放比例）和 `configured_max_size`（当时的上限），
// 和设置页的 `scrcpy.scale` / `scrcpy.max_size` 逐个对应比较；只有老后端（两个字段都没有）才退回
// 比较 `max_size`（那时它就是设置值）。
//
// 纯函数、无 DOM 依赖：`__selfTest()` 可在 node 里直接跑（见文件末尾）。

export interface RunningMirrorConfig {
  /** hello.max_size：已算过缩放的**实际长边**（不是设置页的上限） */
  maxSize?: number
  /** 本次启动用的缩放比例（新后端才有） */
  scalePct?: number
  /** 本次启动时设置页的上限（新后端才有） */
  configuredMaxSize?: number
  maxFps?: number
  videoBitRate?: number
}

export interface MirrorConfigMismatch {
  /** 例如 "缩放 70% → 85%，帧率 60 → 不限" */
  summary: string
  /** 当前镜像实际在跑的长边像素（0 = 未知） */
  runningLongEdge: number
}

function num(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

/** 帧率文案：0 = 不限（scrcpy 语义），不要显示成 “0 → 60”。 */
export function fpsLabel(fps: number): string {
  return fps === 0 ? '不限' : String(fps)
}

function bitrateLabel(bps: number): string {
  const mbps = Math.round((bps / 1_000_000) * 10) / 10
  return `${mbps}M`
}

/**
 * 返回 null 表示“运行参数与设置一致”（或者还拿不到数据，不该提示）。
 */
export function describeConfigMismatch(
  running: RunningMirrorConfig | null | undefined,
  effective: Record<string, unknown> | null | undefined,
): MirrorConfigMismatch | null {
  if (!running || !effective) return null

  const diffs: string[] = []
  const scale = num(effective['scrcpy.scale'])
  const cap = num(effective['scrcpy.max_size'])
  const fps = num(effective['scrcpy.max_fps'])
  const bitrate = num(effective['scrcpy.video_bit_rate'])

  if (running.scalePct !== undefined && scale !== null && running.scalePct !== scale) {
    diffs.push(`缩放 ${running.scalePct}% → ${scale}%`)
  }
  if (running.configuredMaxSize !== undefined && cap !== null && running.configuredMaxSize !== cap) {
    diffs.push(`长边上限 ${running.configuredMaxSize} → ${cap}`)
  }
  // 老后端没有 scale_pct / configured_max_size：那时 max_size 就是设置值，可以安全直接比较
  const hasScaleInfo = running.scalePct !== undefined || running.configuredMaxSize !== undefined
  if (!hasScaleInfo && running.maxSize !== undefined && cap !== null && running.maxSize !== cap) {
    diffs.push(`最大边 ${running.maxSize} → ${cap}`)
  }
  if (running.maxFps !== undefined && fps !== null && running.maxFps !== fps) {
    diffs.push(`帧率 ${fpsLabel(running.maxFps)} → ${fpsLabel(fps)}`)
  }
  if (running.videoBitRate !== undefined && bitrate !== null && running.videoBitRate !== bitrate) {
    diffs.push(`码率 ${bitrateLabel(running.videoBitRate)} → ${bitrateLabel(bitrate)}`)
  }

  if (diffs.length === 0) return null
  return {
    summary: diffs.join('，'),
    runningLongEdge: running.maxSize && running.maxSize > 0 ? running.maxSize : 0,
  }
}

/** 单元级自测：返回失败信息（空数组 = 全部通过）。可在 node 里 `__selfTest()`。 */
export function __selfTest(): string[] {
  const failures: string[] = []
  const check = (cond: boolean, msg: string): void => {
    if (!cond) failures.push(msg)
  }
  const settings = {
    'scrcpy.scale': 70,
    'scrcpy.max_size': 4096,
    'scrcpy.max_fps': 0,
    'scrcpy.video_bit_rate': 8_000_000,
  }

  // 1. 本次会话正是按当前设置启动的：max_size=1868（2670×70%）绝不能和上限 4096 比
  check(
    describeConfigMismatch(
      { maxSize: 1868, scalePct: 70, configuredMaxSize: 4096, maxFps: 0, videoBitRate: 8_000_000 },
      settings,
    ) === null,
    '运行参数与设置一致时不应提示（历史误报：最大边 1868 → 4096）',
  )

  // 2. 只改缩放
  const scaled = describeConfigMismatch(
    { maxSize: 1868, scalePct: 70, configuredMaxSize: 4096, maxFps: 0, videoBitRate: 8_000_000 },
    { ...settings, 'scrcpy.scale': 85 },
  )
  check(!!scaled && scaled.summary === '缩放 70% → 85%', `只改缩放应只报缩放，实际 ${scaled?.summary}`)
  check(!!scaled && scaled.runningLongEdge === 1868, '应带出当前实际长边')

  // 3. 只改上限（不动缩放）
  const capped = describeConfigMismatch(
    { maxSize: 1868, scalePct: 70, configuredMaxSize: 4096, maxFps: 0, videoBitRate: 8_000_000 },
    { ...settings, 'scrcpy.max_size': 2048 },
  )
  check(!!capped && capped.summary === '长边上限 4096 → 2048', `只改上限应只报上限，实际 ${capped?.summary}`)

  // 4. 老后端（没有 scale_pct / configured_max_size）：max_size 就是设置值
  const legacy = describeConfigMismatch({ maxSize: 1600, maxFps: 30, videoBitRate: 8_000_000 }, settings)
  check(
    !!legacy && legacy.summary.includes('最大边 1600 → 4096'),
    `老后端应退回比较 max_size，实际 ${legacy?.summary}`,
  )

  // 5. 帧率 0 的文案 + 多字段拼接顺序
  const fps = describeConfigMismatch(
    { maxSize: 1868, scalePct: 70, configuredMaxSize: 4096, maxFps: 60, videoBitRate: 8_000_000 },
    settings,
  )
  check(!!fps && fps.summary === '帧率 60 → 不限', `帧率文案错误：${fps?.summary}`)

  // 6. 码率换算
  const rate = describeConfigMismatch(
    { maxSize: 1868, scalePct: 70, configuredMaxSize: 4096, maxFps: 0, videoBitRate: 10_000_000 },
    settings,
  )
  check(!!rate && rate.summary === '码率 10M → 8M', `码率文案错误：${rate?.summary}`)

  // 7. 数据缺失不提示
  check(describeConfigMismatch(null, settings) === null, '没有 hello 时不提示')
  check(describeConfigMismatch({ maxSize: 1868 }, null) === null, '没有设置时不提示')
  check(describeConfigMismatch({}, settings) === null, '字段全缺时不提示')

  check(fpsLabel(0) === '不限' && fpsLabel(60) === '60', 'fpsLabel 文案错误')
  return failures
}
