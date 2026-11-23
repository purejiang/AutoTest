"""录制会话：Web 录制、暂停丢弃、seq/event_count、stop 注销、物理手势归并。

全部使用 FakeDriver / 假 getevent 回调，不依赖真机。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import select

from app import config
from app.android.driver import FakeDriver
from app.db import session_scope
from app.models import Recording, RecordingEvent, Script, now_iso
from app.recorder import hooks
from app.recorder import session as rec

# ---------------------------------------------------------------- 夹具


@pytest.fixture(autouse=True)
def _cleanup_sessions():
    yield
    try:
        rec.shutdown_all()
    finally:
        config.reset_to_defaults()


@pytest.fixture()
def fake_driver() -> FakeDriver:
    return FakeDriver("fake-1")


@pytest.fixture()
def driver_getter(fake_driver: FakeDriver):
    def getter(serial: str) -> Any:
        return fake_driver

    return getter


def make_recording(
    temp_db,
    recording_id: str = "rec_test",
    serial: str = "fake-1",
    mode: str = "web",
    status: str = "recording",
) -> str:
    with session_scope() as db:
        db.add(
            Recording(
                id=recording_id,
                name="测试录制",
                mode=mode,
                serial=serial,
                status=status,
                started_at=now_iso(),
                event_count=0,
                meta={"mode": mode},
            )
        )
    return recording_id


def read_events(recording_id: str) -> list[RecordingEvent]:
    with session_scope() as db:
        return list(
            db.scalars(
                select(RecordingEvent)
                .where(RecordingEvent.recording_id == recording_id)
                .order_by(RecordingEvent.seq)
            )
        )


def read_recording(recording_id: str) -> Recording:
    with session_scope() as db:
        recording = db.get(Recording, recording_id)
        assert recording is not None
        return recording


# ---------------------------------------------------------------- Web 录制
def test_web_session_records_tap_and_swipe(temp_db, driver_getter) -> None:
    recording_id = make_recording(temp_db)
    sess = rec.create(recording_id, "fake-1", "web", driver_getter=driver_getter)
    assert isinstance(sess, rec.WebRecorderSession)
    assert hooks.active("fake-1") is sess

    pre = hooks.on_before_pointer("fake-1")
    assert isinstance(pre, dict) and pre["bounds"] == [0, 0, 1080, 2400]
    hooks.on_action("fake-1", "tap", {"x": 540, "y": 710, "duration_ms": 0}, pre)
    hooks.on_action(
        "fake-1",
        "swipe",
        {"from": {"x": 540, "y": 1800}, "to": {"x": 540, "y": 600}, "duration_ms": 250},
        None,
    )

    events = read_events(recording_id)
    assert [event.seq for event in events] == [1, 2]
    assert [event.kind for event in events] == ["tap", "swipe"]
    tap = events[0]
    assert tap.confidence == "high"
    assert tap.payload["x"] == 540 and tap.payload["y"] == 710
    assert tap.payload["node"] == {"by": "text", "value": "登录", "index": 0}
    assert tap.payload["target"] == tap.payload["node"]
    assert tap.payload["source"] == "web"
    swipe = events[1]
    assert swipe.payload["from"] == {"x": 540, "y": 1800}
    assert swipe.payload["to"] == {"x": 540, "y": 600}
    assert read_recording(recording_id).event_count == 2


def test_web_session_pause_drops_events_and_resume_continues(temp_db, driver_getter) -> None:
    recording_id = make_recording(temp_db)
    sess = rec.create(recording_id, "fake-1", "web", driver_getter=driver_getter)

    hooks.on_action("fake-1", "tap", {"x": 540, "y": 710}, None)
    rec.pause(recording_id)
    assert sess.paused is True
    assert read_recording(recording_id).status == "paused"
    hooks.on_action("fake-1", "tap", {"x": 540, "y": 710}, None)
    hooks.on_action("fake-1", "input", {"text": "dropped"}, None)
    assert [event.seq for event in read_events(recording_id)] == [1]

    rec.resume(recording_id)
    assert read_recording(recording_id).status == "recording"
    hooks.on_action("fake-1", "key", {"key": "back"}, None)
    events = read_events(recording_id)
    assert [event.seq for event in events] == [1, 2]
    assert events[1].kind == "key"
    assert events[1].payload["keycode"] == 4


def test_web_session_stop_unregisters_and_recomputes_count(temp_db, driver_getter) -> None:
    recording_id = make_recording(temp_db)
    sess = rec.create(recording_id, "fake-1", "web", driver_getter=driver_getter)
    hooks.on_action("fake-1", "tap", {"x": 540, "y": 710}, None)

    rec.pause(recording_id)
    hooks.on_action("fake-1", "tap", {"x": 540, "y": 710}, None)  # 丢弃 1 条
    rec.resume(recording_id)

    assert rec.stop(recording_id) is True
    assert rec.get(recording_id) is None
    assert hooks.active("fake-1") is None
    recording = read_recording(recording_id)
    assert recording.status == "stopped"
    assert recording.ended_at is not None
    assert recording.event_count == len(read_events(recording_id)) == 1
    assert recording.meta.get("dropped_while_paused") == 1
    assert getattr(sess, "dropped", 0) >= 1


def test_web_session_to_script_creates_mirror_script(temp_db, driver_getter) -> None:
    recording_id = make_recording(temp_db)
    rec.create(recording_id, "fake-1", "web", driver_getter=driver_getter)

    pre = hooks.on_before_pointer("fake-1")
    hooks.on_action("fake-1", "tap", {"x": 540, "y": 710, "duration_ms": 0}, pre)
    hooks.on_action("fake-1", "input", {"text": "hello"}, None)

    result = rec.to_script(recording_id, name="录制脚本A")
    assert result["id"].startswith("rec_")
    assert result["source"] == "mirror"
    assert result["origin_recording_id"] == recording_id
    assert [step["type"] for step in result["data"]["steps"]] == ["click", "input"]
    assert all(step["when"] is None for step in result["data"]["steps"])
    assert isinstance(result["warnings"], list)

    with session_scope() as db:
        script = db.get(Script, result["id"])
        assert script is not None
        assert script.source == "mirror"
        assert script.origin_recording_id == recording_id
        assert script.name == "录制脚本A"
    assert read_recording(recording_id).script_id == result["id"]

    # 事件被排除后只剩一个节点
    result2 = rec.to_script(recording_id, exclude_seqs=[1])
    assert [step["type"] for step in result2["data"]["steps"]] == ["input"]

    with pytest.raises(Exception) as excinfo:
        rec.to_script(recording_id, include_kinds=["nonexistent"])
    assert getattr(excinfo.value, "code", "") == "INVALID_ARGUMENT"


def test_physical_recording_to_script_maps_source(temp_db) -> None:
    recording_id = make_recording(temp_db, recording_id="rec_phys_script", mode="physical")
    with session_scope() as db:
        db.add(
            RecordingEvent(
                recording_id=recording_id,
                seq=1,
                kind="tap",
                payload={
                    "x": 540,
                    "y": 710,
                    "duration_ms": 0,
                    "node": {"by": "text", "value": "登录", "index": 0},
                    "target": {"by": "text", "value": "登录", "index": 0},
                    "source": "physical",
                },
                confidence="high",
                created_at=now_iso(),
            )
        )

    result = rec.to_script(recording_id, name="物理录制脚本")

    assert result["source"] == "physical"
    assert result["origin_recording_id"] == recording_id
    assert all(step["when"] is None for step in result["data"]["steps"])
    with session_scope() as db:
        script = db.get(Script, result["id"])
        assert script is not None
        assert script.source == "physical"
        assert script.origin_recording_id == recording_id
        assert db.get(Recording, recording_id).script_id == result["id"]


def test_web_session_note_manual_records_source(temp_db, driver_getter) -> None:
    recording_id = make_recording(temp_db)
    sess = rec.create(recording_id, "fake-1", "web", driver_getter=driver_getter)
    sess.note_manual("tap", {"x": 540, "y": 710})
    events = read_events(recording_id)
    assert len(events) == 1
    assert events[0].payload["source"] == "manual"


def test_create_is_idempotent(temp_db, driver_getter) -> None:
    recording_id = make_recording(temp_db)
    first = rec.create(recording_id, "fake-1", "web", driver_getter=driver_getter)
    second = rec.create(recording_id, "fake-1", "web", driver_getter=driver_getter)
    assert first is second


# ---------------------------------------------------------------- 物理录制
@dataclass
class FakeSample:
    t: float
    x: int
    y: int
    down: bool = True


@dataclass
class FakeKey:
    t: float
    keycode: int
    down: bool = True


def _physical_with_capture(recording_id: str = "rec_phys") -> tuple[rec.PhysicalRecorder, list]:
    recorder = rec.PhysicalRecorder(recording_id, "fake-1", driver_getter=lambda serial: None)
    captured: list[tuple[str, dict, str]] = []

    def capture(kind: str, payload: dict, confidence: str = "high") -> None:
        captured.append((kind, payload, confidence))

    recorder._record_event = capture  # type: ignore[method-assign]
    return recorder, captured


def test_physical_drag_records_timed_path(temp_db, monkeypatch) -> None:
    monkeypatch.setattr(rec, "_map_point", lambda serial, x, y, mapper=None, screen=None: (x, y))
    recorder, captured = _physical_with_capture()
    recorder._on_touch(FakeSample(100.0, 500, 1000, True))
    recorder._on_touch(FakeSample(100.2, 550, 1000, True))
    recorder._on_touch(FakeSample(100.4, 600, 1000, False))

    assert len(captured) == 1
    kind, payload, _confidence = captured[0]
    assert kind == "drag"
    assert payload["duration_ms"] == 400
    assert payload["path"] == [
        {"x": 500, "y": 1000, "dt_ms": 0},
        {"x": 550, "y": 1000, "dt_ms": 200},
        {"x": 600, "y": 1000, "dt_ms": 400},
    ]


def test_physical_circle_returns_to_start_still_drag(temp_db, monkeypatch) -> None:
    """物理画圆：终点回到起点，但轨迹范围足够大，不能被误判成 long_press。"""
    monkeypatch.setattr(rec, "_map_point", lambda serial, x, y, mapper=None, screen=None: (x, y))
    recorder, captured = _physical_with_capture()
    recorder._on_touch(FakeSample(100.0, 500, 1000, True))
    recorder._on_touch(FakeSample(100.1, 600, 1000, True))
    recorder._on_touch(FakeSample(100.2, 600, 1100, True))
    recorder._on_touch(FakeSample(100.3, 500, 1100, True))
    recorder._on_touch(FakeSample(100.4, 500, 1000, True))
    recorder._on_touch(FakeSample(100.5, 500, 1000, False))

    assert len(captured) == 1
    kind, payload, _confidence = captured[0]
    assert kind == "drag"
    assert payload["from"] == {"x": 500, "y": 1000}
    assert payload["to"] == {"x": 500, "y": 1000}
    assert len(payload["path"]) >= 5


@pytest.mark.parametrize(
    ("duration_s", "dx", "expected_kind"),
    [
        (0.499, 9, "tap"),
        (0.500, 9, "long_press"),
        (0.299, 10, "swipe"),
        (0.300, 10, "drag"),
        (0.150, 300, "swipe"),
        (0.900, 300, "drag"),
    ],
)
def test_physical_gesture_merge_boundaries(
    temp_db, monkeypatch, duration_s: float, dx: int, expected_kind: str
) -> None:
    monkeypatch.setattr(rec, "_map_point", lambda serial, x, y, mapper=None, screen=None: (x, y))
    recorder, captured = _physical_with_capture()
    recorder._on_touch(FakeSample(100.0, 540, 1200, True))
    recorder._on_touch(FakeSample(100.0 + duration_s, 540 + dx, 1200, False))

    assert len(captured) == 1
    kind, payload, _confidence = captured[0]
    assert kind == expected_kind
    assert payload["duration_ms"] == int(round(duration_s * 1000))
    assert payload["source"] == "physical"
    if expected_kind in ("tap", "long_press"):
        assert payload["x"] == 540 and payload["y"] == 1200
        assert payload["from"] is None
    else:
        assert payload["from"] == {"x": 540, "y": 1200}
        assert payload["to"] == {"x": 540 + dx, "y": 1200}


def test_physical_ignores_stray_move_without_down(temp_db, monkeypatch) -> None:
    monkeypatch.setattr(rec, "_map_point", lambda serial, x, y, mapper=None, screen=None: (x, y))
    recorder, captured = _physical_with_capture()
    recorder._on_touch(FakeSample(100.0, 10, 10, False))
    assert captured == []


def test_physical_key_events(temp_db) -> None:
    recorder, captured = _physical_with_capture()
    recorder._on_key(FakeKey(100.0, 158, down=True))  # getevent Linux KEY_BACK -> back
    recorder._on_key(FakeKey(100.1, 158, down=False))  # 抬起忽略
    recorder._on_key(FakeKey(100.2, 102, down=True))  # Linux KEY_HOME -> home
    recorder._on_key(FakeKey(100.3, 4, down=True))  # 兼容 Android keycode 4 -> back
    recorder._on_key(FakeKey(100.4, 9999, down=True))  # 未知键忽略
    assert [kind for kind, _payload, _confidence in captured] == ["key", "key", "key"]
    assert captured[0][1]["key"] == "back"
    assert captured[0][1]["keycode"] == 158
    assert captured[1][1]["key"] == "home"
    assert captured[2][1]["key"] == "back"


def test_physical_start_stop_consumes_stream_and_persists_meta(temp_db, monkeypatch) -> None:
    recording_id = make_recording(temp_db, recording_id="rec_phys2", serial="fake-9", mode="physical")
    monkeypatch.setattr(rec, "_probe_touch_device", lambda serial: {"touch_device": "/dev/input/event9"})
    started = threading.Event()

    def factory(serial, on_touch, on_key, stop_event) -> None:
        assert serial == "fake-9"
        started.set()
        while not stop_event.is_set():
            time.sleep(0.005)

    monkeypatch.setattr(rec, "_stream_events_factory", factory)
    recorder = rec.PhysicalRecorder(recording_id, "fake-9", driver_getter=lambda serial: None)
    recorder.start()
    try:
        assert started.wait(2.0)
        recorder.handle("tap", {"x": 1, "y": 1}, None)  # 物理模式忽略注入动作
        recorder._on_touch(FakeSample(1.0, 100, 200, True))
        recorder._on_touch(FakeSample(1.05, 100, 200, False))
        assert len(read_events(recording_id)) == 1
    finally:
        assert recorder.stop() is True
        assert recorder._thread is None
    events = read_events(recording_id)
    assert [event.kind for event in events] == ["tap"]
    recording = read_recording(recording_id)
    assert recording.meta.get("touch_device") == "/dev/input/event9"
    assert recording.meta.get("mode") == "physical"


# ---------------------------------------------------------------- 手动留痕去重 / 坐标映射 / 触摸设备探测
def test_note_manual_dedupes_with_preceding_handle(temp_db, driver_getter) -> None:
    recording_id = make_recording(temp_db)
    rec.create(recording_id, "fake-1", "web", driver_getter=driver_getter)
    # api 层对运行暂停期间的手动操作会同时调用 on_action + note_manual
    hooks.on_action("fake-1", "tap", {"x": 540, "y": 710}, None)
    hooks.note_manual("fake-1", "tap", {"x": 540, "y": 710})
    assert len(read_events(recording_id)) == 1

    hooks.note_manual("fake-1", "tap", {"x": 100, "y": 100})
    events = read_events(recording_id)
    assert len(events) == 2
    assert events[1].payload["source"] == "manual"


def test_map_point_normalized_to_device_pixels() -> None:
    class _Mapper:
        def apply(self, nx: float, ny: float):
            return int(nx * 200), int(ny * 100)

    # 归一化坐标（be-media TouchSample）-> 屏幕像素
    assert rec._map_point("fake-1", 0.5, 0.5, None, (1080, 2400)) == (540, 1200)
    # 有 TouchMapper 优先用标定/旋转结果
    assert rec._map_point("fake-1", 0.5, 0.5, _Mapper(), (1080, 2400)) == (100, 50)
    # 已是设备像素时原样返回
    assert rec._map_point("fake-1", 540, 710, None, (1080, 2400)) == (540, 710)


def test_probe_touch_device_adapts_be_media_api(temp_db, monkeypatch) -> None:
    from app.android import getevent
    from app.errors import ApiError

    class _Axis:
        def to_tuple(self):
            return (0, 1079)

    class _Device:
        path = "/dev/input/event2"
        name = "fake-touch"
        abs_x = _Axis()
        abs_y = _Axis()

    monkeypatch.setattr(getevent, "touch_device_detection", lambda serial: _Device())
    meta = rec._probe_touch_device("fake-1")
    assert meta["touch_device"] == "/dev/input/event2"
    assert meta["touch_device_name"] == "fake-touch"
    assert meta["abs_x"] == [0, 1079]

    monkeypatch.setattr(getevent, "touch_device_detection", lambda serial: None)
    with pytest.raises(ApiError) as excinfo:
        rec._probe_touch_device("fake-1")
    assert excinfo.value.code == "UNSUPPORTED"
    assert "触摸设备" in excinfo.value.message


def test_physical_normalized_touch_samples_map_to_device_pixels(temp_db) -> None:
    recorder, captured = _physical_with_capture()
    recorder._screen_size = (1080, 2400)  # be-media TouchSample 为 0..1 归一化坐标
    recorder._touch_mapper = None
    recorder._on_touch(FakeSample(2.0, 0.5, 0.5, True))
    recorder._on_touch(FakeSample(2.05, 0.5, 0.5, False))
    assert len(captured) == 1
    kind, payload, _confidence = captured[0]
    assert kind == "tap"
    assert (payload["x"], payload["y"]) == (540, 1200)
