"""无真机测试：getevent -pl 设备探测 + getevent -lt 协议 A/B 流解析。"""

from __future__ import annotations

import threading

from app.android import getevent
from app.android.getevent import AbsAxis, GeteventParser, KeyEvent, TouchDevice, TouchSample

TOUCH_DEV = "/dev/input/event2"
KEY_DEV = "/dev/input/event0"

GETEVENT_PL = """
add device 1: /dev/input/event0
  name:     "gpio-keys"
  events:
    KEY (0001): KEY_VOLUMEUP          : value 0, min 0, max 1, fuzz 0, flat 0, resolution 0
                KEY_VOLUMEDOWN        : value 0, min 0, max 1, fuzz 0, flat 0, resolution 0
    SYN (0000): SYN_REPORT            : value 0, min 0, max 0, fuzz 0, flat 0, resolution 0
add device 2: /dev/input/event2
  name:     "touchscreen"
  events:
    ABS (0003): ABS_MT_SLOT           : value 0, min 0, max 9, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_X     : value 0, min 0, max 1079, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_Y     : value 0, min 0, max 2399, fuzz 0, flat 0, resolution 0
                ABS_MT_TRACKING_ID    : value 0, min 0, max 65535, fuzz 0, flat 0, resolution 0
                ABS_MT_PRESSURE       : value 0, min 0, max 255, fuzz 0, flat 0, resolution 0
    KEY (0001): BTN_TOUCH             : value 0, min 0, max 1, fuzz 0, flat 0, resolution 0
    SYN (0000): SYN_REPORT            : value 0, min 0, max 0, fuzz 0, flat 0, resolution 0
"""

# 协议 B：ABS_MT_SLOT + TRACKING_ID + SYN_REPORT 成帧
PROTOCOL_B = [
    "[  100.000000] /dev/input/event2: 0003 002f 00000000",
    "[  100.000000] /dev/input/event2: 0003 0039 0000002a",
    "[  100.000000] /dev/input/event2: 0003 0035 00000200",
    "[  100.000000] /dev/input/event2: 0003 0036 00000400",
    "[  100.000000] /dev/input/event2: 0003 003a 00000080",
    "[  100.000000] /dev/input/event2: 0001 014a 00000001",
    "[  100.000000] /dev/input/event2: 0000 0000 00000000",
    "[  100.050000] /dev/input/event2: 0003 0035 00000300",
    "[  100.050000] /dev/input/event2: 0003 0036 00000600",
    "[  100.050000] /dev/input/event2: 0000 0000 00000000",
    "[  100.100000] /dev/input/event2: 0003 0039 ffffffff",
    "[  100.100000] /dev/input/event2: 0001 014a 00000000",
    "[  100.100000] /dev/input/event2: 0000 0000 00000000",
]

# 协议 A：无 ABS_MT_SLOT，单 contact
PROTOCOL_A = [
    "[  200.000000] /dev/input/event2: 0003 0039 00000007",
    "[  200.000000] /dev/input/event2: 0003 0035 00000100",
    "[  200.000000] /dev/input/event2: 0003 0036 00000200",
    "[  200.000000] /dev/input/event2: 0001 014a 00000001",
    "[  200.000000] /dev/input/event2: 0000 0000 00000000",
    "[  200.100000] /dev/input/event2: 0003 0039 ffffffff",
    "[  200.100000] /dev/input/event2: 0001 014a 00000000",
    "[  200.100000] /dev/input/event2: 0000 0000 00000000",
]

KEY_EVENTS = [
    "[  300.000000] /dev/input/event0: 0001 0072 00000001",
    "[  300.000000] /dev/input/event0: 0000 0000 00000000",
    "[  300.050000] /dev/input/event0: 0001 0072 00000000",
    "[  300.050000] /dev/input/event0: 0000 0000 00000000",
]


def _parse(lines, **kwargs):
    return list(getevent.parse_lines(lines, **kwargs))


# ---------------------------------------------------------------- getevent -pl
def test_parse_pl_output() -> None:
    devices = getevent.parse_pl_output(GETEVENT_PL)
    assert [dev.path for dev in devices] == [KEY_DEV, TOUCH_DEV]
    touch = devices[1]
    assert touch.name == "touchscreen"
    assert touch.abs_x.to_tuple() == (0, 1079)
    assert touch.abs_y.to_tuple() == (0, 2399)
    assert touch.has_mt_slots is True
    assert touch.has_tracking_id is True
    assert touch.has_btn_touch is True
    assert touch.abs_pressure is not None
    assert touch.abs_pressure.to_tuple() == (0, 255)
    assert touch.is_touch is True
    assert devices[0].is_touch is False


def test_pick_touch_device_prefers_touchscreen() -> None:
    devices = getevent.parse_pl_output(GETEVENT_PL)
    picked = getevent.pick_touch_device(devices)
    assert picked is not None
    assert picked.path == TOUCH_DEV


def test_touch_device_detection_with_injected_shell_runner() -> None:
    calls: list[str] = []

    def runner(command: str) -> str:
        calls.append(command)
        return GETEVENT_PL

    device = getevent.touch_device_detection("fake-serial", shell_runner=runner)
    assert device is not None
    assert device.path == TOUCH_DEV
    assert calls == ["getevent -pl"]


def test_touch_device_detection_without_touch() -> None:
    only_keys = GETEVENT_PL.split("add device 2:")[0]
    device = getevent.touch_device_detection("fake-serial", shell_runner=lambda _cmd: only_keys)
    assert device is None


# ---------------------------------------------------------------- 协议 B 触摸
def test_protocol_b_down_move_up() -> None:
    events = _parse(PROTOCOL_B, abs_x=(0, 1079), abs_y=(0, 2399))
    touches = [event for event in events if isinstance(event, TouchSample)]
    assert len(touches) == 3, touches
    down, move, up = touches
    assert down.down is True
    assert down.tracking_id == 0x2A
    assert down.x == 512 / 1079
    assert down.y == 1024 / 2399
    assert down.pressure == 0x80 / 255.0
    assert down.t == 100.0
    assert move.down is True
    assert move.tracking_id == 0x2A
    assert move.x == 768 / 1079
    assert move.y == 1536 / 2399
    assert move.t == 100.05
    assert up.down is False
    assert up.tracking_id == -1
    assert up.t == 100.1


def test_protocol_b_multiple_slots() -> None:
    lines = [
        "[  1.000000] /dev/input/event2: 0003 002f 00000000",
        "[  1.000000] /dev/input/event2: 0003 0039 00000001",
        "[  1.000000] /dev/input/event2: 0003 0035 00000064",
        "[  1.000000] /dev/input/event2: 0003 0036 000000c8",
        "[  1.000000] /dev/input/event2: 0003 002f 00000001",
        "[  1.000000] /dev/input/event2: 0003 0039 00000002",
        "[  1.000000] /dev/input/event2: 0003 0035 00000190",
        "[  1.000000] /dev/input/event2: 0003 0036 00000258",
        "[  1.000000] /dev/input/event2: 0000 0000 00000000",
        "[  1.100000] /dev/input/event2: 0003 0039 ffffffff",  # 当前 slot(1) 抬起
        "[  1.100000] /dev/input/event2: 0000 0000 00000000",
    ]
    events = [event for event in _parse(lines, abs_x=(0, 1000), abs_y=(0, 2000)) if isinstance(event, TouchSample)]
    downs = [event for event in events if event.down]
    ups = [event for event in events if not event.down]
    assert {event.tracking_id for event in downs} == {1, 2}
    assert len(ups) == 1
    assert ups[0].tracking_id == -1


# ---------------------------------------------------------------- 协议 A
def test_protocol_a_down_up() -> None:
    touches = [event for event in _parse(PROTOCOL_A, abs_x=(0, 1079), abs_y=(0, 2399)) if isinstance(event, TouchSample)]
    assert [event.down for event in touches] == [True, False]
    assert touches[0].tracking_id == 7
    assert touches[0].x == 256 / 1079
    assert touches[0].y == 512 / 2399
    assert touches[0].t == 200.0
    assert touches[1].t == 200.1
    assert touches[1].down is False


# ---------------------------------------------------------------- 按键
def test_key_events() -> None:
    events = _parse(KEY_EVENTS)
    keys = [event for event in events if isinstance(event, KeyEvent)]
    assert len(keys) == 2
    assert keys[0].keycode == 0x72
    assert keys[0].down is True
    assert keys[0].t == 300.0
    assert keys[1].down is False
    assert keys[1].t == 300.05


def test_btn_touch_not_reported_as_key() -> None:
    events = _parse(PROTOCOL_B, abs_x=(0, 1079), abs_y=(0, 2399))
    assert all(not isinstance(event, KeyEvent) for event in events)


# ---------------------------------------------------------------- 过滤 / 标签模式
def test_device_filter() -> None:
    lines = [*PROTOCOL_B, *KEY_EVENTS]
    events = _parse(lines, abs_x=(0, 1079), abs_y=(0, 2399), device=TOUCH_DEV)
    assert all(isinstance(event, TouchSample) for event in events)
    assert len(events) == 3


def test_labeled_getevent_lines() -> None:
    lines = [
        "[  10.000000] /dev/input/event2: EV_ABS ABS_MT_POSITION_X 00000200",
        "[  10.000000] /dev/input/event2: EV_ABS ABS_MT_POSITION_Y 00000400",
        "[  10.000000] /dev/input/event2: EV_ABS ABS_MT_TRACKING_ID 00000001",
        "[  10.000000] /dev/input/event2: EV_SYN SYN_REPORT 00000000",
        "[  10.000000] /dev/input/event2: EV_KEY BTN_TOUCH 00000001",
    ]
    touches = [event for event in _parse(lines, abs_x=(0, 1000), abs_y=(0, 2000)) if isinstance(event, TouchSample)]
    assert len(touches) == 1
    assert touches[0].down is True
    assert touches[0].x == 512 / 1000
    assert touches[0].y == 1024 / 2000


def test_parser_rejects_garbage_lines() -> None:
    parser = GeteventParser(abs_x=(0, 100), abs_y=(0, 100))
    assert parser.feed_line("") == []
    assert parser.feed_line("not an event line") == []
    assert parser.feed_line("[1.0] /dev/input/event2: zz zz zz") == []


def test_abs_range_normalize() -> None:
    axis = AbsAxis(100, 200)
    assert axis.normalize(100) == 0.0
    assert axis.normalize(200) == 1.0
    assert axis.normalize(150) == 0.5
    assert axis.normalize(0) == 0.0  # clamp
    assert axis.normalize(300) == 1.0
    assert AbsAxis(0, 0).normalize(5) == 0.0


# ---------------------------------------------------------------- 真机抓包回归（指定设备无前缀）
# 来自 Android 12 / input_mt_wrapper 的原始输出：`getevent -lt <device>` 不带
# "/dev/input/eventX:" 前缀；抬手只发 BTN_TOUCH UP，不发 tracking id -1。
REAL_LABELED_TAP_NO_PREFIX = [
    "[   93856.488781] EV_ABS       ABS_MT_POSITION_X    00001516",
    "[   93856.488781] EV_ABS       ABS_MT_POSITION_Y    0000457a",
    "[   93856.488781] EV_ABS       ABS_MT_PRESSURE      00000164",
    "[   93856.488781] EV_ABS       ABS_MT_TRACKING_ID   00000000",
    "[   93856.488781] EV_ABS       ABS_MT_TOUCH_MAJOR   000000c9",
    "[   93856.488781] EV_ABS       ABS_MT_TOUCH_MINOR   000000b7",
    "[   93856.488781] EV_ABS       ABS_MT_ORIENTATION   00000027",
    "[   93856.488781] EV_ABS       ABS_MT_BLOB_ID       00000001",
    "[   93856.488781] EV_SYN       SYN_MT_REPORT        00000000",
    "[   93856.488781] EV_KEY       BTN_TOUCH            DOWN",
    "[   93856.488781] EV_SYN       SYN_REPORT           00000000",
    "[   93856.614418] EV_ABS       ABS_MT_POSITION_X    00001516",
    "[   93856.614418] EV_ABS       ABS_MT_POSITION_Y    0000457a",
    "[   93856.614418] EV_ABS       ABS_MT_PRESSURE      00000000",
    "[   93856.614418] EV_ABS       ABS_MT_TOUCH_MAJOR   00000000",
    "[   93856.614418] EV_ABS       ABS_MT_TOUCH_MINOR   00000000",
    "[   93856.614418] EV_ABS       ABS_MT_ORIENTATION   00000000",
    "[   93856.614418] EV_ABS       ABS_MT_BLOB_ID       00000000",
    "[   93856.614418] EV_SYN       SYN_MT_REPORT        00000000",
    "[   93856.614418] EV_KEY       BTN_TOUCH            UP",
    "[   93856.614418] EV_SYN       SYN_REPORT           00000000",
]

REAL_NUMERIC_TAP_NO_PREFIX = [
    "[   93856.488781] 0003 0035 00001516",
    "[   93856.488781] 0003 0036 0000457a",
    "[   93856.488781] 0003 003a 00000164",
    "[   93856.488781] 0003 0039 00000000",
    "[   93856.488781] 0001 014a 00000001",
    "[   93856.488781] 0000 0000 00000000",
    "[   93856.614418] 0003 0035 00001516",
    "[   93856.614418] 0003 0036 0000457a",
    "[   93856.614418] 0003 0039 00000000",
    "[   93856.614418] 0001 014a 00000000",
    "[   93856.614418] 0000 0000 00000000",
]


def test_real_labeled_tap_without_device_prefix() -> None:
    touches = [
        event
        for event in _parse(
            REAL_LABELED_TAP_NO_PREFIX,
            abs_x=(0, 9791),
            abs_y=(0, 21599),
            device=TOUCH_DEV,
        )
        if isinstance(event, TouchSample)
    ]
    assert [event.down for event in touches] == [True, False]
    assert touches[0].x == 0x1516 / 9791
    assert touches[0].y == 0x457A / 21599
    assert touches[0].t == 93856.488781
    assert touches[1].down is False
    assert touches[1].t == 93856.614418


def test_real_numeric_tap_without_device_prefix() -> None:
    touches = [
        event
        for event in _parse(
            REAL_NUMERIC_TAP_NO_PREFIX,
            abs_x=(0, 9791),
            abs_y=(0, 21599),
            device=TOUCH_DEV,
        )
        if isinstance(event, TouchSample)
    ]
    assert [event.down for event in touches] == [True, False]
    assert touches[0].x == 0x1516 / 9791
    assert touches[0].y == 0x457A / 21599


def test_btn_touch_up_releases_all_active_slots() -> None:
    lines = [
        "[  1.000000] 0003 002f 00000000",
        "[  1.000000] 0003 0039 00000001",
        "[  1.000000] 0003 0035 00000064",
        "[  1.000000] 0003 0036 000000c8",
        "[  1.000000] 0003 002f 00000001",
        "[  1.000000] 0003 0039 00000002",
        "[  1.000000] 0003 0035 00000190",
        "[  1.000000] 0003 0036 00000258",
        "[  1.000000] 0001 014a 00000001",
        "[  1.000000] 0000 0000 00000000",
        "[  1.100000] 0001 014a 00000000",
        "[  1.100000] 0000 0000 00000000",
    ]
    touches = [
        event
        for event in _parse(lines, abs_x=(0, 1000), abs_y=(0, 2000))
        if isinstance(event, TouchSample)
    ]
    assert [event.down for event in touches] == [True, True, False, False]
    assert [event.tracking_id for event in touches if not event.down] == [-1, -1]


def test_start_getevent_uses_numeric_mode(monkeypatch) -> None:
    recorded: dict[str, object] = {}

    class _Popen:
        def __init__(self, argv, **kwargs) -> None:
            recorded["argv"] = argv
            recorded["kwargs"] = kwargs

    monkeypatch.setattr(getevent.config, "find_adb", lambda: "/bin/adb")
    monkeypatch.setattr(getevent.subprocess, "Popen", _Popen)
    getevent._start_getevent("serial-1", "/dev/input/event3")
    assert recorded["argv"] == [
        "/bin/adb",
        "-s",
        "serial-1",
        "exec-out",
        "getevent",
        "-t",
        "/dev/input/event3",
    ]


# ---------------------------------------------------------------- stream_events（假进程）
class FakeProcess:
    def __init__(self, lines: list[str]) -> None:
        self.stdout = iter(lines)
        self.stdin = None
        self._finished = False
        self.terminated = False

    def poll(self):
        return 0 if self._finished else None

    def wait(self, timeout=None):
        self._finished = True
        return 0

    def terminate(self) -> None:
        self.terminated = True
        self._finished = True

    def kill(self) -> None:
        self._finished = True


def test_stream_events_dispatches_callbacks(monkeypatch) -> None:
    lines = [*PROTOCOL_B, *KEY_EVENTS]
    fake = FakeProcess(lines)
    monkeypatch.setattr(getevent, "_start_getevent", lambda serial, path: fake)
    touches: list[TouchSample] = []
    keys: list[KeyEvent] = []
    device = TouchDevice(
        path=TOUCH_DEV,
        abs_x=AbsAxis(0, 1079),
        abs_y=AbsAxis(0, 2399),
    )
    getevent.stream_events(
        "fake-serial",
        touches.append,
        keys.append,
        device_path=TOUCH_DEV,
        device=device,
    )
    # 设备过滤只放行触摸设备的事件，但 FakeProcess 同时给了按键设备行 → 解析器 device 过滤掉
    assert [event.down for event in touches] == [True, True, False]
    assert keys == []


def test_stream_events_requires_touch_device(monkeypatch) -> None:
    monkeypatch.setattr(getevent, "touch_device_detection", lambda serial: None)
    try:
        getevent.stream_events("fake-serial", None, None)
    except Exception as exc:  # ApiError
        assert getattr(exc, "code", "") == "DEVICE_UNAVAILABLE"
    else:  # pragma: no cover
        raise AssertionError("should raise DEVICE_UNAVAILABLE")


class BlockingProcess:
    """stdout 永远阻塞，只有 terminate 才释放，模拟无触摸时的 getevent。"""

    def __init__(self) -> None:
        self._released = threading.Event()
        self.stdout = self._iter()
        self.stdin = None
        self.terminated = False

    def _iter(self):
        self._released.wait(5.0)
        return
        yield  # pragma: no cover - 仅用于生成器语法

    def poll(self):
        return 0 if self._released.is_set() else None

    def wait(self, timeout=None):
        return 0

    def terminate(self) -> None:
        self.terminated = True
        self._released.set()

    def kill(self) -> None:
        self.terminated = True
        self._released.set()


def test_stream_events_stop_terminates_blocked_process(monkeypatch) -> None:
    fake = BlockingProcess()
    monkeypatch.setattr(getevent, "_start_getevent", lambda serial, path: fake)
    device = TouchDevice(
        path=TOUCH_DEV,
        abs_x=AbsAxis(0, 1079),
        abs_y=AbsAxis(0, 2399),
    )
    stop = threading.Event()
    results: list[str | None] = []

    def run() -> None:
        results.append(
            getevent.stream_events(
                "fake-serial",
                None,
                None,
                stop,
                device_path=TOUCH_DEV,
                device=device,
            )
        )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    stop.set()
    thread.join(3.0)
    assert not thread.is_alive()
    assert fake.terminated is True
    assert results == [None]


def test_stream_events_reports_unparsed_output(monkeypatch) -> None:
    # 有原始行，但缺少 SYN_REPORT，解析不出事件；用于把“格式变化”暴露给用户。
    fake = FakeProcess(["[  1.000000] 0003 0039 00000001"])
    monkeypatch.setattr(getevent, "_start_getevent", lambda serial, path: fake)
    device = TouchDevice(
        path=TOUCH_DEV,
        abs_x=AbsAxis(0, 1079),
        abs_y=AbsAxis(0, 2399),
    )
    result = getevent.stream_events(
        "fake-serial",
        None,
        None,
        device_path=TOUCH_DEV,
        device=device,
    )
    assert isinstance(result, str)
    assert "未解析" in result
