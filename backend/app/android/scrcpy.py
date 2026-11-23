"""scrcpy server 端集成（契约 §10）。

只做 **server 端**：adb 把 ``scrcpy-server-v5.0.1.jar`` 推到手机，用 ``app_process`` 拉起
``com.genymobile.scrcpy.Server``，再通过 adb forward/reverse 建立 localabstract 隧道，
从 socket 读 12 字节帧头的 H.264 Annex B 流（不做客户端窗口、不解码）。

结构：
- ``ServerConfig``：jar/参数/唯一 socket 名；
- ``verify_jar``：对照 ``backend/vendor/scrcpy.lock.json`` 校验 SHA256；
- ``start_server`` / ``stop_server``：推送、启动、连（按序：video → control）、自检（首帧 ≤8s）、清理；
- ``VideoFrameReader``：12 字节头（u64 BE pts_us + u32 BE len）+ payload；SPS/PPS 缓存、geometry；
- H.264 工具：NAL 扫描、NAL 类型、``codec_string_from_sps``、``is_keyframe``、``build_avcc``、
  最小 SPS 解析（宽高/裁剪）。

**v5.0.1 协议（已按官方 scrcpy-server-v5.0.1.jar 的 dex 反汇编核实）**
- server 参数是 ``key=value``，第一个参数是版本号 ``5.0.1``；socket 由 ``scid=<int>`` 派生：
  ``scrcpy``（scid=-1）或 ``scrcpy_<scid:%08x>``，因此本模块的 localabstract 名是
  ``scrcpy_<8位小写hex>``（随机 scid，天然唯一；``socket_name`` 字段仅作本进程逻辑名/日志）。
- ``tunnel_forward=true`` 时 server 端 ``LocalServerSocket.accept()``：按序 accept video → control，
  客户端（本模块）用 ``adb forward tcp:<port> localabstract:<name>`` 后 connect。
- 可选 stream 头（默认都发，本模块显式关掉后再按模式降级）：
  1. ``send_dummy_byte=true`` → video socket 上先写 1 个 dummy 字节；
  2. ``send_device_meta=true`` → 固定 64 字节 UTF-8 设备名（NUL 补齐，无长度前缀）；
  3. ``send_stream_meta=true`` → u32 codec fourcc + 12 字节 session meta（flags/width/height）。
  本模块的三种模式：``raw``（全关，纯 12 字节头帧）、``meta``（device meta + 帧）、
  ``meta_dummy``（dummy + device meta + 帧），auto 依次尝试。
- 帧头 **12 字节**：``u64 = flags | pts``（见 ``PACKET_FLAG_*``）+ ``u32 payload_len``；
  config 包（SPS/PPS）不进 WS 帧流，转成 ``config`` 控制消息与 hello/sps_b64。
- 控制消息 type 17 = ``TYPE_RESET_VIDEO`` → 请求 keyframe；本模块默认不开 control socket。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import socket
import struct
import subprocess
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config
from ..errors import ApiError
from ..logs import logger

# ---------------------------------------------------------------- 常量
SCRCPY_VERSION = "5.0.1"
JAR_NAME = f"scrcpy-server-v{SCRCPY_VERSION}.jar"
LOCK_NAME = "scrcpy.lock.json"
REMOTE_JAR_PATH = "/data/local/tmp/autotest-scrcpy.jar"
OFFICIAL_JAR_SIZE = 733930  # scrcpy-server-v5.0.1 官方 release 大小（本地 jar 校验的兜底值）
SERVER_CLASS = "com.genymobile.scrcpy.Server"
SOCKET_PREFIX = "autotest_scrcpy_"

FRAME_HEADER_SIZE = 12
FRAME_HEADER = struct.Struct(">QI")  # pts_us(u64 BE) + payload_len(u32 BE)
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
SPS_PPS_TIMEOUT_S = 8.0

START_CODE_3 = b"\x00\x00\x01"
START_CODE_4 = b"\x00\x00\x00\x01"

NAL_SLICE = 1
NAL_IDR = 5
NAL_SEI = 6
NAL_SPS = 7
NAL_PPS = 8
NAL_AUD = 9

# u32 fourcc（scrcpy codec meta 里的 id）
CODEC_FOURCC: dict[int, str] = {
    int.from_bytes(b"h264", "big"): "h264",
    int.from_bytes(b"h265", "big"): "h265",
    int.from_bytes(b"av01", "big"): "av1",
}

# v5 控制消息：1 字节 type。TYPE_RESET_VIDEO=17 → 请求编码器尽快出 IDR（见 v5 jar
# control/ControlMessage.TYPE_RESET_VIDEO + Controller.resetVideo）。
# 默认 control=false；没有 control socket 时 request_keyframe 只记 debug 日志。
CONTROL_REQUEST_KEYFRAME = 17

# ---- 输入注入（v5.0.1 app/src/control_msg.h/c 逐字节核对，全部大端）----
# type=0 INJECT_KEYCODE:      [1B type][1B action][u32 keycode][u32 repeat][u32 meta]           = 14B
# type=1 INJECT_TEXT:         [1B type][u32 len][utf8...]                                       = 5+len
# type=2 INJECT_TOUCH_EVENT:  [1B type][1B action][u64 pointer_id][i32 x][i32 y]
#                             [u16 screen_w][u16 screen_h][u16 pressure][u32 action_button][u32 buttons] = 32B
#   ⚠️ server PositionMapper.map(): position.screen_size 必须**精确等于当前视频帧尺寸**，
#      否则事件被静默丢弃；x/y 是**视频帧坐标系**（server 内部再逆变换到设备像素）。
# type=3 INJECT_SCROLL_EVENT: [1B type][i32 x][i32 y][u16 w][u16 h][i16 hscroll][i16 vscroll][u32 buttons] = 21B
CONTROL_INJECT_KEYCODE = 0
CONTROL_INJECT_TEXT = 1
CONTROL_INJECT_TOUCH_EVENT = 2
CONTROL_INJECT_SCROLL_EVENT = 3

MOTION_DOWN = 0
MOTION_UP = 1
MOTION_MOVE = 2

POINTER_ID_GENERIC_FINGER = 0xFFFFFFFFFFFFFFFE  # -2，见 control_msg.h SC_POINTER_ID_GENERIC_FINGER
_INJECT_TEXT_MAX = 300  # SC_CONTROL_MSG_INJECT_TEXT_MAX_LENGTH


def build_inject_touch(
    action: int,
    x: int,
    y: int,
    video_w: int,
    video_h: int,
    pointer_id: int = POINTER_ID_GENERIC_FINGER,
    pressure: float = 1.0,
    action_button: int = 0,
    buttons: int = 0,
) -> bytes:
    buf = bytearray(32)
    buf[0] = CONTROL_INJECT_TOUCH_EVENT
    buf[1] = action & 0xFF
    buf[2:10] = int(pointer_id & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "big")
    buf[10:14] = int(x).to_bytes(4, "big", signed=True)
    buf[14:18] = int(y).to_bytes(4, "big", signed=True)
    buf[18:20] = int(max(0, min(65535, video_w))).to_bytes(2, "big")
    buf[20:22] = int(max(0, min(65535, video_h))).to_bytes(2, "big")
    buf[22:24] = int(max(0.0, min(1.0, pressure)) * 65535).to_bytes(2, "big")
    buf[24:28] = int(action_button).to_bytes(4, "big")
    buf[28:32] = int(buttons).to_bytes(4, "big")
    return bytes(buf)


def build_inject_keycode(keycode: int, action: int = MOTION_DOWN, repeat: int = 0, metastate: int = 0) -> bytes:
    buf = bytearray(14)
    buf[0] = CONTROL_INJECT_KEYCODE
    buf[1] = action & 0xFF
    buf[2:6] = int(keycode).to_bytes(4, "big")
    buf[6:10] = int(repeat).to_bytes(4, "big")
    buf[10:14] = int(metastate).to_bytes(4, "big")
    return bytes(buf)


def build_inject_text(text: str) -> list[bytes]:
    """INJECT_TEXT 单条 ≤300 字符，超长自动切片（按字符，避免拆坏 UTF-8）。"""
    chunks: list[bytes] = []
    for start in range(0, len(text), _INJECT_TEXT_MAX):
        piece = text[start : start + _INJECT_TEXT_MAX]
        data = piece.encode("utf-8")
        chunks.append(bytes([CONTROL_INJECT_TEXT]) + len(data).to_bytes(4, "big") + data)
    return chunks


def build_inject_scroll(x: int, y: int, video_w: int, video_h: int, hscroll: float, vscroll: float) -> bytes:
    buf = bytearray(21)
    buf[0] = CONTROL_INJECT_SCROLL_EVENT
    buf[1:5] = int(x).to_bytes(4, "big", signed=True)
    buf[5:9] = int(y).to_bytes(4, "big", signed=True)
    buf[9:11] = int(max(0, min(65535, video_w))).to_bytes(2, "big")
    buf[11:13] = int(max(0, min(65535, video_h))).to_bytes(2, "big")
    h = int(max(-1.0, min(1.0, hscroll)) * 32767)
    v = int(max(-1.0, min(1.0, vscroll)) * 32767)
    buf[13:15] = (h & 0xFFFF).to_bytes(2, "big")
    buf[15:17] = (v & 0xFFFF).to_bytes(2, "big")
    buf[17:21] = (0).to_bytes(4, "big")
    return bytes(buf)

# v5 12 字节帧头里的 u64 实际是 [flags 高位 + pts]（Streamer 反汇编确认）：
#   PACKET_FLAG_SESSION   = 1<<63（session meta 伪包头：u32 flags|u32 width|u32 height）
#   PACKET_FLAG_CONFIG    = 1<<62（codec config 包：payload 是 SPS/PPS，不是图像帧）
#   PACKET_FLAG_KEY_FRAME = 1<<61（关键帧）
#   pts 只有低 61 位有效
PACKET_FLAG_SESSION = 1 << 63
PACKET_FLAG_CONFIG = 1 << 62
PACKET_FLAG_KEY_FRAME = 1 << 61
PACKET_PTS_MASK = (1 << 61) - 1


# ---------------------------------------------------------------- 异常
class ScrcpyStartupError(Exception):
    """server 进程/隧道启动失败（可尝试下一种握手模式）。"""


class ScrcpyProtocolError(Exception):
    """socket 上的字节流不符合协议（可尝试下一种握手模式）。"""


class ScrcpyDisconnected(ScrcpyProtocolError):
    """对端关闭（读 frame 时）。"""


# ---------------------------------------------------------------- 路径 / 校验
def vendor_dir() -> Path:
    return config.vendor_dir()


def jar_path_override() -> Path | None:
    raw = os.environ.get("AUTOTEST_SCRCPY_JAR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return None


def default_jar_path() -> Path:
    override = jar_path_override()
    if override:
        return override
    configured = str(config.get("scrcpy.jar") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return vendor_dir() / JAR_NAME


def jar_path(cfg: ServerConfig | None = None) -> Path:
    if cfg is not None and cfg.jar_path:
        return Path(cfg.jar_path).expanduser()
    return default_jar_path()


def lock_path_for(jar: Path) -> Path:
    env = os.environ.get("AUTOTEST_SCRCPY_LOCK", "").strip()
    if env:
        return Path(env).expanduser()
    return jar.parent / LOCK_NAME


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_jar(cfg: ServerConfig | None = None) -> dict[str, Any]:
    """校验 server jar 与 lock 文件的 SHA256。

    - 缺 jar / 缺 lock / sha 不符 → ``ApiError("SCRCPY_FAILED")``；
    - 环境变量 ``AUTOTEST_SCRCPY_SKIP_VERIFY=1`` 为开发逃生口（仅本机调试用，报告里标注）。
    """
    jar = jar_path(cfg)
    if os.environ.get("AUTOTEST_SCRCPY_SKIP_VERIFY") == "1":
        return {"jar": str(jar), "sha256": "", "size": jar.stat().st_size if jar.exists() else 0,
                "version": SCRCPY_VERSION, "skipped": True}
    if not jar.exists():
        raise ApiError(
            "SCRCPY_FAILED",
            f"scrcpy-server jar 未就位: {jar}",
            {"jar": str(jar), "hint": "运行 scripts/fetch_scrcpy.py（或设置 AUTOTEST_SCRCPY_JAR）"},
        )
    lock_file = lock_path_for(jar)
    try:
        lock = json.loads(lock_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiError(
            "SCRCPY_FAILED",
            f"scrcpy lock 文件缺失或损坏: {lock_file}",
            {"lock": str(lock_file), "reason": type(exc).__name__, "hint": "重新运行 scripts/fetch_scrcpy.py --force"},
        ) from exc
    if not isinstance(lock, dict) or not lock.get("sha256"):
        raise ApiError("SCRCPY_FAILED", f"scrcpy lock 文件格式错误: {lock_file}", {"lock": str(lock_file)})
    expected = str(lock["sha256"]).lower()
    actual = sha256_file(jar)
    size = jar.stat().st_size
    expected_size = lock.get("size")
    if actual != expected or (expected_size is not None and int(expected_size) != size):
        raise ApiError(
            "SCRCPY_FAILED",
            "scrcpy-server jar 校验失败（SHA256/大小不符）",
            {
                "jar": str(jar),
                "expected_sha256": expected,
                "actual_sha256": actual,
                "expected_size": expected_size,
                "actual_size": size,
                "hint": "文件损坏或版本不匹配：运行 scripts/fetch_scrcpy.py --force",
            },
        )
    version = str(lock.get("version") or SCRCPY_VERSION)
    if version != SCRCPY_VERSION:
        logger.warning("scrcpy lock version %s != expected %s (%s)", version, SCRCPY_VERSION, jar)
    return {"jar": str(jar), "sha256": actual, "size": size, "version": version}


# ---------------------------------------------------------------- H.264 工具
def split_nals(data: bytes) -> list[bytes]:
    """Annex B 扫描：返回去掉起始码（00 00 01 / 00 00 00 01）后的 NAL 列表。"""
    if not data:
        return []
    nals: list[bytes] = []
    n = len(data)
    i = 0
    start = -1
    while i + 3 <= n:
        if data[i] == 0 and data[i + 1] == 0 and data[i + 2] == 1:
            if start >= 0:
                end = i
                while end > start and data[end - 1] == 0:  # 4 字节起始码多出的 0
                    end -= 1
                if end > start:
                    nals.append(data[start:end])
            start = i + 3
            i += 3
        else:
            i += 1
    if start >= 0:
        end = n
        while end > start and data[end - 1] == 0:
            end -= 1
        if end > start:
            nals.append(data[start:end])
    return nals


def nal_type(nal: bytes) -> int:
    if not nal:
        return -1
    return nal[0] & 0x1F


def nal_types(payload: bytes) -> list[int]:
    return [nal_type(nal) for nal in split_nals(payload)]


def is_keyframe(nal_payloads: Iterable[bytes]) -> bool:
    """含 IDR NAL（type 5）即视为关键帧（Annex B 流里 SPS/PPS 通常与 IDR 同包）。"""
    for nal in nal_payloads:
        if nal and nal_type(nal) == NAL_IDR:
            return True
    return False


def codec_string_from_sps(sps: bytes) -> str:
    """SPS → WebCodecs/avc1 兼容的 codec 字符串 ``avc1.PPCCLL``（大写十六进制）。"""
    if len(sps) < 4:
        raise ValueError(f"SPS too short: {len(sps)} bytes")
    if nal_type(sps) != NAL_SPS:
        raise ValueError(f"not an SPS NAL (type={nal_type(sps)})")
    return f"avc1.{sps[1]:02X}{sps[2]:02X}{sps[3]:02X}"


def build_avcc(sps: bytes, pps: bytes) -> bytes:
    """构造 AVCDecoderConfigurationRecord（前端 WebCodecs 兜底 description）。"""
    if nal_type(sps) != NAL_SPS:
        raise ValueError("first argument is not an SPS")
    if nal_type(pps) != NAL_PPS:
        raise ValueError("second argument is not a PPS")
    return (
        b"\x01"
        + bytes(sps[1:4])
        + b"\xff"  # lengthSizeMinusOne=3
        + b"\xe1"  # numOfSequenceParameterSets=1
        + struct.pack(">H", len(sps))
        + sps
        + b"\x01"  # numOfPictureParameterSets=1
        + struct.pack(">H", len(pps))
        + pps
    )


# ---------------------------------------------------------------- SPS 解析
_HIGH_PROFILES = frozenset(
    {100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135}
)


class _BitReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.bit = 0

    def u(self, bits: int) -> int:
        value = 0
        for _ in range(bits):
            byte_index = self.bit >> 3
            if byte_index >= len(self.data):
                raise ScrcpyProtocolError("SPS parse overflow (truncated)")
            value = (value << 1) | ((self.data[byte_index] >> (7 - (self.bit & 7))) & 1)
            self.bit += 1
        return value

    def ue(self) -> int:
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
            if zeros > 31:
                raise ScrcpyProtocolError("invalid exp-golomb code in SPS")
        return (1 << zeros) - 1 + (self.u(zeros) if zeros else 0)

    def se(self) -> int:
        value = self.ue()
        return (value + 1) // 2 if value % 2 else -(value // 2)


def _rbsp_from_nal(nal: bytes) -> bytes:
    """去掉 NAL header 字节与防竞争字节（00 00 03 → 00 00）。"""
    out = bytearray()
    zeros = 0
    for byte in nal[1:]:
        if zeros >= 2 and byte == 3:
            zeros = 0
            continue
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


def _skip_scaling_list(reader: _BitReader, size: int) -> None:
    last_scale = 8
    next_scale = 8
    for _ in range(size):
        if next_scale != 0:
            next_scale = (last_scale + reader.se() + 256) % 256
        if next_scale != 0:
            last_scale = next_scale


@dataclass(frozen=True)
class SpsInfo:
    profile_idc: int = 0
    constraint_flags: int = 0
    level_idc: int = 0
    width: int = 0
    height: int = 0
    chroma_format_idc: int = 1
    frame_cropping: tuple[int, int, int, int] = (0, 0, 0, 0)

    @property
    def codec(self) -> str:
        return f"avc1.{self.profile_idc:02X}{self.constraint_flags:02X}{self.level_idc:02X}"


def parse_sps(sps: bytes) -> SpsInfo:
    """最小 SPS 解析：profile/level/chroma/裁剪 → 编码宽高（像素）。

    只解析到 frame_cropping，够几何/旋转检测用；异常输入抛 ``ScrcpyProtocolError``。
    """
    if len(sps) < 4 or nal_type(sps) != NAL_SPS:
        raise ScrcpyProtocolError("invalid SPS NAL")
    reader = _BitReader(_rbsp_from_nal(sps))
    profile_idc = reader.u(8)
    constraint_flags = reader.u(8)
    level_idc = reader.u(8)
    reader.ue()  # seq_parameter_set_id
    chroma_format_idc = 1
    if profile_idc in _HIGH_PROFILES:
        chroma_format_idc = reader.ue()
        if chroma_format_idc == 3:
            reader.u(1)  # separate_colour_plane_flag
        reader.ue()  # bit_depth_luma_minus8
        reader.ue()  # bit_depth_chroma_minus8
        reader.u(1)  # qpprime_y_zero_transform_bypass_flag
        if reader.u(1):  # seq_scaling_matrix_present_flag
            count = 12 if chroma_format_idc == 3 else 8
            for index in range(count):
                if reader.u(1):
                    _skip_scaling_list(reader, 16 if index < 6 else 64)
    reader.ue()  # log2_max_frame_num_minus4
    pic_order_cnt_type = reader.ue()
    if pic_order_cnt_type == 0:
        reader.ue()  # log2_max_pic_order_cnt_lsb_minus4
    elif pic_order_cnt_type == 1:
        reader.u(1)  # delta_pic_order_always_zero_flag
        reader.se()  # offset_for_non_ref_pic
        reader.se()  # offset_for_top_to_bottom_field
        for _ in range(reader.ue()):
            reader.se()
    reader.ue()  # max_num_ref_frames
    reader.u(1)  # gaps_in_frame_num_value_allowed_flag
    pic_width_in_mbs_minus1 = reader.ue()
    pic_height_in_map_units_minus1 = reader.ue()
    frame_mbs_only_flag = reader.u(1)
    if not frame_mbs_only_flag:
        reader.u(1)  # mb_adaptive_frame_field_flag
    reader.u(1)  # direct_8x8_inference_flag
    crop = (0, 0, 0, 0)
    if reader.u(1):  # frame_cropping_flag
        crop = (reader.ue(), reader.ue(), reader.ue(), reader.ue())
    sub_width = 2 if chroma_format_idc in (1, 2) else 1
    sub_height = 2 if chroma_format_idc == 1 else 1
    width = (pic_width_in_mbs_minus1 + 1) * 16 - (crop[0] + crop[1]) * sub_width
    height = (2 - frame_mbs_only_flag) * (pic_height_in_map_units_minus1 + 1) * 16 - (
        crop[2] + crop[3]
    ) * sub_height
    return SpsInfo(
        profile_idc=profile_idc,
        constraint_flags=constraint_flags,
        level_idc=level_idc,
        width=width,
        height=height,
        chroma_format_idc=chroma_format_idc,
        frame_cropping=crop,
    )


# ---------------------------------------------------------------- 帧头 / geometry
@dataclass(frozen=True)
class VideoGeometry:
    width: int = 0
    height: int = 0
    rotation: int = 0
    codec: str = ""
    sps: bytes | None = None
    pps: bytes | None = None

    def to_dict(self, *, include_sps_b64: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
            "codec": self.codec,
        }
        if include_sps_b64:
            data["sps_b64"] = base64.b64encode(self.sps).decode("ascii") if self.sps else ""
            data["pps_b64"] = base64.b64encode(self.pps).decode("ascii") if self.pps else ""
        return data


@dataclass(frozen=True)
class FrameInfo:
    pts_us: int
    size: int
    keyframe: bool = False
    config: bool = False  # v5 codec config 包（payload 是 SPS/PPS，不是图像帧）
    session_meta: bool = False  # v5 session meta 伪包头（旋转/尺寸变化时重发）
    nals: tuple[int, ...] = ()
    sps_changed: bool = False
    pps_changed: bool = False
    geometry: VideoGeometry = field(default_factory=VideoGeometry)


def pack_frame(pts_us: int, payload: bytes) -> bytes:
    """按契约 §6.2 的 12 字节头打包（对外 WS 帧：pts 不带 v5 内部 flag）。"""
    return FRAME_HEADER.pack(int(pts_us) & PACKET_PTS_MASK, len(payload)) + payload


def unpack_frame_header(header: bytes) -> tuple[int, int]:
    """解析 12 字节头 → (掩掉 v5 flag 的 pts_us, payload_len)。"""
    if len(header) != FRAME_HEADER_SIZE:
        raise ScrcpyProtocolError(f"frame header must be {FRAME_HEADER_SIZE} bytes, got {len(header)}")
    raw_pts, size = FRAME_HEADER.unpack(header)
    if size > MAX_PAYLOAD_BYTES:
        raise ScrcpyProtocolError(f"frame payload length out of range: {size}")
    return raw_pts & PACKET_PTS_MASK, size


def decode_frame_header(header: bytes) -> tuple[int, int, bool, bool]:
    """解析 v5 12 字节头 → ``(pts_us, size, is_config, is_keyframe)``（session 包也按此返回）。"""
    if len(header) != FRAME_HEADER_SIZE:
        raise ScrcpyProtocolError(f"frame header must be {FRAME_HEADER_SIZE} bytes, got {len(header)}")
    raw_pts, size = FRAME_HEADER.unpack(header)
    if not (raw_pts & PACKET_FLAG_SESSION) and size > MAX_PAYLOAD_BYTES:
        raise ScrcpyProtocolError(f"frame payload length out of range: {size}")
    return (
        raw_pts & PACKET_PTS_MASK,
        size,
        bool(raw_pts & PACKET_FLAG_CONFIG),
        bool(raw_pts & PACKET_FLAG_KEY_FRAME),
    )


def decode_session_meta(header: bytes) -> tuple[int, int, bool]:
    """解析 session meta 伪包头 → ``(width, height, locked)``。

    v5 ``Streamer.writeSessionMeta`` 写 12 字节：``u32 flags | u32 width | u32 height``，
    其中 flags 的高位 = PACKET_FLAG_SESSION，bit0 = 客户端 resize 锁定标志。
    """
    raw_pts, height = FRAME_HEADER.unpack(header)
    if not raw_pts & PACKET_FLAG_SESSION:
        raise ScrcpyProtocolError("not a session meta packet")
    flags_hi = (raw_pts >> 32) & 0xFFFFFFFF
    width = raw_pts & 0xFFFFFFFF
    return width, height, bool(flags_hi & 0x1)


def _looks_like_annexb(payload: bytes) -> bool:
    """首帧 payload 自检：必须能在前 8 字节内找到 Annex B 起始码且 NAL 类型合法。"""
    head = payload[:8]
    if b"\x00\x00\x01" not in head:
        return False
    nals = split_nals(payload)
    if not nals:
        return False
    return nal_type(nals[0]) in (NAL_SLICE, NAL_IDR, NAL_SEI, NAL_SPS, NAL_PPS, NAL_AUD)


# ---------------------------------------------------------------- stream 头（v5 可选握手）
DEVICE_NAME_FIELD_LENGTH = 64  # v5 DesktopConnection.DEVICE_NAME_FIELD_LENGTH


@dataclass(frozen=True)
class StreamHeaderSpec:
    """v5 握手顺序：dummy 字节 → 64B 设备名 → u32 codec id（session meta 走帧头循环）。"""

    device_meta: bool = True
    dummy_byte: bool = True
    stream_meta: bool = True  # 4 字节 codec id；随后第一个 12B "包头" 是 session meta


STREAM_MODE_SPECS: dict[str, StreamHeaderSpec | None] = {
    # 官方默认（全开）：dummy + 设备名 + codec id + session meta + 帧
    "full": StreamHeaderSpec(device_meta=True, dummy_byte=True, stream_meta=True),
    # 只关 dummy
    "meta": StreamHeaderSpec(device_meta=True, dummy_byte=False, stream_meta=True),
    # 全关：纯 12 字节头帧（无 dummy/name/codec/session meta）
    "raw": None,
}


@dataclass(frozen=True)
class StreamHeader:
    device_name: str = ""
    dummy_byte: bool = False
    codec_id: int = 0
    codec: str = ""

    def to_geometry(self) -> VideoGeometry:
        return VideoGeometry(codec=self.codec)


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        try:
            chunk = sock.recv(remaining)
        except TimeoutError as exc:
            raise TimeoutError(f"timed out reading {count} bytes from scrcpy socket") from exc
        if not chunk:
            raise ScrcpyDisconnected("scrcpy socket closed by peer")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_stream_headers(sock: socket.socket, spec: StreamHeaderSpec) -> StreamHeader:
    """读取 v5 stream 头：dummy 字节 → 64 字节设备名 → u32 codec id。

    紧随其后的 12 字节是 session meta 伪包头（PACKET_FLAG_SESSION），由 ``read_frame`` 消费。
    """
    dummy = False
    if spec.dummy_byte:
        _recv_exact(sock, 1)
        dummy = True
    device_name = ""
    if spec.device_meta:
        raw_name = _recv_exact(sock, DEVICE_NAME_FIELD_LENGTH)
        device_name = raw_name.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    codec_id = 0
    codec = ""
    if spec.stream_meta:
        (codec_id,) = struct.unpack(">I", _recv_exact(sock, 4))
        codec = CODEC_FOURCC.get(codec_id, "")
    return StreamHeader(device_name=device_name, dummy_byte=dummy, codec_id=codec_id, codec=codec)


def _first_nal_type(payload: bytes) -> int:
    """只看起始码后的第一个 NAL header（不做全量扫描），用于判断是否值得扫参数集。"""
    if payload.startswith(START_CODE_4):
        return payload[4] & 0x1F if len(payload) > 4 else -1
    if payload.startswith(START_CODE_3):
        return payload[3] & 0x1F if len(payload) > 3 else -1
    return payload[0] & 0x1F if payload else -1


class VideoFrameReader:
    """从 scrcpy video socket 读帧（12 字节头 + Annex B payload）。

    - ``read_frame() -> (pts_us, payload)``；短读自动重试；对端关闭抛 ``ScrcpyDisconnected``；
    - v5 的 session meta 伪包头（PACKET_FLAG_SESSION）不出 payload：返回 ``(0, b"")``，
      并通过 ``last_info.session_meta=True`` + ``geometry.width/height`` 暴露新尺寸/旋转；
    - v5 的 codec config 包（PACKET_FLAG_CONFIG，SPS/PPS）返回 ``(0, payload)`` 且
      ``last_info.config=True``；``codec_config`` 缓存最近一次 config payload 供上层 in-band 拼接；
    - 自动缓存 SPS/PPS 并维护 ``geometry``（尺寸优先取 session meta，codec 串取 SPS）；
    - ``last_info`` 暴露本帧 NAL 类型、关键帧、SPS/PPS/尺寸变化。
    """

    def __init__(self, sock: socket.socket, *, timeout: float | None = None) -> None:
        self.sock = sock
        self.timeout = timeout
        self.geometry = VideoGeometry()
        self.last_info: FrameInfo | None = None
        self.codec_config: bytes = b""  # 最近一次 CONFIG 包（SPS/PPS，Annex B）
        self.session_size: tuple[int, int] | None = None
        self._sps: bytes = b""
        self._pps: bytes = b""
        self._session_locked = False
        self._open = True
        if timeout is not None:
            try:
                self.sock.settimeout(timeout)
            except OSError:  # pragma: no cover - 已关闭的 fake socket
                pass

    # ---- 基础读取
    def _read_exact(self, count: int) -> bytes:
        return _recv_exact(self.sock, count)

    def read_stream_headers(self, spec: StreamHeaderSpec | None = None) -> StreamHeader:
        header = read_stream_headers(self.sock, spec or StreamHeaderSpec())
        geometry = header.to_geometry()
        if geometry.width and geometry.height:
            self.geometry = geometry
        return header

    # ---- 帧
    def read_frame(self) -> tuple[int, bytes]:
        header = self._read_exact(FRAME_HEADER_SIZE)
        raw_pts, size = FRAME_HEADER.unpack(header)
        if raw_pts & PACKET_FLAG_SESSION:
            info = self._update_session_meta(header)
            self.last_info = info
            return (0, b"")
        if size > MAX_PAYLOAD_BYTES:
            raise ScrcpyProtocolError(f"frame payload length out of range: {size}")
        pts_us = raw_pts & PACKET_PTS_MASK
        is_config = bool(raw_pts & PACKET_FLAG_CONFIG)
        is_key = bool(raw_pts & PACKET_FLAG_KEY_FRAME)
        payload = self._read_exact(size) if size else b""
        info = self._update_info(pts_us, payload, config=is_config, keyframe_flag=is_key)
        self.last_info = info
        return pts_us, payload

    def _update_session_meta(self, header: bytes) -> FrameInfo:
        width, height, locked = decode_session_meta(header)
        if not (0 < width <= 16384 and 0 < height <= 16384):
            raise ScrcpyProtocolError(f"implausible session meta size: {width}x{height}")
        self.session_size = (width, height)
        self._session_locked = locked
        self.geometry = VideoGeometry(
            width=width,
            height=height,
            rotation=self.geometry.rotation,
            codec=self.geometry.codec,
            sps=self._sps or None,
            pps=self._pps or None,
        )
        logger.info("scrcpy session meta: %dx%d locked=%s", width, height, locked)
        return FrameInfo(
            pts_us=0,
            size=0,
            session_meta=True,
            geometry=self.geometry,
        )

    def _update_info(
        self,
        pts_us: int,
        payload: bytes,
        *,
        config: bool = False,
        keyframe_flag: bool = False,
    ) -> FrameInfo:
        # 性能：普通 P 帧不扫 NAL。v5 帧头自带关键帧 flag，SPS/PPS 走独立 config 包；
        # 只有 config / 关键帧 / 尚未拿到 SPS / 首 NAL 是 SPS/PPS/IDR 时才扫（兼容 in-band 参数集）。
        need_scan = (
            config
            or keyframe_flag
            or not self._sps
            or _first_nal_type(payload) in (NAL_SPS, NAL_PPS, NAL_IDR)
        )
        nals = split_nals(payload) if need_scan else []
        sps_changed = False
        pps_changed = False
        types: list[int] = []
        for nal in nals:
            kind = nal_type(nal)
            types.append(kind)
            if kind == NAL_SPS:
                if nal != self._sps:
                    self._sps = nal
                    sps_changed = True
                if sps_changed or not self.geometry.codec:
                    try:
                        info = parse_sps(nal)
                    except ScrcpyProtocolError as exc:
                        logger.warning("failed to parse SPS: %s", exc)
                        continue
                    if self.session_size is not None:
                        width, height = self.session_size
                    else:
                        width, height = info.width, info.height
                    self.geometry = VideoGeometry(
                        width=width,
                        height=height,
                        rotation=self.geometry.rotation,
                        codec=info.codec,
                        sps=nal,
                        pps=self._pps or None,
                    )
            elif kind == NAL_PPS:
                if nal != self._pps:
                    self._pps = nal
                    pps_changed = True
                    if self._sps:
                        self.geometry = VideoGeometry(
                            width=self.geometry.width,
                            height=self.geometry.height,
                            rotation=self.geometry.rotation,
                            codec=self.geometry.codec,
                            sps=self._sps,
                            pps=nal,
                        )
        if config and payload:
            self.codec_config = payload
        keyframe = keyframe_flag or (need_scan and NAL_IDR in types)
        return FrameInfo(
            pts_us=pts_us,
            size=len(payload),
            keyframe=keyframe,
            config=config,
            nals=tuple(types),
            sps_changed=sps_changed,
            pps_changed=pps_changed,
            geometry=self.geometry,
        )

    def close(self) -> None:
        self._open = False
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------- ServerConfig
def default_socket_name() -> str:
    """本进程逻辑 session 名（日志/registry 用；真机 localabstract 由 scid 派生）。"""
    return f"{SOCKET_PREFIX}{os.getpid()}_{secrets.token_hex(4)}"


def default_scid() -> int:
    """v5 ``scid``：随机正 int32（server 用 Integer.parseInt(...,16)，需 ≤0x7FFFFFFF）。

    localabstract 名 = ``scrcpy_<scid 的 8 位小写 hex>``。
    """
    return secrets.randbelow(0x7FFFFFFF) + 1


_CROP_RE = re.compile(r"^\d+:\d+:\d+:\d+$")
_SOCKET_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


@dataclass
class ServerConfig:
    """scrcpy server 参数（字段名与设置项白名单对齐，契约 §10）。"""

    jar_path: Path | None = None
    max_size: int = 1600
    max_fps: int = 30
    video_bit_rate: int = 8_000_000
    crop: str = ""
    codec: str = "h264"
    socket_name: str = field(default_factory=default_socket_name)  # 逻辑名（日志/registry）
    scid: int = field(default_factory=default_scid)  # v5 真机 socket：scrcpy_<%08x>
    tunnel_forward: bool = True
    control: bool = True  # v5：control=true 时 server 会 accept 第二个 socket，必须连接
    log_level: str = "info"
    stream_mode: str = "auto"  # auto|full|meta|raw
    connect_timeout_s: float = 10.0
    handshake_timeout_s: float = 3.0
    first_frame_timeout_s: float = 8.0
    extra_args: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.max_size and not (0 < int(self.max_size) <= 4096):
            raise ApiError("INVALID_ARGUMENT", f"scrcpy.max_size 非法: {self.max_size}")
        if not (1 <= int(self.max_fps) <= 240):
            raise ApiError("INVALID_ARGUMENT", f"scrcpy.max_fps 非法: {self.max_fps}")
        if int(self.video_bit_rate) <= 0:
            raise ApiError("INVALID_ARGUMENT", f"scrcpy.video_bit_rate 非法: {self.video_bit_rate}")
        if self.crop and not _CROP_RE.match(str(self.crop)):
            raise ApiError("INVALID_ARGUMENT", f"scrcpy.crop 需为 w:h:x:y，收到: {self.crop}")
        if self.codec not in ("h264",):
            raise ApiError("UNSUPPORTED", f"仅支持 h264 镜像（收到 {self.codec}）")
        if not _SOCKET_RE.match(self.socket_name):
            raise ApiError("INVALID_ARGUMENT", f"非法 socket 名: {self.socket_name}")
        if not (-1 <= int(self.scid) <= 0x7FFFFFFF):
            raise ApiError("INVALID_ARGUMENT", f"非法 scid（-1 或 0..0x7FFFFFFF）: {self.scid}")
        if self.stream_mode not in ("auto", "full", "meta", "raw"):
            raise ApiError("INVALID_ARGUMENT", f"非法 stream_mode: {self.stream_mode}")

    @classmethod
    def from_settings(cls, jar: Path | None = None) -> ServerConfig:
        return cls(
            jar_path=jar if jar is not None else None,
            max_size=int(config.get("scrcpy.max_size") or 1600),
            max_fps=int(config.get("scrcpy.max_fps") or 30),
            video_bit_rate=int(config.get("scrcpy.video_bit_rate") or 8_000_000),
            crop=str(config.get("scrcpy.crop") or ""),
        )

    @property
    def device_socket_name(self) -> str:
        """v5 server 实际监听的 localabstract 名。"""
        if int(self.scid) == -1:
            return "scrcpy"
        return f"scrcpy_{int(self.scid) & 0x7FFFFFFF:08x}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_size": self.max_size,
            "max_fps": self.max_fps,
            "video_bit_rate": self.video_bit_rate,
            "crop": self.crop,
            "codec": self.codec,
            "socket_name": self.socket_name,
            "device_socket_name": self.device_socket_name,
            "scid": self.scid,
            "tunnel_forward": self.tunnel_forward,
            "control": self.control,
            "stream_mode": self.stream_mode,
        }


def build_server_args(cfg: ServerConfig, mode: str) -> list[str]:
    """构造 v5 server 参数列表（第一个是版本号，其余 ``key=value``，已按 jar 反汇编核对）。"""
    args: list[str] = [
        SCRCPY_VERSION,
        f"log_level={cfg.log_level}",
        "audio=false",  # v5 的 --no-audio 等价物
        "video=true",
        f"video_codec={cfg.codec}",
        f"max_size={int(cfg.max_size)}",
        f"max_fps={int(cfg.max_fps)}",
        f"video_bit_rate={int(cfg.video_bit_rate)}",
        f"tunnel_forward={'true' if cfg.tunnel_forward else 'false'}",
        f"control={'true' if cfg.control else 'false'}",
        f"scid={int(cfg.scid) & 0x7FFFFFFF:08x}",  # v5 按 hex 解析 scid（int32 范围）
        "send_frame_meta=true",  # 契约 §6.2 的 12 字节头
    ]
    if cfg.crop:
        args.append(f"crop={cfg.crop}")
    if mode == "raw":
        # 只发 12 字节头 + Annex B：关掉 dummy/device meta/codec+session meta
        args.extend(["send_device_meta=false", "send_dummy_byte=false", "send_stream_meta=false"])
    elif mode == "meta":
        args.extend(["send_device_meta=true", "send_dummy_byte=false", "send_stream_meta=true"])
    else:  # full（v5 官方默认）
        args.extend(["send_device_meta=true", "send_dummy_byte=true", "send_stream_meta=true"])
    for key, value in cfg.extra_args.items():
        args.append(f"{key}={value}")
    return args


def build_server_command(cfg: ServerConfig, mode: str = "raw") -> str:
    options = " ".join(build_server_args(cfg, mode))
    return f"CLASSPATH={REMOTE_JAR_PATH} app_process / {SERVER_CLASS} {options}"


# ---------------------------------------------------------------- adb 封装
def _resolve_adb_module() -> Any:
    """优先用 be-driver 的 app.android.adb（契约 §14.1）；尚未就绪时返回 None。"""
    try:
        from . import adb as adb_module  # noqa: PLC0415 - 延迟导入，避免队友文件未就绪时崩溃
    except Exception:  # noqa: BLE001
        return None
    required = ("push_file", "forward", "remove_forward", "list_devices")
    if all(hasattr(adb_module, name) for name in required):
        return adb_module
    return None


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class AdbOps:
    """本模块需要的 adb 能力；有 be-driver 实现时优先调用，否则内部 subprocess 兜底。"""

    def __init__(self, serial: str, *, adb_module: Any = None, adb_binary: str | None = None) -> None:
        self.serial = serial
        self._module = adb_module if adb_module is not None else _resolve_adb_module()
        self._binary = adb_binary or config.find_adb()

    # ---- 内部
    def _run(self, args: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
        command = [self._binary, "-s", self.serial, *args]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError as exc:
            raise ApiError(
                "DEVICE_UNAVAILABLE",
                f"adb 不可用（未找到 {self._binary}）",
                {"serial": self.serial, "adb": self._binary},
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ApiError(
                "DEVICE_UNAVAILABLE",
                f"adb 命令超时: {' '.join(args)}",
                {"serial": self.serial, "timeout_s": timeout},
            ) from exc
        return result

    # ---- 契约 §14.1 能力（模块优先，失败兜底）
    def list_devices(self) -> list[dict[str, Any]]:
        if self._module is not None:
            try:
                return [dict(item) for item in self._module.list_devices()]
            except Exception as exc:  # noqa: BLE001 - be-driver 实现异常时走兜底
                logger.warning("app.android.adb.list_devices failed (%s), falling back to subprocess", exc)
        result = self._run(["devices"], timeout=10)
        if result.returncode != 0:
            raise ApiError(
                "DEVICE_UNAVAILABLE",
                f"adb devices 失败: {result.stderr.strip() or result.stdout.strip()}",
                {"serial": self.serial},
            )
        devices: list[dict[str, Any]] = []
        for line in result.stdout.splitlines()[1:]:
            line = line.strip()
            if not line or "\t" not in line:
                continue
            serial, _, state = line.partition("\t")
            devices.append({"serial": serial.strip(), "state": state.strip()})
        return devices

    def push(self, local: Path, remote: str) -> None:
        if self._module is not None:
            try:
                self._module.push_file(self.serial, str(local), remote)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("adb.push_file failed (%s), falling back to subprocess", exc)
        result = self._run(["push", str(local), remote], timeout=120)
        if result.returncode != 0:
            raise ApiError(
                "SCRCPY_FAILED",
                f"adb push 失败: {result.stderr.strip() or result.stdout.strip()}",
                {"serial": self.serial, "local": str(local), "remote": remote},
            )

    def forward(self, local: str, remote: str) -> None:
        if self._module is not None:
            try:
                self._module.forward(self.serial, local, remote)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("adb.forward failed (%s), falling back to subprocess", exc)
        result = self._run(["forward", local, remote], timeout=15)
        if result.returncode != 0:
            raise ApiError(
                "SCRCPY_FAILED",
                f"adb forward 失败: {result.stderr.strip() or result.stdout.strip()}",
                {"serial": self.serial, "local": local, "remote": remote},
            )

    def reverse(self, remote: str, local: str) -> None:
        if self._module is not None and hasattr(self._module, "reverse"):
            try:
                self._module.reverse(self.serial, remote, local)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("adb.reverse failed (%s), falling back to subprocess", exc)
        result = self._run(["reverse", remote, local], timeout=15)
        if result.returncode != 0:
            raise ApiError(
                "SCRCPY_FAILED",
                f"adb reverse 失败: {result.stderr.strip() or result.stdout.strip()}",
                {"serial": self.serial, "remote": remote, "local": local},
            )

    def remove_forward(self, local: str) -> None:
        if self._module is not None:
            try:
                self._module.remove_forward(self.serial, local)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("adb.remove_forward failed (%s), falling back to subprocess", exc)
        try:
            self._run(["forward", "--remove", local], timeout=10)
        except ApiError as exc:
            logger.warning("remove forward %s failed: %s", local, exc.message)

    def remove_reverse(self, remote: str) -> None:
        try:
            self._run(["reverse", "--remove", remote], timeout=10)
        except ApiError as exc:
            logger.warning("remove reverse %s failed: %s", remote, exc.message)

    def shell_text(self, command: str, timeout: float = 10.0) -> str:
        if self._module is not None and hasattr(self._module, "shell_bytes"):
            try:
                raw = self._module.shell_bytes(self.serial, command)
                return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning("adb.shell_bytes failed (%s), falling back to subprocess", exc)
        result = self._run(["shell", command], timeout=timeout)
        return (result.stdout or "") + (result.stderr or "")

    def start_process(self, command: str) -> subprocess.Popen[str]:
        argv = [self._binary, "-s", self.serial, "shell", command]
        try:
            return subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise ApiError(
                "DEVICE_UNAVAILABLE",
                f"adb 不可用（未找到 {self._binary}）",
                {"serial": self.serial, "adb": self._binary},
            ) from exc


def ensure_device_online(serial: str, ops: AdbOps | None = None) -> dict[str, Any]:
    """无固定在线设备 → ``DEVICE_UNAVAILABLE``（由 mirror start 先调用，错误更可读）。"""
    ops = ops or AdbOps(serial)
    devices = ops.list_devices()
    online = [item for item in devices if str(item.get("state", "")).lower() == "device"]
    match = next((item for item in devices if item.get("serial") == serial), None)
    if match is None:
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            f"设备 {serial} 不在线（在线设备：{[d.get('serial') for d in online] or '无'}）",
            {"serial": serial, "devices": devices},
        )
    state = str(match.get("state", "")).lower()
    if state != "device":
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            f"设备 {serial} 状态不可用: {state}",
            {"serial": serial, "state": state},
        )
    return match


# ---------------------------------------------------------------- server 生命周期
@dataclass
class ScrcpyServer:
    """已启动的 scrcpy server + 已连上的 socket（video/control）+ 首帧。"""

    serial: str
    cfg: ServerConfig
    ops: AdbOps
    process: Any
    video_sock: socket.socket
    control_sock: socket.socket | None
    reader: VideoFrameReader
    first_frame: tuple[int, bytes]
    local_port: int
    stream_mode: str = "raw"
    log_lines: list[str] = field(default_factory=list)  # 设备端 server 输出（排障用）
    started_at: float = field(default_factory=time.monotonic)
    _stopped: bool = False
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def server_log_tail(self, limit: int = 40) -> str:
        with self._lock:
            lines = list(self.log_lines)
        return "\n".join(lines[-limit:])

    @property
    def geometry(self) -> VideoGeometry:
        return self.reader.geometry

    @property
    def forward_local(self) -> str:
        return f"tcp:{self.local_port}"

    @property
    def remote_socket(self) -> str:
        return f"localabstract:{self.cfg.device_socket_name}"

    def process_alive(self) -> bool:
        try:
            return self.process.poll() is None
        except Exception:  # noqa: BLE001 - fake process
            return True

    def send_control(self, payload: bytes) -> bool:
        """向 control socket 写一条控制消息（线程安全；失败返回 False 不抛）。"""
        if self.control_sock is None:
            return False
        with self._lock:
            try:
                self.control_sock.sendall(payload)
                return True
            except OSError as exc:
                logger.warning("scrcpy control send failed (%s): %s", self.serial, exc)
                return False

    def video_size(self) -> tuple[int, int]:
        geometry = self.geometry
        return int(geometry.width or 0), int(geometry.height or 0)

    def inject_touch(self, action: int, x: int, y: int, video_w: int, video_h: int) -> bool:
        """流式触摸：单条 INJECT_TOUCH_EVENT（down/move/up），调用方负责时序。"""
        if video_w <= 0 or video_h <= 0:
            return False
        return self.send_control(build_inject_touch(action, x, y, video_w, video_h))

    def inject_tap(self, x: int, y: int, video_w: int | None = None, video_h: int | None = None) -> bool:
        w, h = (int(video_w), int(video_h)) if video_w and video_h else self.video_size()
        if w <= 0 or h <= 0:
            return False
        if not self.send_control(build_inject_touch(MOTION_DOWN, x, y, w, h)):
            return False
        return self.send_control(build_inject_touch(MOTION_UP, x, y, w, h))

    def inject_long_press(
        self, x: int, y: int, duration_ms: int = 800, video_w: int | None = None, video_h: int | None = None
    ) -> bool:
        w, h = (int(video_w), int(video_h)) if video_w and video_h else self.video_size()
        if w <= 0 or h <= 0:
            return False
        if not self.send_control(build_inject_touch(MOTION_DOWN, x, y, w, h)):
            return False
        time.sleep(max(0.1, min(10.0, duration_ms / 1000.0)))
        return self.send_control(build_inject_touch(MOTION_UP, x, y, w, h))

    def inject_swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
        video_w: int | None = None,
        video_h: int | None = None,
    ) -> bool:
        """DOWN → 若干 MOVE → UP，按 duration 分配移动间隔（scrcpy 原生滑动同款）。"""
        w, h = (int(video_w), int(video_h)) if video_w and video_h else self.video_size()
        if w <= 0 or h <= 0:
            return False
        duration_s = max(0.05, min(10.0, duration_ms / 1000.0))
        steps = max(2, min(30, int(duration_s * 60)))
        if not self.send_control(build_inject_touch(MOTION_DOWN, x1, y1, w, h)):
            return False
        step_sleep = duration_s / steps
        for index in range(1, steps):
            ratio = index / steps
            mx = int(round(x1 + (x2 - x1) * ratio))
            my = int(round(y1 + (y2 - y1) * ratio))
            time.sleep(step_sleep)
            if not self.send_control(build_inject_touch(MOTION_MOVE, mx, my, w, h)):
                self.send_control(build_inject_touch(MOTION_UP, mx, my, w, h))
                return False
        time.sleep(step_sleep)
        return self.send_control(build_inject_touch(MOTION_UP, x2, y2, w, h))

    def inject_key(self, keycode: int) -> bool:
        if not self.send_control(build_inject_keycode(int(keycode), MOTION_DOWN)):
            return False
        time.sleep(0.008)
        return self.send_control(build_inject_keycode(int(keycode), MOTION_UP))

    def inject_text(self, text: str) -> bool:
        if not text:
            return True
        for chunk in build_inject_text(text):
            if not self.send_control(chunk):
                return False
        return True

    def request_keyframe(self) -> bool:
        """请编码器尽快出 IDR；无 control socket 时返回 False（客户端等自然 IDR）。"""
        if self.control_sock is None:
            logger.debug("request_keyframe ignored: control socket disabled")
            return False
        try:
            self.control_sock.sendall(bytes([CONTROL_REQUEST_KEYFRAME]))
            return True
        except OSError as exc:
            logger.warning("request_keyframe send failed: %s", exc)
            return False

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            logger.info("stopping scrcpy server for %s (socket=%s)", self.serial, self.cfg.socket_name)
            try:
                self.video_sock.close()
            except OSError:
                pass
            if self.control_sock is not None:
                try:
                    self.control_sock.close()
                except OSError:
                    pass
            _terminate_process(self.process)
            _kill_remote_server(self.ops, self.cfg)
            if self.cfg.tunnel_forward:
                self.ops.remove_forward(self.forward_local)
            else:
                self.ops.remove_reverse(self.remote_socket)
            _unregister_server(self)


_ACTIVE_SERVERS: dict[str, ScrcpyServer] = {}
_SERVERS_LOCK = threading.RLock()


def _register_server(server: ScrcpyServer) -> None:
    with _SERVERS_LOCK:
        _ACTIVE_SERVERS[server.cfg.socket_name] = server


def _unregister_server(server: ScrcpyServer) -> None:
    with _SERVERS_LOCK:
        _ACTIVE_SERVERS.pop(server.cfg.socket_name, None)


def active_servers() -> list[ScrcpyServer]:
    with _SERVERS_LOCK:
        return list(_ACTIVE_SERVERS.values())


def _terminate_process(process: Any) -> None:
    if process is None:
        return
    try:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=1.5)
        except Exception:  # noqa: BLE001 - 超时/无 wait 的 fake
            process.kill()
            try:
                process.wait(timeout=1.0)
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("terminate scrcpy process failed: %s", exc)


def _attach_process_log(process: Any, limit: int = 500) -> list[str]:
    """持续消费 adb shell 的 stdout/stderr（否则 PIPE 写满会阻塞设备端），保留最后 N 行。"""
    lines: list[str] = []
    lock = threading.Lock()

    def pump(stream: Any) -> None:
        try:
            for raw in stream:
                line = str(raw).rstrip()
                with lock:
                    lines.append(line)
                    if len(lines) > limit:
                        del lines[: len(lines) - limit]
        except Exception as exc:  # noqa: BLE001 - 进程结束/管道关闭
            logger.debug("scrcpy process log pump ended: %s", exc)

    for stream in (getattr(process, "stdout", None), getattr(process, "stderr", None)):
        if stream is not None:
            threading.Thread(target=pump, args=(stream,), name="scrcpy-log", daemon=True).start()
    text = str(getattr(process, "stderr_text", "") or "")
    if text:
        lines.append(text)
    return lines


def _drain_stderr(process: Any, limit: int = 800, log_lines: list[str] | None = None) -> str:
    """返回设备端 server 输出尾部（优先用 pump 采集的日志，避免和读线程抢 PIPE）。"""
    if log_lines:
        return "\n".join(log_lines[-limit // 80 :])[-limit:]
    text = str(getattr(process, "stderr_text", "") or "")
    if text:
        return text[-limit:]
    stderr = getattr(process, "stderr", None)
    if stderr is None:
        return ""
    try:
        data = stderr.read()
    except Exception:  # noqa: BLE001
        return ""
    return str(data or "")[-limit:]


def _process_failure(process: Any, context: str, log_lines: list[str] | None = None) -> ScrcpyStartupError:
    code = getattr(process, "returncode", None)
    if code is None:
        try:
            code = process.poll()
        except Exception:  # noqa: BLE001
            code = None
    stderr = _drain_stderr(process, log_lines=log_lines)
    message = f"{context}: scrcpy server exited (code={code})"
    if stderr:
        message += f": {stderr.strip()}"
    return ScrcpyStartupError(message)


def _kill_remote_server(ops: AdbOps, cfg: ServerConfig) -> None:
    """按本 session 的 scid 精确 kill 设备端 server（防 adb shell 断开后孤儿残留）。"""
    scid_tag = f"scid={int(cfg.scid) & 0x7FFFFFFF:08x}"
    try:
        output = ops.shell_text("ps -A -o PID,ARGS", timeout=6.0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("list device processes failed: %s", exc)
        return
    for line in (output or "").splitlines():
        if "com.genymobile.scrcpy.Server" not in line or scid_tag not in line:
            continue
        pid = line.split()[0] if line.split() else ""
        if pid.isdigit():
            try:
                ops.shell_text(f"kill -9 {pid}", timeout=5.0)
                logger.info("killed orphan scrcpy server on device: pid=%s (%s)", pid, scid_tag)
            except Exception as exc:  # noqa: BLE001
                logger.debug("kill remote scrcpy server %s failed: %s", pid, exc)


def _capture_crash_log(ops: AdbOps, limit: int = 2000) -> str:
    """启动失败时尽量抓一段设备端 crash log（best effort，不抛异常）。"""
    try:
        output = ops.shell_text("logcat -b crash -d -t 20", timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        return f"(logcat unavailable: {exc})"
    return (output or "").strip()[-limit:]


def _remote_jar_size(ops: AdbOps) -> int | None:
    """``ls -l`` 拿设备上 jar 大小；不存在/失败返回 None。"""
    try:
        output = ops.shell_text(f"ls -l {REMOTE_JAR_PATH}", timeout=8.0)
    except Exception:  # noqa: BLE001
        return None
    if not output or "No such file" in output or "No such file or directory" in output:
        return None
    parts = output.split()
    # 标准 ls -l：-rw-r--r-- 1 shell shell 733930 2026-11-20 08:26 /path
    if len(parts) >= 5 and parts[0][:1] in ("-", "d", "l"):
        try:
            return int(parts[4])
        except ValueError:
            pass
    for token in reversed(parts):
        if token.isdigit() and len(token) >= 4:
            return int(token)
    return None


def ensure_remote_jar(ops: AdbOps, cfg: ServerConfig) -> dict[str, Any]:
    """每次 start 前确认设备端 jar 存在且大小一致；不符/缺失则 push（失败不删远端 jar）。"""
    local = jar_path(cfg)
    local_size = local.stat().st_size if local.exists() else OFFICIAL_JAR_SIZE
    remote_size = _remote_jar_size(ops)
    pushed = False
    if remote_size != local_size:
        logger.info(
            "remote scrcpy jar missing/size mismatch (remote=%s local=%s), pushing %s",
            remote_size,
            local_size,
            local,
        )
        ops.push(local, REMOTE_JAR_PATH)
        pushed = True
        remote_size = _remote_jar_size(ops)
        if remote_size != local_size:
            raise ScrcpyStartupError(
                f"push 后远端 jar 大小仍不符: remote={remote_size} local={local_size} path={REMOTE_JAR_PATH}"
            )
    return {"remote_jar": REMOTE_JAR_PATH, "size": local_size, "pushed": pushed}


def _connect_tcp(
    port: int,
    deadline: float,
    process: Any,
    log_lines: list[str] | None = None,
    context: str = "connect video socket",
) -> socket.socket:
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if process.poll() is not None:
                raise _process_failure(process, context, log_lines)
        except ScrcpyStartupError:
            raise
        except Exception:  # noqa: BLE001
            pass
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return sock
        except OSError as exc:
            last_error = exc
            time.sleep(0.05)
    raise ScrcpyStartupError(f"connect tcp:{port} timeout: {last_error}")


def _accept_tcp(
    listener: socket.socket,
    deadline: float,
    process: Any,
    context: str,
    log_lines: list[str] | None = None,
) -> socket.socket:
    listener.settimeout(0.2)
    while time.monotonic() < deadline:
        try:
            if process is not None and process.poll() is not None:
                raise _process_failure(process, context, log_lines)
        except ScrcpyStartupError:
            raise
        except Exception:  # noqa: BLE001
            pass
        try:
            client, _addr = listener.accept()
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return client
        except TimeoutError:
            continue
        except OSError as exc:  # pragma: no cover - accept 中断
            raise ScrcpyStartupError(f"accept timeout: {exc}") from exc
    raise ScrcpyStartupError(f"{context}: accept timeout")


def _device_socket_present(ops: AdbOps, cfg: ServerConfig) -> bool:
    try:
        output = ops.shell_text("cat /proc/net/unix", timeout=6.0)
    except Exception:  # noqa: BLE001
        return False
    return cfg.device_socket_name in (output or "")


def _wait_for_device_socket(
    ops: AdbOps,
    cfg: ServerConfig,
    deadline: float,
    process: Any,
    log_lines: list[str] | None = None,
) -> None:
    """服务端 LocalServerSocket 就绪前不 connect（否则 adb forward 会先 accept 再 abort → EOF）。"""
    while time.monotonic() < deadline:
        try:
            if process is not None and process.poll() is not None:
                raise _process_failure(process, "wait device socket", log_lines)
        except ScrcpyStartupError:
            raise
        except Exception:  # noqa: BLE001
            pass
        if _device_socket_present(ops, cfg):
            logger.info("device localabstract socket ready: @%s", cfg.device_socket_name)
            return
        time.sleep(0.1)
    raise ScrcpyStartupError(f"等待设备 socket 超时: localabstract:{cfg.device_socket_name}")


def _attempt_modes(cfg: ServerConfig) -> list[str]:
    if cfg.stream_mode != "auto":
        return [cfg.stream_mode]
    # 官方 v5 默认 full 握手优先；失败再尝试纯 12 字节头（raw）
    return ["full", "raw"]


def start_server(serial: str, cfg: ServerConfig | None = None, *, ops: AdbOps | None = None) -> ScrcpyServer:
    """推送并启动 scrcpy server，连 socket，自检首帧（≤8s）。

    失败自动尝试下一种握手模式（full → raw）；全部失败抛 ``SCRCPY_FAILED``（details 带
    command/scid/socket/server_log_tail/adb_stderr），保证进程/forward/socket 已清理。
    """
    cfg = cfg or ServerConfig.from_settings()
    verified = verify_jar(cfg)
    ops = ops or AdbOps(serial)
    attempts: list[dict[str, Any]] = []
    last_error: Exception | None = None
    for mode in _attempt_modes(cfg):
        try:
            server = _start_attempt(serial, cfg, ops, mode)
        except ApiError:
            raise
        except (ScrcpyStartupError, ScrcpyProtocolError, TimeoutError, OSError) as exc:
            logger.warning("scrcpy start attempt mode=%s failed: %s", mode, exc)
            item = {"mode": mode, "error": f"{type(exc).__name__}: {exc}"}
            item.update(getattr(exc, "details", {}) or {})
            attempts.append(item)
            last_error = exc
            continue
        logger.info(
            "scrcpy server ready: serial=%s mode=%s jar_sha256=%s geometry=%dx%d codec=%s",
            serial,
            mode,
            str(verified.get("sha256", ""))[:12],
            server.geometry.width,
            server.geometry.height,
            server.geometry.codec,
        )
        return server
    detail = attempts[-1]["error"] if attempts else "unknown"
    last = attempts[-1] if attempts else {}
    raise ApiError(
        "SCRCPY_FAILED",
        f"scrcpy server 启动失败：{detail}",
        {
            "serial": serial,
            "attempts": attempts,
            "cfg": cfg.to_dict(),
            "command": last.get("command", ""),
            "scid": cfg.scid,
            "socket": cfg.device_socket_name,
            "server_log_tail": last.get("server_log_tail", ""),
            "adb_stderr": last.get("adb_stderr", ""),
            "last_error": type(last_error).__name__ if last_error else "",
        },
    )


def _start_attempt(serial: str, cfg: ServerConfig, ops: AdbOps, mode: str) -> ScrcpyServer:
    process: Any = None
    video_sock: socket.socket | None = None
    control_sock: socket.socket | None = None
    listener: socket.socket | None = None
    local_port = 0
    forwarded = False
    log_lines: list[str] = []
    command = build_server_command(cfg, mode)
    remote_socket = f"localabstract:{cfg.device_socket_name}"
    details: dict[str, Any] = {
        "mode": mode,
        "command": command,
        "scid": cfg.scid,
        "socket": cfg.device_socket_name,
        "remote_jar": REMOTE_JAR_PATH,
    }
    try:
        details.update(ensure_remote_jar(ops, cfg))
        spec = STREAM_MODE_SPECS[mode]
        if cfg.tunnel_forward:
            local_port = _free_tcp_port()
            ops.forward(f"tcp:{local_port}", remote_socket)
            forwarded = True
        else:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(2)
            local_port = int(listener.getsockname()[1])
            ops.reverse(remote_socket, f"tcp:{local_port}")
            forwarded = True
        deadline = time.monotonic() + float(cfg.connect_timeout_s)
        logger.info(
            "starting scrcpy server for %s: mode=%s scid=%s socket=%s",
            serial,
            mode,
            cfg.scid,
            cfg.device_socket_name,
        )
        process = ops.start_process(command)
        log_lines = _attach_process_log(process)
        if cfg.tunnel_forward:
            _wait_for_device_socket(ops, cfg, deadline, process, log_lines)
        # v5：先连 video；control=true 时 server 会 accept 第二个 socket，必须连上才出帧
        if cfg.tunnel_forward:
            video_sock = _connect_tcp(local_port, deadline, process, log_lines, "connect video socket")
            if cfg.control:
                control_sock = _connect_tcp(local_port, deadline, process, log_lines, "connect control socket")
        else:
            assert listener is not None
            video_sock = _accept_tcp(listener, deadline, process, "accept video socket", log_lines)
            if cfg.control:
                control_sock = _accept_tcp(listener, deadline, process, "accept control socket", log_lines)
        reader = VideoFrameReader(video_sock)
        video_sock.settimeout(float(cfg.handshake_timeout_s))
        if spec is not None:
            try:
                stream_header = reader.read_stream_headers(spec)
            except ScrcpyDisconnected as exc:
                if process is not None and process.poll() is not None:
                    raise _process_failure(process, "stream handshake", log_lines) from exc
                raise
            logger.info(
                "scrcpy stream header: device=%r dummy_byte=%s codec=%s",
                stream_header.device_name,
                stream_header.dummy_byte,
                stream_header.codec or "unknown",
            )
        # 自检：等首个**真实图像帧**（跳过 session meta 与 config 包），整体 ≤first_frame_timeout
        first_deadline = time.monotonic() + float(cfg.first_frame_timeout_s)
        first_frame: tuple[int, bytes] | None = None
        while time.monotonic() < first_deadline:
            remaining = max(0.05, first_deadline - time.monotonic())
            video_sock.settimeout(remaining)
            try:
                pts_us, payload = reader.read_frame()
            except TimeoutError as exc:
                raise ScrcpyStartupError(
                    f"首帧超时（>{cfg.first_frame_timeout_s}s，模式={mode}）"
                ) from exc
            info = reader.last_info
            if info is not None and (info.config or info.session_meta):
                continue  # session meta / codec config → 继续等真实帧
            if not _looks_like_annexb(payload):
                raise ScrcpyProtocolError(
                    f"首帧不是 Annex B H.264（mode={mode}, len={len(payload)}, head={payload[:8].hex()}）"
                )
            if reader.geometry.width <= 0 or reader.geometry.height <= 0:
                raise ScrcpyProtocolError(f"无法确定视频尺寸（mode={mode}）")
            first_frame = (pts_us, payload)
            break
        if first_frame is None:
            raise ScrcpyStartupError(f"首帧超时（>{cfg.first_frame_timeout_s}s，模式={mode}）")
        video_sock.settimeout(None)
        details["server_log_tail"] = "\n".join(log_lines[-40:])
        server = ScrcpyServer(
            serial=serial,
            cfg=cfg,
            ops=ops,
            process=process,
            video_sock=video_sock,
            control_sock=control_sock,
            reader=reader,
            first_frame=first_frame,
            local_port=local_port,
            stream_mode=mode,
            log_lines=log_lines,
        )
        _register_server(server)
        return server
    except Exception as exc:
        # 任一失败都把本次尝试的资源清干净（不 pkill 别的 session；不删远端 jar）
        if control_sock is not None:
            try:
                control_sock.close()
            except OSError:
                pass
        if video_sock is not None:
            try:
                video_sock.close()
            except OSError:
                pass
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        _terminate_process(process)
        _kill_remote_server(ops, cfg)
        if forwarded:
            if cfg.tunnel_forward:
                try:
                    ops.remove_forward(f"tcp:{local_port}")
                except Exception:  # noqa: BLE001
                    pass
            else:
                try:
                    ops.remove_reverse(remote_socket)
                except Exception:  # noqa: BLE001
                    pass
        details["server_log_tail"] = "\n".join(log_lines[-40:])
        details["adb_stderr"] = _capture_crash_log(ops)
        exc.details = details  # type: ignore[attr-defined] - 供 start_server 汇总错误详情
        raise


def stop_server(server: ScrcpyServer | None = None) -> None:
    """停止指定 server；不传则停止本进程启动的全部 server（只清理自己的 forward）。"""
    if server is not None:
        server.stop()
        return
    for item in active_servers():
        try:
            item.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("stop scrcpy server %s failed: %s", item.cfg.socket_name, exc)


def cleanup_stale_forwards(serial: str | None = None, *, ops: AdbOps | None = None) -> list[str]:
    """只清理**本进程**已登记 session 的 forward/reverse；绝不 pkill 别人的 scrcpy。"""
    removed: list[str] = []
    for item in active_servers():
        if serial is not None and item.serial != serial:
            continue
        removed.append(item.cfg.socket_name)
        try:
            item.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("cleanup stale forward %s failed: %s", item.cfg.socket_name, exc)
    return removed


import atexit  # noqa: E402 - 放到末尾，避免污染模块头部 import 分组

atexit.register(lambda: stop_server(None))
