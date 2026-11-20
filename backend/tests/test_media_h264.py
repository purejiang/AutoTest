"""无真机测试：H.264 NAL 扫描 / SPS 解析 / codec 字符串 / 关键帧 / avCC。

SPS 用测试内 BitWriter 合成（含防竞争字节），不依赖网络与真机。
"""

from __future__ import annotations

import pytest

from app.android import scrcpy

HIGH_PROFILES = {100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135}


class BitWriter:
    """写 RBSP 比特流（ue/se/u），便于造合法 SPS。"""

    def __init__(self) -> None:
        self.bits: list[int] = []

    def u(self, value: int, bits: int) -> None:
        for shift in range(bits - 1, -1, -1):
            self.bits.append((value >> shift) & 1)

    def ue(self, value: int) -> None:
        code = value + 1
        zeros = code.bit_length() - 1
        self.bits.extend([0] * zeros)
        for shift in range(zeros, -1, -1):
            self.bits.append((code >> shift) & 1)

    def se(self, value: int) -> None:
        self.ue(2 * value - 1 if value > 0 else -2 * value)

    def to_bytes(self) -> bytes:
        bits = [*self.bits, 1]  # rbsp_stop_one_bit
        while len(bits) % 8:
            bits.append(0)
        out = bytearray()
        for index in range(0, len(bits), 8):
            byte = 0
            for bit in bits[index : index + 8]:
                byte = (byte << 1) | bit
            out.append(byte)
        return bytes(out)


def _emulation_prevention(payload: bytes) -> bytes:
    out = bytearray()
    zeros = 0
    for byte in payload:
        if zeros >= 2 and byte <= 3:
            out.append(0x03)
            zeros = 0
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


def build_sps(
    width: int = 1080,
    height: int = 1920,
    profile: int = 0x64,
    constraint: int = 0x00,
    level: int = 0x1F,
    frame_mbs_only: int = 1,
) -> bytes:
    """合成一份最小合法 SPS（宽高按 16 对齐 + crop 修正）。"""
    writer = BitWriter()
    writer.u(profile, 8)
    writer.u(constraint, 8)
    writer.u(level, 8)
    writer.ue(0)  # seq_parameter_set_id
    if profile in HIGH_PROFILES:
        writer.ue(1)  # chroma_format_idc = 4:2:0
        writer.ue(0)  # bit_depth_luma_minus8
        writer.ue(0)  # bit_depth_chroma_minus8
        writer.u(0, 1)  # qpprime_y_zero_transform_bypass_flag
        writer.u(0, 1)  # seq_scaling_matrix_present_flag
    writer.ue(0)  # log2_max_frame_num_minus4
    writer.ue(0)  # pic_order_cnt_type
    writer.ue(0)  # log2_max_pic_order_cnt_lsb_minus4
    writer.ue(1)  # max_num_ref_frames
    writer.u(0, 1)  # gaps_in_frame_num_value_allowed_flag
    mb_width = (width + 15) // 16
    crop_right = (mb_width * 16 - width) // 2
    writer.ue(mb_width - 1)  # pic_width_in_mbs_minus1
    mb_height = (height + 15) // 16
    # 4:2:0 → 垂直 crop 单位 subHeightC=2（frame_mbs_only 时 map unit 高度 = 16）
    crop_bottom = (mb_height * 16 - height) // 2
    writer.ue(mb_height - 1)  # pic_height_in_map_units_minus1
    writer.u(frame_mbs_only, 1)
    if not frame_mbs_only:
        writer.u(0, 1)  # mb_adaptive_frame_field_flag
    writer.u(1, 1)  # direct_8x8_inference_flag
    if crop_right or crop_bottom:
        writer.u(1, 1)  # frame_cropping_flag
        writer.ue(0)  # crop_left
        writer.ue(crop_right)
        writer.ue(0)  # crop_top
        writer.ue(crop_bottom)
    else:
        writer.u(0, 1)
    writer.u(0, 1)  # vui_parameters_present_flag
    return b"\x67" + _emulation_prevention(writer.to_bytes())


def build_pps() -> bytes:
    return bytes.fromhex("68ce3c80")


def build_idr(size: int = 32) -> bytes:
    return b"\x65" + bytes(range(1, size))


def build_slice(size: int = 24) -> bytes:
    return b"\x41" + bytes(range(2, size))


def annexb(*nals: bytes) -> bytes:
    return b"".join(b"\x00\x00\x00\x01" + nal for nal in nals)


# ---------------------------------------------------------------- NAL 扫描
def test_split_nals_handles_3_and_4_byte_start_codes() -> None:
    sps, pps, idr = build_sps(), build_pps(), build_idr()
    data = b"\x00\x00\x00\x01" + sps + b"\x00\x00\x01" + pps + b"\x00\x00\x00\x01" + idr
    nals = scrcpy.split_nals(data)
    assert nals == [sps, pps, idr]
    assert [scrcpy.nal_type(nal) for nal in nals] == [7, 8, 5]


def test_split_nals_trailing_zero_of_4byte_prefix_not_in_nal() -> None:
    data = annexb(build_sps())
    # 同时出现连续起始码：前一个 NAL 不应带上后一个起始码前导 0
    mixed = b"\x00\x00\x00\x01" + b"\x41\x01\x02" + b"\x00\x00\x00\x01" + b"\x65\x03\x04"
    nals = scrcpy.split_nals(mixed)
    assert nals == [b"\x41\x01\x02", b"\x65\x03\x04"]
    assert data


def test_split_nals_empty_and_garbage() -> None:
    assert scrcpy.split_nals(b"") == []
    assert scrcpy.split_nals(b"not annexb") == []


# ---------------------------------------------------------------- codec string
def test_codec_string_from_sps_known_bytes() -> None:
    sps = build_sps(profile=0x64, constraint=0x00, level=0x1F)
    assert scrcpy.codec_string_from_sps(sps) == "avc1.64001F"
    sps = build_sps(profile=0x4D, constraint=0x40, level=0x1E)
    assert scrcpy.codec_string_from_sps(sps) == "avc1.4D401E"
    # 6 位大写十六进制（契约/前端约定）
    assert len(scrcpy.codec_string_from_sps(sps).split(".")[1]) == 6
    assert scrcpy.codec_string_from_sps(sps).split(".")[1] == scrcpy.codec_string_from_sps(sps).split(".")[1].upper()


def test_codec_string_rejects_non_sps() -> None:
    with pytest.raises(ValueError):
        scrcpy.codec_string_from_sps(b"\x68\x00\x00\x00")
    with pytest.raises(ValueError):
        scrcpy.codec_string_from_sps(b"\x67")


# ---------------------------------------------------------------- SPS 解析
def test_parse_sps_dimensions() -> None:
    for width, height in [(1080, 1920), (720, 1280), (1080, 2400), (1920, 1080)]:
        info = scrcpy.parse_sps(build_sps(width=width, height=height))
        assert (info.width, info.height) == (width, height)
        assert info.profile_idc == 0x64
        assert info.level_idc == 0x1F


def test_parse_sps_codec_property() -> None:
    info = scrcpy.parse_sps(build_sps(profile=0x42, constraint=0xE0, level=0x1E))
    assert info.codec == "avc1.42E01E"


def test_parse_sps_rejects_garbage() -> None:
    with pytest.raises(scrcpy.ScrcpyProtocolError):
        scrcpy.parse_sps(b"\x67\x01")
    with pytest.raises(scrcpy.ScrcpyProtocolError):
        scrcpy.parse_sps(b"\x68\x00\x00\x00")


# ---------------------------------------------------------------- keyframe
def test_is_keyframe() -> None:
    sps, pps, idr, slice_nal = build_sps(), build_pps(), build_idr(), build_slice()
    assert scrcpy.is_keyframe([sps, pps, idr]) is True
    assert scrcpy.is_keyframe([sps, pps, slice_nal]) is False
    assert scrcpy.is_keyframe([]) is False


def test_nal_types_of_payload() -> None:
    payload = annexb(build_sps(), build_pps(), build_idr())
    assert scrcpy.nal_types(payload) == [7, 8, 5]


# ---------------------------------------------------------------- avCC
def test_build_avcc_layout() -> None:
    sps, pps = build_sps(), build_pps()
    avcc = scrcpy.build_avcc(sps, pps)
    assert avcc[0] == 0x01  # configurationVersion
    assert avcc[1:4] == bytes(sps[1:4])
    assert avcc[4] == 0xFF  # lengthSizeMinusOne = 3
    assert avcc[5] == 0xE1  # numOfSequenceParameterSets = 1
    assert avcc[6:8] == len(sps).to_bytes(2, "big")
    assert avcc[8 : 8 + len(sps)] == sps
    offset = 8 + len(sps)
    assert avcc[offset] == 0x01  # numOfPictureParameterSets
    assert avcc[offset + 1 : offset + 3] == len(pps).to_bytes(2, "big")
    assert avcc[offset + 3 :] == pps
    assert len(avcc) == 11 + len(sps) + len(pps)


def test_build_avcc_rejects_wrong_nal_types() -> None:
    with pytest.raises(ValueError):
        scrcpy.build_avcc(build_pps(), build_pps())
    with pytest.raises(ValueError):
        scrcpy.build_avcc(build_sps(), build_sps())
