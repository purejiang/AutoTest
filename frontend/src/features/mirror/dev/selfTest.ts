// 开发期自检入口（不要求页面暴露）。
//
// 无真机时用于证明：
//   - h264.ts 的 Annex B/ SPS/avcC 解析与构造（见 __selfTest 内的合成 SPS 断言）；
//   - mirrorWs.ts 的 12 字节帧头解析 + “只收到 payload”兜底。
//
// 触发方式（dev server）：
//   1) 浏览器打开任意镜像页并在 URL 加 ?mirror_selftest=1，控制台打印结果；
//   2) 或在控制台执行：
//        const m = await import('/src/features/mirror/dev/selfTest.ts')
//        m.runMirrorSelfTest()
// 生产构建不执行（import.meta.env.DEV 判断），也不暴露任何 UI。

import { __selfTest as h264SelfTest } from '../lib/h264'
import { parseBinaryFrame } from '../lib/mirrorWs'

export interface SelfTestReport {
  passed: boolean
  failures: string[]
}

function bytesEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false
  return true
}

/** 运行镜像模块的自检断言，返回失败信息（空数组 = 通过）。 */
export function runMirrorSelfTest(): SelfTestReport {
  const failures: string[] = []
  const check = (cond: boolean, msg: string): void => {
    if (!cond) failures.push(msg)
  }

  // 1) H.264 Annex B / SPS / codecString / avcC
  try {
    failures.push(...h264SelfTest().map((f) => `[h264] ${f}`))
  } catch (err) {
    failures.push(`[h264] 自测抛错：${(err as Error).message}`)
  }

  // 2) WS 12 字节帧头：[u64 BE pts_us][u32 BE len][payload]
  const payload = Uint8Array.from([0x00, 0x00, 0x00, 0x01, 0x65, 0x88, 0x84, 0x21])
  const framed = new ArrayBuffer(12 + payload.length)
  const framedView = new DataView(framed)
  framedView.setBigUint64(0, 123456789n, false)
  framedView.setUint32(8, payload.length, false)
  new Uint8Array(framed, 12).set(payload)
  const parsed = parseBinaryFrame(framed)
  check(parsed.ptsUs === 123456789, `[ws] 帧头 pts 解析错误：${parsed.ptsUs}`)
  check(bytesEqual(parsed.payload, payload), '[ws] 帧头 payload 解析错误')

  // 3) 只收到 payload（头被吃掉）时的兜底
  const payloadOnly = payload.slice().buffer
  const fallback = parseBinaryFrame(payloadOnly, 42)
  check(fallback.ptsUs === 42, `[ws] payload-only 兜底 pts 错误：${fallback.ptsUs}`)
  check(bytesEqual(fallback.payload, payload), '[ws] payload-only 兜底内容错误')

  // 4) 头在但长度字段不匹配 -> 整条兜底
  const mismatched = new ArrayBuffer(16)
  const mismatchedView = new DataView(mismatched)
  mismatchedView.setBigUint64(0, 1n, false)
  mismatchedView.setUint32(8, 999, false) // 与 16-12=4 不匹配
  const fallback2 = parseBinaryFrame(mismatched, 7)
  check(fallback2.ptsUs === 7, '[ws] 长度不匹配时应整条兜底')
  check(fallback2.payload.byteLength === 16, '[ws] 长度不匹配时 payload 应为整条消息')

  return { passed: failures.length === 0, failures }
}

/** 仅开发环境 + URL 带 ?mirror_selftest=1 时执行，并打印结果。 */
export function maybeRunSelfTestInDev(): void {
  if (!import.meta.env.DEV) return
  if (typeof window === 'undefined') return
  const params = new URLSearchParams(window.location.search)
  if (params.get('mirror_selftest') !== '1') return
  const report = runMirrorSelfTest()
  if (report.passed) {
    console.info('[mirror self-test] 全部通过（h264 + WS 帧解析）')
  } else {
    console.error('[mirror self-test] 失败：', report.failures)
  }
  // 方便在控制台里复跑
  ;(window as unknown as { __mirrorSelfTest?: () => SelfTestReport }).__mirrorSelfTest =
    runMirrorSelfTest
}
