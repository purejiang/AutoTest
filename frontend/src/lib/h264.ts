// H.264 Annex B 工具（纯函数，无 DOM 依赖，浏览器 / Worker / Node 均可运行）。
//
// 职责：
//  - splitNals:          扫描 3/4 字节起始码，切出 NAL 单元（去掉起始码与尾部 trailing_zero_8bits）
//  - nalType:            NAL unit type（nal_unit_header 低 5 bit）
//  - isKeyframeFrame:    帧内是否含 IDR（type 5）；无 SPS/PPS 的纯 IDR 也算
//  - parseSps:           完整解析 SPS 的 profile / level / 可视宽高（含 frame_cropping）
//  - codecString:        由 SPS 得到 "avc1.PPCCLL"（大写十六进制）
//  - buildAvcC:          由 SPS+PPS 构造 AVCDecoderConfigurationRecord
//  - nalsToAvcc:         Annex B NAL 列表 -> AVCC（4 字节大端长度前缀）数据
//
// 约定：
//  - 传入的 bytes 可以是带起始码的 Annex B 数据，也可以是裸 NAL；解析函数都做了容错。
//  - SPS 解析严格按 ITU-T H.264 7.3.2.1.1（含 high profile 的 chroma_format_idc/scaling list）。
//  - 无真机时的正确性证明见文件末尾 __selfTest()：用合成 SPS 做端到端断言。

/** 解析后的 SPS 关键字段。width/height 为裁剪后的可视尺寸（像素）。 */
export interface SpsInfo {
  profileIdc: number
  /** 兼容性字节（constraint_set0..5 + reserved），即 SPS 第 3 字节 */
  constraintFlags: number
  levelIdc: number
  width: number
  height: number
  /** 色度采样格式：0=mono,1=4:2:0,2=4:2:2,3=4:4:4 */
  chromaFormatIdc?: number
  frameMbsOnlyFlag?: number
}

const HIGH_PROFILE_IDCS = new Set([
  100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135,
])

/** 3 字节 / 4 字节起始码统一定位：00 00 01（4 字节起始码会被识别为该 pattern 偏移 +1 处）。 */
function isStartCode3(bytes: Uint8Array, i: number): boolean {
  return i + 2 < bytes.length && bytes[i] === 0 && bytes[i + 1] === 0 && bytes[i + 2] === 1
}

/**
 * 扫描 Annex B 字节流，返回 NAL 单元（不含起始码，去掉末尾 0x00 填充）。
 * 容错：
 *  - 3/4 字节起始码均可；
 *  - 首个 NAL 前允许有任意前导零/junk；
 *  - 完全没有起始码时，把整条数据当成一个 NAL 返回（镜像 WS 只推送 payload 时的兜底）；
 *  - 空 NAL（全是零/空 slice）被过滤。
 */
export function splitNals(bytes: Uint8Array): Uint8Array[] {
  const nals: Uint8Array[] = []
  const len = bytes.length
  let payloadStart = -1
  let i = 0
  while (i + 2 < len) {
    if (isStartCode3(bytes, i)) {
      if (payloadStart >= 0) pushTrimmed(bytes, payloadStart, i, nals)
      i += 3
      payloadStart = i
    } else {
      i++
    }
  }
  if (payloadStart >= 0) {
    pushTrimmed(bytes, payloadStart, len, nals)
  } else if (len > 0) {
    // 没有起始码：整条当 NAL（调用方可能拿到的是纯 payload 或裸 NAL）
    pushTrimmed(bytes, 0, len, nals)
  }
  return nals
}

function pushTrimmed(src: Uint8Array, start: number, end: number, out: Uint8Array[]): void {
  // rbsp_trailing_bits 保证最后一个字节非 0，因此去掉尾部 0 是安全的（Annex B trailing_zero_8bits）。
  while (end > start && src[end - 1] === 0) end--
  if (end > start) out.push(src.subarray(start, end))
}

/** 去掉可能存在的起始码；返回裸 NAL（含 1 字节 NAL header）。 */
export function stripStartCode(nal: Uint8Array): Uint8Array {
  if (isStartCode3(nal, 0)) return nal.subarray(3)
  if (nal.length >= 4 && nal[0] === 0 && nal[1] === 0 && nal[2] === 0 && nal[3] === 1) {
    return nal.subarray(4)
  }
  return nal
}

/** nal_unit_type（1..23），空数据返回 0。 */
export function nalType(nal: Uint8Array): number {
  return nal.length === 0 ? 0 : nal[0] & 0x1f
}

export function isSpsNal(nal: Uint8Array): boolean {
  return nalType(nal) === 7
}

export function isPpsNal(nal: Uint8Array): boolean {
  return nalType(nal) === 8
}

export function isIdrNal(nal: Uint8Array): boolean {
  return nalType(nal) === 5
}

/** 帧是否含 IDR（type 5）。注：scrcpy 每帧 Annex B payload 通常为 [SPS,PPS,IDR] 或 [IDR]。 */
export function isKeyframeFrame(nals: Uint8Array[]): boolean {
  return nals.some(isIdrNal)
}

export function bytesEqual(a: Uint8Array | null | undefined, b: Uint8Array | null | undefined): boolean {
  if (a === b) return true
  if (!a || !b || a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false
  return true
}

export function concatBytes(parts: Uint8Array[]): Uint8Array {
  let total = 0
  for (const p of parts) total += p.length
  const out = new Uint8Array(total)
  let off = 0
  for (const p of parts) {
    out.set(p, off)
    off += p.length
  }
  return out
}

/**
 * Annex B NAL 列表 -> AVCC 数据（每个 NAL 前加 4 字节大端长度）。
 * 仅当 VideoDecoder 以 `description = buildAvcC(...)` 配置时需要。
 * in-band 的 SPS/PPS 默认丢弃（description 已带），避免部分浏览器对重复参数集报错。
 */
export function nalsToAvcc(
  nals: Uint8Array[],
  opts: { dropParameterSets?: boolean } = {},
): Uint8Array {
  const drop = opts.dropParameterSets !== false
  const used = drop ? nals.filter((n) => !isSpsNal(n) && !isPpsNal(n)) : nals
  let total = 0
  for (const n of used) total += 4 + n.length
  const out = new Uint8Array(total)
  const view = new DataView(out.buffer)
  let off = 0
  for (const n of used) {
    view.setUint32(off, n.length, false)
    off += 4
    out.set(n, off)
    off += n.length
  }
  return out
}

// ---------------------------------------------------------------------------
// RBSP 去防竞争字节 + 位读取
// ---------------------------------------------------------------------------

/** 去掉 emulation_prevention_three_byte（00 00 03 -> 00 00）。 */
export function unescapeRbsp(data: Uint8Array): Uint8Array {
  const out = new Uint8Array(data.length)
  let oi = 0
  let zeros = 0
  for (let i = 0; i < data.length; i++) {
    const b = data[i]
    if (zeros >= 2 && b === 3) {
      // 跳过防竞争字节 0x03
      zeros = 0
      continue
    }
    out[oi++] = b
    zeros = b === 0 ? zeros + 1 : 0
  }
  return out.subarray(0, oi)
}

class BitReader {
  private readonly data: Uint8Array
  private bitPos = 0
  constructor(data: Uint8Array) {
    this.data = data
  }
  get bitsLeft(): number {
    return this.data.length * 8 - this.bitPos
  }
  u1(): number {
    if (this.bitPos >= this.data.length * 8) throw new Error('h264: SPS 位读取越界')
    const byte = this.data[this.bitPos >> 3]
    const bit = (byte >> (7 - (this.bitPos & 7))) & 1
    this.bitPos++
    return bit
  }
  u(n: number): number {
    let v = 0
    for (let i = 0; i < n; i++) v = v * 2 + this.u1()
    return v
  }
  /** unsigned Exp-Golomb */
  ue(): number {
    let leadingZeros = 0
    while (this.u1() === 0) {
      leadingZeros++
      if (leadingZeros > 31) throw new Error('h264: Exp-Golomb 编码非法')
    }
    if (leadingZeros === 0) return 0
    return 2 ** leadingZeros - 1 + this.u(leadingZeros)
  }
  /** signed Exp-Golomb */
  se(): number {
    const k = this.ue()
    return k % 2 === 1 ? (k + 1) / 2 : -(k / 2)
  }
}

function skipScalingList(reader: BitReader, size: number): void {
  let lastScale = 8
  let nextScale = 8
  for (let j = 0; j < size; j++) {
    if (nextScale !== 0) {
      const deltaScale = reader.se()
      nextScale = (lastScale + deltaScale + 256) % 256
    }
    lastScale = nextScale === 0 ? lastScale : nextScale
  }
}

/**
 * 解析 SPS（输入可带起始码）。返回 profile/level/裁剪后的 width/height。
 * 完整走完 pic_width_in_mbs_minus1 / pic_height_in_map_units_minus1 / frame_cropping 的语法。
 */
export function parseSps(sps: Uint8Array): SpsInfo {
  const nal = stripStartCode(sps)
  if (nal.length < 4) throw new Error('h264: SPS 数据过短')
  const type = nalType(nal)
  if (type !== 7) throw new Error(`h264: 不是 SPS（nal type=${type}）`)

  // 跳过 1 字节 nal_unit_header，并对 RBSP 做防竞争字节恢复
  const rbsp = unescapeRbsp(nal.subarray(1))
  const reader = new BitReader(rbsp)

  const profileIdc = reader.u(8)
  const constraintFlags = reader.u(8)
  const levelIdc = reader.u(8)
  reader.ue() // seq_parameter_set_id

  let chromaFormatIdc = 1
  let separateColourPlaneFlag = 0
  if (HIGH_PROFILE_IDCS.has(profileIdc)) {
    chromaFormatIdc = reader.ue()
    if (chromaFormatIdc === 3) separateColourPlaneFlag = reader.u1()
    reader.ue() // bit_depth_luma_minus8
    reader.ue() // bit_depth_chroma_minus8
    reader.u1() // qpprime_y_zero_transform_bypass_flag
    const seqScalingMatrixPresent = reader.u1()
    if (seqScalingMatrixPresent) {
      const count = chromaFormatIdc !== 3 ? 8 : 12
      for (let i = 0; i < count; i++) {
        const present = reader.u1()
        if (present) skipScalingList(reader, i < 6 ? 16 : 64)
      }
    }
  }

  reader.ue() // log2_max_frame_num_minus4
  const picOrderCntType = reader.ue()
  if (picOrderCntType === 0) {
    reader.ue() // log2_max_pic_order_cnt_lsb_minus4
  } else if (picOrderCntType === 1) {
    reader.u1() // delta_pic_order_always_zero_flag
    reader.se() // offset_for_non_ref_pic
    reader.se() // offset_for_top_to_bottom_field
    const cycle = reader.ue()
    if (cycle > 256) throw new Error('h264: SPS num_ref_frames_in_pic_order_cnt_cycle 非法')
    for (let i = 0; i < cycle; i++) reader.se()
  } else if (picOrderCntType !== 2) {
    throw new Error(`h264: SPS pic_order_cnt_type=${picOrderCntType} 非法`)
  }

  reader.ue() // max_num_ref_frames
  reader.u1() // gaps_in_frame_num_value_allowed_flag
  const picWidthInMbsMinus1 = reader.ue()
  const picHeightInMapUnitsMinus1 = reader.ue()
  const frameMbsOnlyFlag = reader.u1()
  if (!frameMbsOnlyFlag) reader.u1() // mb_adaptive_frame_field_flag
  reader.u1() // direct_8x8_inference_flag

  let cropLeft = 0
  let cropRight = 0
  let cropTop = 0
  let cropBottom = 0
  const frameCroppingFlag = reader.u1()
  if (frameCroppingFlag) {
    cropLeft = reader.ue()
    cropRight = reader.ue()
    cropTop = reader.ue()
    cropBottom = reader.ue()
  }

  const chromaArrayType = separateColourPlaneFlag ? 0 : chromaFormatIdc
  const subWidthC = chromaArrayType === 0 ? 1 : chromaFormatIdc === 3 ? 1 : 2
  const subHeightC = chromaArrayType === 0 ? 1 : chromaFormatIdc === 1 ? 2 : 1
  const cropUnitX = chromaArrayType === 0 ? 1 : subWidthC
  const cropUnitY = chromaArrayType === 0 ? 2 - frameMbsOnlyFlag : subHeightC * (2 - frameMbsOnlyFlag)

  const width = (picWidthInMbsMinus1 + 1) * 16 - cropUnitX * (cropLeft + cropRight)
  const height =
    (2 - frameMbsOnlyFlag) * (picHeightInMapUnitsMinus1 + 1) * 16 -
    cropUnitY * (cropTop + cropBottom)

  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    throw new Error(`h264: SPS 解析得到非法尺寸 ${width}x${height}`)
  }

  return {
    profileIdc,
    constraintFlags,
    levelIdc,
    width,
    height,
    chromaFormatIdc,
    frameMbsOnlyFlag,
  }
}

function hex2(v: number): string {
  return (v & 0xff).toString(16).toUpperCase().padStart(2, '0')
}

/** "avc1.PPCCLL"（大写十六进制）。传 SpsInfo 可跳过重复解析。 */
export function codecString(sps: Uint8Array | SpsInfo): string {
  const info = isSpsInfo(sps) ? sps : parseSps(sps)
  return `avc1.${hex2(info.profileIdc)}${hex2(info.constraintFlags)}${hex2(info.levelIdc)}`
}

function isSpsInfo(v: Uint8Array | SpsInfo): v is SpsInfo {
  return typeof (v as SpsInfo).profileIdc === 'number'
}

/**
 * 构造 AVCDecoderConfigurationRecord（ISO/IEC 14496-15 §5.3.3.1）：
 *   version=1 | profile | compat | level | 0xFF（4 字节长度域） |
 *   0xE1 + u16 SPS 长度 + SPS | 1 + u16 PPS 长度 + PPS
 * 注：SPS/PPS 必须是裸 NAL（不含起始码），带起始码时自动剥离。
 */
export function buildAvcC(sps: Uint8Array, pps: Uint8Array): Uint8Array {
  const spsNal = stripStartCode(sps)
  const ppsNal = stripStartCode(pps)
  if (spsNal.length < 4) throw new Error('h264: buildAvcC 需要有效 SPS')
  if (ppsNal.length < 2) throw new Error('h264: buildAvcC 需要有效 PPS')
  if (nalType(spsNal) !== 7) throw new Error(`h264: buildAvcC 第一个参数应为 SPS（nal type=${nalType(spsNal)}）`)
  if (nalType(ppsNal) !== 8) throw new Error(`h264: buildAvcC 第二个参数应为 PPS（nal type=${nalType(ppsNal)}）`)
  if (spsNal.length > 0xffff || ppsNal.length > 0xffff) throw new Error('h264: SPS/PPS 过长')

  // 6 字节固定头 + u16+SPS + 1 字节 PPS 数 + u16+PPS
  const total = 6 + (2 + spsNal.length) + 1 + (2 + ppsNal.length)
  const out = new Uint8Array(total)
  const view = new DataView(out.buffer)
  let off = 0
  out[off++] = 1 // configurationVersion
  out[off++] = spsNal[1] // AVCProfileIndication
  out[off++] = spsNal[2] // profile_compatibility
  out[off++] = spsNal[3] // AVCLevelIndication
  out[off++] = 0xff // 6 bits reserved(111111) + lengthSizeMinusOne(11) = 4 字节长度域
  out[off++] = 0xe1 // 3 bits reserved(111) + numOfSequenceParameterSets(1)
  view.setUint16(off, spsNal.length, false)
  off += 2
  out.set(spsNal, off)
  off += spsNal.length
  out[off++] = 1 // numOfPictureParameterSets
  view.setUint16(off, ppsNal.length, false)
  off += 2
  out.set(ppsNal, off)
  off += ppsNal.length
  if (off !== total) throw new Error('h264: buildAvcC 长度计算不一致')
  return out
}

// ---------------------------------------------------------------------------
// base64（hello 消息里的 sps_b64）
// ---------------------------------------------------------------------------

export function base64ToBytes(b64: string): Uint8Array {
  const normalized = b64.trim()
  if (!normalized) return new Uint8Array(0)
  if (typeof atob === 'function') {
    const bin = atob(normalized)
    const out = new Uint8Array(bin.length)
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
    return out
  }
  // Node / 测试环境兜底
  const nodeBuffer = (globalThis as { Buffer?: { from(s: string, enc: string): Uint8Array } }).Buffer
  if (nodeBuffer) return Uint8Array.from(nodeBuffer.from(normalized, 'base64'))
  throw new Error('h264: 当前环境无 base64 解码能力')
}

export function bytesToBase64(bytes: Uint8Array): string {
  if (typeof btoa === 'function') {
    let bin = ''
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
    return btoa(bin)
  }
  const nodeBuffer = (globalThis as { Buffer?: { from(b: Uint8Array): { toString(enc: string): string } } }).Buffer
  if (nodeBuffer) return nodeBuffer.from(bytes).toString('base64')
  throw new Error('h264: 当前环境无 base64 编码能力')
}

// ---------------------------------------------------------------------------
// 单元级自测（无真机 / 无浏览器环境下的正确性证明）
// ---------------------------------------------------------------------------

interface BitWriter {
  bits: number[]
}

function bw(): BitWriter {
  return { bits: [] }
}
function bwU(w: BitWriter, n: number, v: number): void {
  for (let i = n - 1; i >= 0; i--) w.bits.push((v >>> i) & 1)
}
function bwUe(w: BitWriter, v: number): void {
  const codeNum = v + 1
  let len = 0
  while (2 ** len <= codeNum) len++
  for (let i = 0; i < len - 1; i++) w.bits.push(0)
  bwU(w, len, codeNum)
}
function bwSe(w: BitWriter, v: number): void {
  bwUe(w, v <= 0 ? -2 * v : 2 * v - 1)
}
function bwBytes(w: BitWriter): Uint8Array {
  const out = new Uint8Array(Math.ceil(w.bits.length / 8))
  for (let i = 0; i < w.bits.length; i++) {
    if (w.bits[i]) out[i >> 3] |= 0x80 >> (i & 7)
  }
  return out
}
function emulationPrevent(rbsp: Uint8Array): Uint8Array {
  const out: number[] = []
  let zeros = 0
  for (const b of rbsp) {
    if (zeros >= 2 && b <= 3) {
      out.push(3)
      zeros = 0
    }
    out.push(b)
    zeros = b === 0 ? zeros + 1 : 0
  }
  return Uint8Array.from(out)
}

interface SyntheticSpsOptions {
  profileIdc?: number
  constraintFlags?: number
  levelIdc?: number
  widthMbsMinus1?: number
  heightMapUnitsMinus1?: number
  cropBottom?: number
  chromaFormatIdc?: number
}

/** 生成一份语法合法的合成 SPS（默认 1920x1080 baseline 4:2:0）。 */
function buildSyntheticSps(opts: SyntheticSpsOptions = {}): Uint8Array {
  const profileIdc = opts.profileIdc ?? 66
  const constraintFlags = opts.constraintFlags ?? 0xc0
  const levelIdc = opts.levelIdc ?? 30
  const widthMbsMinus1 = opts.widthMbsMinus1 ?? 119 // (119+1)*16 = 1920
  const heightMapUnitsMinus1 = opts.heightMapUnitsMinus1 ?? 67 // 68*16 = 1088，再裁 8 => 1080
  const cropBottom = opts.cropBottom ?? 4 // CropUnitY=2 -> 8px
  const highProfile = HIGH_PROFILE_IDCS.has(profileIdc)

  const w = bw()
  bwU(w, 8, profileIdc)
  bwU(w, 8, constraintFlags)
  bwU(w, 8, levelIdc)
  bwUe(w, 0) // sps id
  if (highProfile) {
    bwUe(w, opts.chromaFormatIdc ?? 1) // chroma_format_idc
    bwUe(w, 0) // bit_depth_luma_minus8
    bwUe(w, 0) // bit_depth_chroma_minus8
    bwU(w, 1, 0) // qpprime_y_zero_transform_bypass_flag
    bwU(w, 1, 0) // seq_scaling_matrix_present_flag
  }
  bwUe(w, 0) // log2_max_frame_num_minus4
  bwUe(w, 0) // pic_order_cnt_type = 0
  bwUe(w, 0) // log2_max_pic_order_cnt_lsb_minus4
  bwUe(w, 1) // max_num_ref_frames
  bwU(w, 1, 0) // gaps_in_frame_num_value_allowed_flag
  bwUe(w, widthMbsMinus1)
  bwUe(w, heightMapUnitsMinus1)
  bwU(w, 1, 1) // frame_mbs_only_flag
  bwU(w, 1, 1) // direct_8x8_inference_flag
  bwU(w, 1, 1) // frame_cropping_flag
  bwUe(w, 0) // crop left
  bwUe(w, 0) // crop right
  bwUe(w, 0) // crop top
  bwUe(w, cropBottom)
  bwU(w, 1, 0) // vui_parameters_present_flag
  bwU(w, 1, 1) // rbsp_stop_one_bit
  const rbsp = emulationPrevent(bwBytes(w))
  const nal = new Uint8Array(1 + rbsp.length)
  nal[0] = 0x67 // forbidden=0, nal_ref_idc=3, type=7
  nal.set(rbsp, 1)
  return nal
}

function buildSyntheticPps(): Uint8Array {
  // 最小的合法 PPS 开头：header(0x68) + pps_id ue(0) + sps_id ue(0) + entropy_coding_mode_flag + bottom_field_pic_order...
  // 这里只用于 avcC 结构测试，内容不需要能被解析。
  return Uint8Array.from([0x68, 0xce, 0x3c, 0x80])
}

/**
 * 单元级自测：返回断言失败信息数组（空数组 = 全部通过）。
 * 可在浏览器 console 执行：`import('@/lib/h264').then(m => console.log(m.__selfTest()))`，
 * 或由 features/mirror/dev/selfTest.ts 的自检入口调用。
 */
export function __selfTest(): string[] {
  const failures: string[] = []
  const check = (cond: boolean, msg: string): void => {
    if (!cond) failures.push(msg)
  }

  // --- 1. splitNals：3/4 字节起始码混用
  const sps = buildSyntheticSps()
  const pps = buildSyntheticPps()
  const idrPayload = Uint8Array.from([0x65, 0x88, 0x84, 0x00, 0x21])
  const stream4 = concatBytes([
    Uint8Array.from([0, 0, 0, 1]), sps,
    Uint8Array.from([0, 0, 0, 1]), pps,
    Uint8Array.from([0, 0, 1]), idrPayload,
  ])
  const nals4 = splitNals(stream4)
  check(nals4.length === 3, `splitNals 应切出 3 个 NAL，实际 ${nals4.length}`)
  check(nals4[0] && bytesEqual(nals4[0], sps), 'splitNals[0] 应等于 SPS')
  check(nals4[1] && bytesEqual(nals4[1], pps), 'splitNals[1] 应等于 PPS')
  check(nals4[2] && bytesEqual(nals4[2], idrPayload), 'splitNals[2] 应等于 IDR')
  check(nalType(nals4[0]) === 7 && nalType(nals4[1]) === 8 && nalType(nals4[2]) === 5, 'splitNals nalType 断言失败')
  check(isKeyframeFrame(nals4), '含 IDR 的帧应判定为关键帧')
  check(!isKeyframeFrame([sps, pps]), '只有 SPS/PPS 时不应判定为关键帧')

  // --- 2. splitNals：无起始码兜底（纯 payload）
  const payloadOnly = Uint8Array.from([0x65, 0x88, 0x84, 0x21, 0x00, 0x11])
  const nalsBare = splitNals(payloadOnly)
  check(nalsBare.length === 1 && bytesEqual(nalsBare[0], payloadOnly), 'splitNals 无起始码时应整条兜底')
  // 注意：payloadOnly 末尾不是 0，不会被裁剪
  check(isKeyframeFrame(nalsBare), '裸 IDR payload 应判定为关键帧')

  // --- 3. splitNals：前导零 + 尾部填充零裁剪
  const withLeadingZeros = concatBytes([Uint8Array.from([0, 0, 0, 0]), Uint8Array.from([0, 0, 0, 1]), sps, Uint8Array.from([0, 0, 0, 1]), pps, Uint8Array.from([0, 0])])
  const nalsLead = splitNals(withLeadingZeros)
  check(nalsLead.length === 2, `前导零/尾部零应切出 2 个 NAL，实际 ${nalsLead.length}`)
  check(nalsLead[0] && bytesEqual(nalsLead[0], sps), '前导零场景 SPS 应一致')
  check(nalsLead[1] && bytesEqual(nalsLead[1], pps), '尾部零应被裁剪')

  // --- 4. parseSps：baseline 1920x1080
  const info = parseSps(sps)
  check(info.profileIdc === 66, `profileIdc 应为 66，实际 ${info.profileIdc}`)
  check(info.constraintFlags === 0xc0, `constraintFlags 应为 0xC0，实际 ${info.constraintFlags}`)
  check(info.levelIdc === 30, `levelIdc 应为 30，实际 ${info.levelIdc}`)
  check(info.width === 1920, `width 应为 1920，实际 ${info.width}`)
  check(info.height === 1080, `height 应为 1080，实际 ${info.height}`)
  check(codecString(sps) === 'avc1.42C01E', `codecString 应为 avc1.42C01E，实际 ${codecString(sps)}`)
  check(codecString(info) === 'avc1.42C01E', 'codecString(SpsInfo) 应与解析结果一致')

  // --- 5. parseSps：high profile 4:2:0 1920x1080
  const spsHigh = buildSyntheticSps({ profileIdc: 100, constraintFlags: 0x00, levelIdc: 40, chromaFormatIdc: 1 })
  const infoHigh = parseSps(spsHigh)
  check(infoHigh.profileIdc === 100, 'high profile profileIdc 应为 100')
  check(infoHigh.chromaFormatIdc === 1, 'high profile chromaFormatIdc 应为 1')
  check(infoHigh.width === 1920 && infoHigh.height === 1080, `high profile 尺寸应为 1920x1080，实际 ${infoHigh.width}x${infoHigh.height}`)
  check(codecString(spsHigh) === 'avc1.640028', `high profile codecString 应为 avc1.640028，实际 ${codecString(spsHigh)}`)

  // --- 6. parseSps：带起始码输入
  const spsWithSc = concatBytes([Uint8Array.from([0, 0, 0, 1]), sps])
  check(parseSps(spsWithSc).width === 1920, 'parseSps 应容忍带起始码输入')

  // --- 7. parseSps：非法输入
  let threw = false
  try {
    parseSps(Uint8Array.from([0x68, 0x00]))
  } catch {
    threw = true
  }
  check(threw, 'parseSps 对非 SPS 数据应抛错')

  // --- 8. buildAvcC 结构
  const avcc = buildAvcC(sps, pps)
  const expectedLen = 6 + 2 + sps.length + 1 + 2 + pps.length
  check(avcc.length === expectedLen, `avcC 长度应为 ${expectedLen}，实际 ${avcc.length}`)
  const avccView = new DataView(avcc.buffer, avcc.byteOffset, avcc.byteLength)
  check(avcc[0] === 1, 'avcC version 应为 1')
  check(avcc[1] === sps[1] && avcc[2] === sps[2] && avcc[3] === sps[3], 'avcC profile/compat/level 应来自 SPS')
  check(avcc[4] === 0xff, 'avcC lengthSizeMinusOne 应为 0xFF')
  check(avcc[5] === 0xe1, 'avcC numOfSequenceParameterSets 应为 0xE1')
  check(avccView.getUint16(6, false) === sps.length, 'avcC 内 SPS 长度字段错误')
  check(bytesEqual(avcc.subarray(8, 8 + sps.length), sps), 'avcC 内 SPS 内容错误')
  const ppsCountOff = 8 + sps.length
  check(avcc[ppsCountOff] === 1, 'avcC numOfPictureParameterSets 应为 1')
  check(avccView.getUint16(ppsCountOff + 1, false) === pps.length, 'avcC 内 PPS 长度字段错误')
  check(bytesEqual(avcc.subarray(ppsCountOff + 3, ppsCountOff + 3 + pps.length), pps), 'avcC 内 PPS 内容错误')

  // --- 9. nalsToAvcc
  const avccFrame = nalsToAvcc(nals4) // 去掉 SPS/PPS，只剩 IDR
  check(avccFrame.length === 4 + idrPayload.length, `nalsToAvcc 长度应为 ${4 + idrPayload.length}，实际 ${avccFrame.length}`)
  check(new DataView(avccFrame.buffer).getUint32(0, false) === idrPayload.length, 'nalsToAvcc 长度前缀错误')
  check(bytesEqual(avccFrame.subarray(4), idrPayload), 'nalsToAvcc IDR 内容错误')
  const avccKeep = nalsToAvcc(nals4, { dropParameterSets: false })
  check(new DataView(avccKeep.buffer).getUint32(0, false) === sps.length, 'nalsToAvcc 保留参数集时首个长度应为 SPS')

  // --- 10. base64 往返（依赖 atob/btoa 或 Buffer）
  try {
    const roundTrip = base64ToBytes(bytesToBase64(sps))
    check(bytesEqual(roundTrip, sps), 'base64 往返应一致')
  } catch (err) {
    failures.push(`base64 往返失败：${(err as Error).message}`)
  }

  return failures
}

/** 开发期自检入口：在控制台打印结果（失败时抛错便于定位）。返回失败消息列表。 */
export function runSelfTestOrThrow(): string[] {
  const failures = __selfTest()
  if (failures.length > 0) {
    // eslint-disable-next-line no-console
    console.error('[h264 __selfTest] 失败:')
    for (const f of failures) console.error(' -', f)
    throw new Error(`h264 自测失败 ${failures.length} 项`)
  }
  // eslint-disable-next-line no-console
  console.info('[h264 __selfTest] 全部通过')
  return failures
}
