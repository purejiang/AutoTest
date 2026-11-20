"""无真机测试：VideoFrameReader 从假 socket 读 12 字节头帧（含短读/EOF/stream 头）。

假 socket 提供 ``recv(n)``（可用 chunk 限制模拟短读）与 ``settimeout``，
不需要真机、不联网。
"""

from __future__ import annotations

import socket
import struct

import pytest

from app.android import scrcpy
from tests.test_media_h264 import annexb, build_idr, build_pps, build_sps


class FakeSocket:
    """按 chunk 返回数据的假 socket（模拟短读）。"""

    def __init__(self, data: bytes, chunk: int = 4096) -> None:
        self.data = data
        self.chunk = chunk
        self.pos = 0
        self.timeout: float | None = None
        self.closed = False
        self.recv_calls = 0

    def recv(self, size: int) -> bytes:
        self.recv_calls += 1
        if self.pos >= len(self.data):
            return b""
        take = min(size, self.chunk, len(self.data) - self.pos)
        result = self.data[self.pos : self.pos + take]
        self.pos += take
        return result

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def close(self) -> None:
        self.closed = True


def test_read_two_frames_with_short_reads() -> None:
    payload_a = annexb(build_sps(), build_pps(), build_idr())
    payload_b = annexb(b"\x41\x01\x02\x03")
    data = scrcpy.pack_frame(1_000, payload_a) + scrcpy.pack_frame(2_000_000, payload_b)
    sock = FakeSocket(data, chunk=3)  # 每个 recv 最多 3 字节 → 强测短读重试
    reader = scrcpy.VideoFrameReader(sock)
    pts_a, out_a = reader.read_frame()
    first_info = reader.last_info
    pts_b, out_b = reader.read_frame()
    assert pts_a == 1_000
    assert out_a == payload_a
    assert pts_b == 2_000_000
    assert out_b == payload_b
    # 12 字节头 + payload 都被完整读取
    assert sock.pos == len(data)
    assert sock.recv_calls > 4
    # SPS/PPS 被缓存，geometry 可解析
    assert reader.geometry.width == 1080
    assert reader.geometry.height == 1920
    assert reader.geometry.codec == "avc1.64001F"
    assert reader.geometry.sps == build_sps()
    assert reader.geometry.pps == build_pps()
    assert first_info is not None
    assert first_info.keyframe is True
    assert first_info.nals == (7, 8, 5)
    assert first_info.sps_changed is True
    assert reader.last_info is not None
    assert reader.last_info.keyframe is False


def test_read_frame_header_layout() -> None:
    payload = b"\x00\x00\x00\x01\x65\x01"
    raw = scrcpy.pack_frame(0x0102030405060708, payload)
    assert len(raw) == 12 + len(payload)
    assert struct.unpack(">QI", raw[:12]) == (0x0102030405060708, len(payload))
    assert raw[12:] == payload
    assert scrcpy.unpack_frame_header(raw[:12]) == (0x0102030405060708, len(payload))


def test_reader_raises_on_eof() -> None:
    reader = scrcpy.VideoFrameReader(FakeSocket(b""))
    with pytest.raises(scrcpy.ScrcpyDisconnected):
        reader.read_frame()


def test_reader_raises_on_truncated_payload() -> None:
    payload = annexb(build_idr())
    data = scrcpy.pack_frame(10, payload)[:-2]  # 少两个字节
    reader = scrcpy.VideoFrameReader(FakeSocket(data))
    with pytest.raises(scrcpy.ScrcpyDisconnected):
        reader.read_frame()


def test_reader_rejects_absurd_payload_length() -> None:
    bogus_header = struct.pack(">QI", 0, scrcpy.MAX_PAYLOAD_BYTES + 1)
    reader = scrcpy.VideoFrameReader(FakeSocket(bogus_header))
    with pytest.raises(scrcpy.ScrcpyProtocolError):
        reader.read_frame()


def test_reader_stream_headers_device_meta_v5() -> None:
    # v5：固定 64 字节 UTF-8 设备名（NUL 补齐，无长度前缀）
    device_name = b"Pixel 9 Pro"
    header = (
        device_name.ljust(scrcpy.DEVICE_NAME_FIELD_LENGTH, b"\x00")
        + scrcpy.pack_frame(123, annexb(build_sps(), build_pps(), build_idr()))
    )
    reader = scrcpy.VideoFrameReader(FakeSocket(header))
    stream_header = reader.read_stream_headers(
        scrcpy.StreamHeaderSpec(device_meta=True, dummy_byte=False, stream_meta=False)
    )
    assert stream_header.device_name == "Pixel 9 Pro"
    assert stream_header.dummy_byte is False
    pts, payload = reader.read_frame()
    assert pts == 123
    assert payload
    assert reader.geometry.width == 1080


def test_reader_stream_headers_with_dummy_byte() -> None:
    # v5 顺序：dummy 字节（先）→ 64 字节设备名 → 帧
    device_name = b"phone"
    header = (
        b"\x00"
        + device_name.ljust(scrcpy.DEVICE_NAME_FIELD_LENGTH, b"\x00")
        + scrcpy.pack_frame(7, annexb(build_sps(720, 1280), build_pps(), build_idr()))
    )
    reader = scrcpy.VideoFrameReader(FakeSocket(header))
    stream_header = reader.read_stream_headers(
        scrcpy.StreamHeaderSpec(device_meta=True, dummy_byte=True, stream_meta=False)
    )
    assert stream_header.dummy_byte is True
    assert stream_header.device_name == "phone"
    pts, _payload = reader.read_frame()
    assert pts == 7
    assert reader.geometry.width == 720
    assert reader.geometry.height == 1280


def test_reader_stream_headers_truncated() -> None:
    reader = scrcpy.VideoFrameReader(FakeSocket(b"name"))
    with pytest.raises(scrcpy.ScrcpyDisconnected):
        reader.read_stream_headers(
            scrcpy.StreamHeaderSpec(device_meta=True, dummy_byte=False, stream_meta=False)
        )


def test_v5_full_stream_headers_and_session_meta() -> None:
    """v5 官方握手：dummy → 64B 名字 → u32 codec id → session meta 伪包头 → config → keyframe。"""
    dummy = b"\x00"
    name = b"demo-serial".ljust(scrcpy.DEVICE_NAME_FIELD_LENGTH, b"\x00")
    codec_id = int.from_bytes(b"h264", "big")
    session_meta = struct.pack(">III", 0x80000000, 1080, 2400)  # flags|width, height
    config_payload = annexb(build_sps(1080, 2400), build_pps())
    config_packet = struct.pack(
        ">QI", scrcpy.PACKET_FLAG_CONFIG, len(config_payload)
    ) + config_payload
    keyframe = annexb(build_idr())
    key_packet = struct.pack(
        ">QI", scrcpy.PACKET_FLAG_KEY_FRAME | 5000, len(keyframe)
    ) + keyframe
    data = dummy + name + struct.pack(">I", codec_id) + session_meta + config_packet + key_packet
    sock = FakeSocket(data, chunk=5)
    reader = scrcpy.VideoFrameReader(sock)
    header = reader.read_stream_headers(scrcpy.StreamHeaderSpec())
    assert header.dummy_byte is True
    assert header.device_name == "demo-serial"
    assert header.codec == "h264"
    # 第一个 read_frame 是 session meta（无 payload）
    pts, payload = reader.read_frame()
    assert (pts, payload) == (0, b"")
    assert reader.last_info is not None
    assert reader.last_info.session_meta is True
    assert (reader.geometry.width, reader.geometry.height) == (1080, 2400)
    assert reader.session_size == (1080, 2400)
    # 第二个是 config 包（SPS/PPS）
    pts, payload = reader.read_frame()
    assert payload == config_payload
    assert reader.last_info is not None
    assert reader.last_info.config is True
    assert reader.codec_config == config_payload
    assert reader.geometry.codec == "avc1.64001F"
    assert reader.geometry.sps is not None
    # 第三个是关键帧（bit61 + 真实 pts）
    pts, payload = reader.read_frame()
    assert pts == 5000
    assert payload == keyframe
    assert reader.last_info is not None
    assert reader.last_info.keyframe is True


def test_session_meta_mid_stream_size_change() -> None:
    """旋转：流中重新发 session meta（尺寸交换）。"""
    first = struct.pack(">III", 0x80000000, 1080, 2400)
    second = struct.pack(">III", 0x80000000, 2400, 1080)
    frame = scrcpy.pack_frame(1, annexb(build_sps(2400, 1080), build_pps(), build_idr()))
    reader = scrcpy.VideoFrameReader(FakeSocket(first + frame + second + frame))
    reader.read_frame()
    assert (reader.geometry.width, reader.geometry.height) == (1080, 2400)
    reader.read_frame()
    reader.read_frame()
    assert (reader.geometry.width, reader.geometry.height) == (2400, 1080)


def test_decode_session_meta_helper() -> None:
    header = struct.pack(">III", 0x80000001, 720, 1280)
    assert scrcpy.decode_session_meta(header) == (720, 1280, True)
    header = struct.pack(">III", 0x80000000, 720, 1280)
    assert scrcpy.decode_session_meta(header) == (720, 1280, False)
    with pytest.raises(scrcpy.ScrcpyProtocolError):
        scrcpy.decode_session_meta(scrcpy.pack_frame(1, b"xx")[:12])


def test_reader_frame_header_flags_mask_pts() -> None:
    payload = annexb(build_idr())
    raw_header = struct.pack(
        ">QI", 0x2000000000000000 | 1234, len(payload)  # KEY_FRAME flag + pts
    )
    reader = scrcpy.VideoFrameReader(FakeSocket(raw_header + payload))
    pts, out = reader.read_frame()
    assert pts == 1234
    assert out == payload
    assert reader.last_info is not None
    assert reader.last_info.keyframe is True


def test_reader_config_packet_flagged() -> None:
    config_payload = annexb(build_sps(), build_pps())
    raw_header = struct.pack(">QI", scrcpy.PACKET_FLAG_CONFIG, len(config_payload))
    keyframe = annexb(build_idr())
    key_header = struct.pack(
        ">QI", scrcpy.PACKET_FLAG_KEY_FRAME | 42, len(keyframe)
    )
    reader = scrcpy.VideoFrameReader(FakeSocket(raw_header + config_payload + key_header + keyframe))
    pts, out = reader.read_frame()
    assert pts == 0
    assert out == config_payload
    assert reader.last_info is not None
    assert reader.last_info.config is True
    assert reader.last_info.keyframe is False
    # config 包里的 SPS/PPS 已被缓存，几何可解析
    assert reader.geometry.width == 1080
    pts, out = reader.read_frame()
    assert pts == 42
    assert reader.last_info is not None
    assert reader.last_info.config is False
    assert reader.last_info.keyframe is True


def test_decode_frame_header_helper() -> None:
    size = 10
    header = struct.pack(">QI", scrcpy.PACKET_FLAG_CONFIG | scrcpy.PACKET_FLAG_KEY_FRAME | 7, size)
    assert scrcpy.decode_frame_header(header) == (7, size, True, True)
    assert scrcpy.unpack_frame_header(header) == (7, size)


def test_reader_geometry_changes_with_new_sps() -> None:
    first = scrcpy.pack_frame(1, annexb(build_sps(1080, 1920), build_pps(), build_idr()))
    second = scrcpy.pack_frame(2, annexb(build_sps(1920, 1080), build_pps(), build_idr()))
    reader = scrcpy.VideoFrameReader(FakeSocket(first + second))
    reader.read_frame()
    assert (reader.geometry.width, reader.geometry.height) == (1080, 1920)
    reader.read_frame()
    assert (reader.geometry.width, reader.geometry.height) == (1920, 1080)
    assert reader.last_info is not None
    assert reader.last_info.sps_changed is True


def test_reader_close_closes_socket() -> None:
    sock = FakeSocket(b"")
    reader = scrcpy.VideoFrameReader(sock)
    reader.close()
    assert sock.closed is True


def test_real_socketpair() -> None:
    """用真实 socketpair 验证阻塞读取（仍无真机）。"""
    left, right = socket.socketpair()
    try:
        payload = annexb(build_sps(), build_pps(), build_idr())
        frame = scrcpy.pack_frame(999, payload)
        right.sendall(frame)
        reader = scrcpy.VideoFrameReader(left)
        pts, out = reader.read_frame()
        assert pts == 999
        assert out == payload
    finally:
        left.close()
        right.close()


# ---------------------------------------------------------------- 真机抓包字节（v5.0.1 / Android 16，serial 已脱敏）
REAL_DEVICE_NAME = "23127PN0CC"
REAL_SPS_HEX = "6764001facb40b832f373505060506d0a135"
REAL_PPS_HEX = "68ee06f2c0"
REAL_CONFIG_HEX = "000000016764001facb40b832f373505060506d0a1350000000168ee06f2c0"
REAL_SESSION = (358, 800)
REAL_FIRST_PTS = 156396867456
REAL_FIRST_HEAD_HEX = "0000000165b84f7175da5b80f282acd67cad5960196df794"


def test_real_capture_v5_stream_layout() -> None:
    """用真机抓到的字节（dummy/64B 名/codec/session meta/config/keyframe）回放解析。"""
    dummy = b"\x00"
    name = REAL_DEVICE_NAME.encode().ljust(scrcpy.DEVICE_NAME_FIELD_LENGTH, b"\x00")
    codec_id = struct.pack(">I", int.from_bytes(b"h264", "big"))
    width, height = REAL_SESSION
    session_meta = struct.pack(">III", 0x80000000, width, height)
    config_payload = bytes.fromhex(REAL_CONFIG_HEX)
    config_packet = struct.pack(">QI", scrcpy.PACKET_FLAG_CONFIG, len(config_payload)) + config_payload
    keyframe_payload = bytes.fromhex(REAL_FIRST_HEAD_HEX)
    key_packet = (
        struct.pack(">QI", scrcpy.PACKET_FLAG_KEY_FRAME | REAL_FIRST_PTS, len(keyframe_payload))
        + keyframe_payload
    )
    reader = scrcpy.VideoFrameReader(FakeSocket(dummy + name + codec_id + session_meta + config_packet + key_packet))
    header = reader.read_stream_headers(scrcpy.StreamHeaderSpec())
    assert header.device_name == REAL_DEVICE_NAME
    assert header.codec == "h264"
    # session meta
    assert reader.read_frame() == (0, b"")
    assert reader.session_size == REAL_SESSION
    # config = 真机 SPS+PPS（Annex B）
    pts, payload = reader.read_frame()
    assert payload == config_payload
    assert reader.codec_config == config_payload
    assert reader.geometry.sps.hex() == REAL_SPS_HEX
    assert reader.geometry.pps.hex() == REAL_PPS_HEX
    assert reader.geometry.codec == "avc1.64001F"
    assert (reader.geometry.width, reader.geometry.height) == REAL_SESSION
    # keyframe：pts 掩码 + bit61
    pts, payload = reader.read_frame()
    assert pts == REAL_FIRST_PTS
    assert payload == keyframe_payload
    assert reader.last_info is not None and reader.last_info.keyframe is True
    assert reader.last_info.nals == (scrcpy.NAL_IDR,)
