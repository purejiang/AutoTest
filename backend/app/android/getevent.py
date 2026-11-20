"""物理录制：getevent 流解析（契约 §8 / §14）。

- ``touch_device_detection(serial)``：``getevent -pl`` 解析出触摸设备节点与 ABS 范围；
- ``GeteventParser`` / ``parse_lines``：把 ``getevent -lt`` 行解析成 ``TouchSample`` / ``KeyEvent``
  （协议 A/B 状态机：ABS_MT_SLOT、ABS_MT_TRACKING_ID、SYN_REPORT 成帧、BTN_TOUCH）；
- ``stream_events(serial, on_touch, on_key, stop_event, device_path=None)``：真正拉流
  （``adb -s S exec-out getevent -lt <dev>``），回调消费；``stop_event`` 可停。

行格式（``getevent -t`` 数字模式，例）::

    [  1234.567] /dev/input/event2: 0003 0035 00000123

也兼容 ``-l`` 标签模式（``EV_ABS ABS_MT_POSITION_X 00000123``）。触摸坐标用 ABS 范围线性
归一化到 0..1；``t`` 是 getevent 时间戳（设备/主机秒）。

无真机可测：``parse_pl_output`` / ``parse_lines`` 是纯函数（tests/test_media_getevent.py 喂样例文本）。
"""

from __future__ import annotations

import re
import subprocess
import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import config
from ..errors import ApiError
from ..logs import logger

# ---- Linux input event 常量
EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03

SYN_REPORT = 0x00

ABS_X = 0x00
ABS_Y = 0x01
ABS_PRESSURE = 0x18
ABS_MT_SLOT = 0x2F
ABS_MT_TOUCH_MAJOR = 0x30
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TRACKING_ID = 0x39
ABS_MT_PRESSURE = 0x3A
BTN_TOUCH = 0x14A

# EV_KEY 中 0x100~0x17f 是 BTN_*（触摸/笔/按键组），不算 Android 物理键
_BTN_RANGE = range(0x100, 0x180)

# 标签模式（getevent -l）用到的映射（只覆盖录制需要的最小子集）
_EV_TYPES = {"EV_SYN": EV_SYN, "EV_KEY": EV_KEY, "EV_ABS": EV_ABS}
_EV_CODES = {
    "SYN_REPORT": SYN_REPORT,
    "BTN_TOUCH": BTN_TOUCH,
    "ABS_X": ABS_X,
    "ABS_Y": ABS_Y,
    "ABS_PRESSURE": ABS_PRESSURE,
    "ABS_MT_SLOT": ABS_MT_SLOT,
    "ABS_MT_TOUCH_MAJOR": ABS_MT_TOUCH_MAJOR,
    "ABS_MT_POSITION_X": ABS_MT_POSITION_X,
    "ABS_MT_POSITION_Y": ABS_MT_POSITION_Y,
    "ABS_MT_TRACKING_ID": ABS_MT_TRACKING_ID,
    "ABS_MT_PRESSURE": ABS_MT_PRESSURE,
}

_DEFAULT_ABS_X = (0, 1079)
_DEFAULT_ABS_Y = (0, 2399)
# getevent 的 tracking id = -1 在无符号十六进制里通常打印成 ffffffff
_TRACKING_ID_NONE = -1


# ---------------------------------------------------------------- 数据类
@dataclass(frozen=True)
class TouchSample:
    """一次触摸状态（SYN_REPORT 成帧）：坐标已归一化到 0..1。"""

    t: float
    x: float
    y: float
    down: bool
    tracking_id: int = -1
    pressure: float = 0.0


@dataclass(frozen=True)
class KeyEvent:
    """物理按键（EV_KEY，keycode 为 Linux key code）。"""

    t: float
    keycode: int
    down: bool


@dataclass
class AbsAxis:
    min: int = 0
    max: int = 0

    @property
    def span(self) -> int:
        return max(0, self.max - self.min)

    def normalize(self, value: int) -> float:
        if self.span <= 0:
            return 0.0
        return min(1.0, max(0.0, (value - self.min) / self.span))

    def to_tuple(self) -> tuple[int, int]:
        return (self.min, self.max)


@dataclass
class TouchDevice:
    """``getevent -pl`` 探测出的输入设备。"""

    path: str = ""
    name: str = ""
    abs_x: AbsAxis = field(default_factory=AbsAxis)
    abs_y: AbsAxis = field(default_factory=AbsAxis)
    has_mt_slots: bool = False
    has_tracking_id: bool = False
    has_btn_touch: bool = False
    abs_pressure: AbsAxis | None = None
    # 原始 value 行（调试/排障）
    raw: dict[str, tuple[int, int, int]] = field(default_factory=dict)

    @property
    def is_touch(self) -> bool:
        return (self.abs_x.span > 0 and self.abs_y.span > 0) or (
            "ABS_MT_POSITION_X" in self.raw and "ABS_MT_POSITION_Y" in self.raw
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "abs_x": list(self.abs_x.to_tuple()),
            "abs_y": list(self.abs_y.to_tuple()),
            "has_mt_slots": self.has_mt_slots,
            "has_tracking_id": self.has_tracking_id,
            "has_btn_touch": self.has_btn_touch,
            "is_touch": self.is_touch,
        }


# ---------------------------------------------------------------- getevent -pl 解析
_DEVICE_RE = re.compile(r"^add device \d+:\s*(?P<path>/\S+)")
_NAME_RE = re.compile(r'^\s*name:\s*"(?P<name>.*)"\s*$')
_PROP_RE = re.compile(
    r"^\s*(?P<type>[A-Z_]+)\s+\((?P<type_hex>[0-9a-fA-F]{4})\):\s*"
    r"(?P<code>[A-Za-z0-9_]+)\s*:\s*value\s+(?P<value>-?\d+),\s*min\s+(?P<min>-?\d+),\s*max\s+(?P<max>-?\d+)"
)
# getevent -p 的续行：同一 type 组内后续 code 不再重复 "ABS (0003):" 前缀
_PROP_CONT_RE = re.compile(
    r"^\s+(?P<code>[A-Za-z0-9_]+)\s*:\s*value\s+(?P<value>-?\d+),\s*min\s+(?P<min>-?\d+),\s*max\s+(?P<max>-?\d+)"
)


def _apply_prop(current: TouchDevice, code: str, value: int, axis_min: int, axis_max: int) -> None:
    current.raw[code] = (value, axis_min, axis_max)
    if code in ("ABS_MT_POSITION_X", "ABS_X"):
        if code == "ABS_MT_POSITION_X" or not current.abs_x.span:
            current.abs_x = AbsAxis(axis_min, axis_max)
    elif code in ("ABS_MT_POSITION_Y", "ABS_Y"):
        if code == "ABS_MT_POSITION_Y" or not current.abs_y.span:
            current.abs_y = AbsAxis(axis_min, axis_max)
    elif code == "ABS_MT_SLOT":
        current.has_mt_slots = True
    elif code == "ABS_MT_TRACKING_ID":
        current.has_tracking_id = True
    elif code == "BTN_TOUCH":
        current.has_btn_touch = True
    elif code in ("ABS_MT_PRESSURE", "ABS_PRESSURE"):
        current.abs_pressure = AbsAxis(axis_min, axis_max)


def parse_pl_output(text: str) -> list[TouchDevice]:
    """解析 ``adb shell getevent -pl`` 输出为设备列表（保持出现顺序）。

    兼容两种排版：每个 code 都带 ``TYPE (xxxx):`` 前缀，或同 type 组内后续 code 只给缩进续行。
    """
    devices: list[TouchDevice] = []
    current: TouchDevice | None = None
    current_type: str | None = None
    for line in (text or "").splitlines():
        match = _DEVICE_RE.match(line)
        if match:
            current = TouchDevice(path=match.group("path"))
            current_type = None
            devices.append(current)
            continue
        if current is None:
            continue
        name_match = _NAME_RE.match(line)
        if name_match:
            current.name = name_match.group("name")
            continue
        prop_match = _PROP_RE.match(line)
        if prop_match:
            current_type = prop_match.group("type")
            if current_type in ("ABS", "KEY"):
                _apply_prop(
                    current,
                    prop_match.group("code"),
                    int(prop_match.group("value")),
                    int(prop_match.group("min")),
                    int(prop_match.group("max")),
                )
            continue
        cont_match = _PROP_CONT_RE.match(line)
        if cont_match and current_type in ("ABS", "KEY"):
            _apply_prop(
                current,
                cont_match.group("code"),
                int(cont_match.group("value")),
                int(cont_match.group("min")),
                int(cont_match.group("max")),
            )
    return devices


def pick_touch_device(devices: list[TouchDevice]) -> TouchDevice | None:
    """优先带 MT 协议 B 的触摸屏；其次任意带 ABS_MT 坐标/ABS_X/Y 的设备。"""
    candidates = [dev for dev in devices if dev.is_touch]
    if not candidates:
        return None

    def score(dev: TouchDevice) -> int:
        value = 0
        if dev.has_mt_slots:
            value += 4
        if dev.has_tracking_id:
            value += 2
        if dev.has_btn_touch:
            value += 1
        if "touch" in dev.name.lower():
            value += 8
        return value

    return max(candidates, key=score)


# ---------------------------------------------------------------- adb 交互
def _shell_runner(serial: str, command: str, timeout: float = 20.0) -> str:
    """优先 be-driver 的 app.android.adb.shell_bytes（契约 §14.1），否则 subprocess 兜底。"""
    try:
        from . import adb as adb_module  # noqa: PLC0415 - 队友文件可能尚未就绪

        raw = adb_module.shell_bytes(serial, command)
        return raw.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        logger.debug("app.android.adb.shell_bytes unavailable (%s), falling back to subprocess", exc)
    try:
        result = subprocess.run(
            [config.find_adb(), "-s", serial, "shell", command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            f"adb 不可用（未找到 {config.find_adb()}）",
            {"serial": serial},
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ApiError("DEVICE_UNAVAILABLE", f"getevent 探测超时: {command}", {"serial": serial}) from exc
    return (result.stdout or "") + (result.stderr or "")


def touch_device_detection(
    serial: str,
    *,
    shell_runner: Callable[[str], str] | None = None,
) -> TouchDevice | None:
    """``getevent -pl`` 探测触摸设备（返回 None 表示没有可用触摸设备）。"""
    runner = shell_runner or (lambda command: _shell_runner(serial, command))
    text = runner("getevent -pl")
    devices = parse_pl_output(text)
    device = pick_touch_device(devices)
    if device is None:
        logger.warning("no touch device detected for %s (getevent -pl devices=%d)", serial, len(devices))
    else:
        logger.info(
            "touch device detected for %s: %s (%r) x=%s y=%s",
            serial,
            device.path,
            device.name,
            device.abs_x.to_tuple(),
            device.abs_y.to_tuple(),
        )
    return device


# ---------------------------------------------------------------- 行解析 / 协议状态机
_LINE_RE = re.compile(
    r"^\s*(?:\[?\s*(?P<t>\d+\.\d+)\s*\]?\s*)?(?P<dev>/dev/input/\S+?):\s*(?P<rest>.+?)\s*$"
)


def _parse_line(line: str) -> tuple[float, str, int, int, int] | None:
    """一行 → (t, device, ev_type, code, value)；无法解析返回 None。"""
    match = _LINE_RE.match(line)
    if not match:
        return None
    try:
        timestamp = float(match.group("t")) if match.group("t") else 0.0
    except (TypeError, ValueError):
        return None
    parts = match.group("rest").split()
    if len(parts) < 3:
        return None
    device = match.group("dev")
    first, second, third = parts[0], parts[1], parts[2]
    try:
        ev_type = int(first, 16)
        code = int(second, 16)
        value = int(third, 16)
        return (timestamp, device, ev_type, code, value)
    except ValueError:
        pass
    # 标签模式：EV_ABS ABS_MT_POSITION_X 00000123
    ev_type = _EV_TYPES.get(first)
    code = _EV_CODES.get(second)
    if ev_type is None or code is None:
        return None
    try:
        value = int(third, 16)
    except ValueError:
        return None
    return (timestamp, device, ev_type, code, value)


class GeteventParser:
    """协议 A/B 状态机：喂行，吐出 ``TouchSample`` / ``KeyEvent``。

    - 协议 B（有 ABS_MT_SLOT）：按 slot 维护 tracking_id/x/y；
    - 协议 A（无 SLOT）：一个 contact，tracking_id=-1 表示抬起；
    - SYN_REPORT 成帧；只有状态真正变化才产事件（down/move/up 各一次，不重复刷）。
    """

    def __init__(
        self,
        *,
        abs_x: tuple[int, int] | None = None,
        abs_y: tuple[int, int] | None = None,
        device: str | None = None,
        pressure_max: float = 255.0,
        default_pressure: float = 1.0,
    ) -> None:
        self.abs_x = AbsAxis(*(abs_x or _DEFAULT_ABS_X))
        self.abs_y = AbsAxis(*(abs_y or _DEFAULT_ABS_Y))
        self.device = device
        self.pressure_max = max(1.0, float(pressure_max))
        self.default_pressure = float(default_pressure)
        self._last_t = 0.0
        self._protocol_b = False
        self._current_slot = 0
        self._slots: dict[int, dict[str, Any]] = {}
        self._changed_slots: set[int] = set()
        self._btn_touch = False
        self._syn_pending = False

    # ---- 内部
    def _slot(self, slot: int) -> dict[str, Any]:
        return self._slots.setdefault(
            slot,
            {"tracking_id": _TRACKING_ID_NONE, "x": None, "y": None, "down": False, "pressure": None},
        )

    def _norm_pressure(self, raw: int | None) -> float:
        if raw is None:
            return self.default_pressure
        return min(1.0, max(0.0, float(raw) / self.pressure_max))

    # ---- 主入口
    def feed_line(self, line: str) -> list[TouchSample | KeyEvent]:
        parsed = _parse_line(line)
        if parsed is None:
            return []
        timestamp, device, ev_type, code, value = parsed
        if self.device and device != self.device:
            return []
        self._last_t = timestamp
        events: list[TouchSample | KeyEvent] = []
        if ev_type == EV_ABS:
            self._handle_abs(code, value)
        elif ev_type == EV_KEY:
            if code == BTN_TOUCH:
                self._btn_touch = bool(value)
                self._syn_pending = True
            elif code not in _BTN_RANGE:
                events.append(KeyEvent(t=timestamp, keycode=code, down=bool(value)))
        elif ev_type == EV_SYN and code == SYN_REPORT:
            events.extend(self._flush_syn())
        return events

    def _handle_abs(self, code: int, value: int) -> None:
        if code == ABS_MT_SLOT:
            self._protocol_b = True
            self._current_slot = value
            self._changed_slots.add(value)
            self._syn_pending = True
            return
        if code == ABS_MT_TRACKING_ID:
            self._protocol_b = True
            slot = self._slot(self._current_slot)
            slot["tracking_id"] = -1 if value in (0xFFFFFFFF, -1) else value
            self._changed_slots.add(self._current_slot)
            self._syn_pending = True
            return
        slot = self._slot(self._current_slot)
        if code == ABS_MT_POSITION_X or code == ABS_X:
            slot["x"] = int(value)
            self._changed_slots.add(self._current_slot)
            self._syn_pending = True
        elif code == ABS_MT_POSITION_Y or code == ABS_Y:
            slot["y"] = int(value)
            self._changed_slots.add(self._current_slot)
            self._syn_pending = True
        elif code in (ABS_MT_PRESSURE, ABS_PRESSURE):
            slot["pressure"] = int(value)
            self._changed_slots.add(self._current_slot)
            self._syn_pending = True

    def _flush_syn(self) -> list[TouchSample | KeyEvent]:
        if not self._syn_pending:
            return []
        self._syn_pending = False
        events: list[TouchSample | KeyEvent] = []
        slots = sorted(self._changed_slots) or ([0] if not self._slots else [])
        self._changed_slots = set()
        for slot_index in slots:
            slot = self._slot(slot_index)
            tracking_id = int(slot["tracking_id"])
            raw_x = slot["x"]
            raw_y = slot["y"]
            if tracking_id < 0:
                if slot["down"]:
                    x = self.abs_x.normalize(raw_x) if raw_x is not None else 0.0
                    y = self.abs_y.normalize(raw_y) if raw_y is not None else 0.0
                    events.append(
                        TouchSample(
                            t=self._last_t,
                            x=x,
                            y=y,
                            down=False,
                            tracking_id=-1,
                            pressure=0.0,
                        )
                    )
                    slot["down"] = False
                    slot.pop("last_emitted", None)
                elif not self._protocol_b and self._btn_touch and raw_x is not None and raw_y is not None:
                    # 协议 A 且无 ABS_MT_TRACKING_ID 的老设备：BTN_TOUCH 按下 + 坐标即一次 down
                    x = self.abs_x.normalize(int(raw_x))
                    y = self.abs_y.normalize(int(raw_y))
                    state = (x, y, -1)
                    if slot.get("last_emitted") != state:
                        slot["last_emitted"] = state
                        slot["down"] = True
                        events.append(
                            TouchSample(
                                t=self._last_t,
                                x=x,
                                y=y,
                                down=True,
                                tracking_id=-1,
                                pressure=self._norm_pressure(slot["pressure"]),
                            )
                        )
                continue
            if raw_x is None or raw_y is None:
                continue
            x = self.abs_x.normalize(int(raw_x))
            y = self.abs_y.normalize(int(raw_y))
            previous = slot.get("last_emitted")
            state = (x, y, tracking_id)
            if previous == state and slot["down"]:
                continue
            slot["last_emitted"] = state
            slot["down"] = True
            events.append(
                TouchSample(
                    t=self._last_t,
                    x=x,
                    y=y,
                    down=True,
                    tracking_id=tracking_id,
                    pressure=self._norm_pressure(slot["pressure"]),
                )
            )
        return events


def parse_lines(
    lines: Iterable[str],
    *,
    abs_x: tuple[int, int] | None = None,
    abs_y: tuple[int, int] | None = None,
    device: str | None = None,
) -> Iterator[TouchSample | KeyEvent]:
    """回放模式：把 ``getevent -lt`` 行序列解析成事件（测试用；无 I/O）。"""
    parser = GeteventParser(abs_x=abs_x, abs_y=abs_y, device=device)
    for line in lines:
        yield from parser.feed_line(line)


# ---------------------------------------------------------------- 实时流
def _start_getevent(serial: str, device_path: str) -> subprocess.Popen[str]:
    argv = [config.find_adb(), "-s", serial, "exec-out", "getevent", "-lt", device_path]
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
            f"adb 不可用（未找到 {config.find_adb()}）",
            {"serial": serial},
        ) from exc


def _stop_process(process: subprocess.Popen[str]) -> None:
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("stop getevent process failed: %s", exc)


def stream_events(
    serial: str,
    on_touch: Callable[[TouchSample], None] | None,
    on_key: Callable[[KeyEvent], None] | None,
    stop_event: threading.Event | None = None,
    device_path: str | None = None,
    *,
    device: TouchDevice | None = None,
) -> None:
    """拉 ``getevent -lt`` 流并回放成事件，直到 ``stop_event`` 置位/设备断开。

    ``on_touch`` / ``on_key`` 为 None 时对应事件丢弃；异常只记录（调用方负责重连策略）。
    """
    stop_event = stop_event or threading.Event()
    if device is None:
        device = touch_device_detection(serial)
    if device_path is None:
        if device is None:
            raise ApiError(
                "DEVICE_UNAVAILABLE",
                "未检测到触摸设备（getevent -pl 无 ABS_MT_POSITION_X/Y）",
                {"serial": serial},
            )
        device_path = device.path
    abs_x = device.abs_x.to_tuple() if device is not None and device.abs_x.span > 0 else None
    abs_y = device.abs_y.to_tuple() if device is not None and device.abs_y.span > 0 else None
    parser = GeteventParser(abs_x=abs_x, abs_y=abs_y, device=device_path)
    process = _start_getevent(serial, device_path)
    logger.info("getevent stream started: serial=%s device=%s", serial, device_path)
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if stop_event.is_set():
                break
            for event in parser.feed_line(line):
                if isinstance(event, TouchSample):
                    if on_touch is not None:
                        on_touch(event)
                elif isinstance(event, KeyEvent) and on_key is not None:
                    on_key(event)
        if process.poll() is None:
            process.wait(timeout=5.0)
    except Exception as exc:  # noqa: BLE001 - 结束/管道错误
        logger.warning("getevent stream for %s ended: %s", serial, exc)
    finally:
        _stop_process(process)
        logger.info("getevent stream stopped: serial=%s device=%s", serial, device_path)
