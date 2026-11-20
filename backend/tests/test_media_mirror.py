"""无真机测试：归一化坐标逆映射（4 旋转 + crop/max_size）与 MirrorSession 广播/背压/重启。"""

from __future__ import annotations

import asyncio
import time

import pytest

from app import mirror
from app.android import scrcpy
from app.android.scrcpy import VideoGeometry
from tests.test_media_h264 import annexb, build_idr, build_pps, build_sps

PORTRAIT_SPS = build_sps(1080, 1920)
PPS = build_pps()
KEY_PAYLOAD = annexb(PORTRAIT_SPS, PPS, build_idr())
SLICE_PAYLOAD = annexb(b"\x41\x01\x02\x03")
VIDEO_GEOMETRY = VideoGeometry(
    width=1080,
    height=1920,
    rotation=0,
    codec="avc1.64001F",
    sps=PORTRAIT_SPS,
    pps=PPS,
)
NATURAL = (1080, 1920)


@pytest.fixture(autouse=True)
def clean_sessions():
    yield
    mirror.shutdown_all()


def drain_queue(sub) -> list:
    items = []
    while not sub.queue.empty():
        items.append(sub.queue.get_nowait())
    return items


# ---------------------------------------------------------------- 纯函数：map_normalized
def geometry(rotation: int, crop: tuple[int, int, int, int] | None, stream=(800, 1600)) -> mirror.MirrorGeometry:
    return mirror.MirrorGeometry(
        stream_width=stream[0],
        stream_height=stream[1],
        natural_width=1080,
        natural_height=2400,
        rotation=rotation,
        crop=crop,
        max_size=800,
    )


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_map_normalized_with_crop_all_rotations(rotation: int) -> None:
    geo = geometry(rotation, crop=(100, 200, 800, 1600))
    origin = mirror.map_normalized(0.0, 0.0, geo)
    corner = mirror.map_normalized(1.0, 1.0, geo)
    expected = {
        0: ((100, 200), (900, 1800)),
        90: ((600, 100), (2200, 900)),
        180: ((180, 600), (980, 2200)),
        270: ((200, 180), (1800, 980)),
    }[rotation]
    assert origin == expected[0]
    assert corner == expected[1]


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (0, ((0, 0), (1079, 2399))),
        (90, ((0, 0), (2399, 1079))),
        (180, ((0, 0), (1079, 2399))),
        (270, ((0, 0), (2399, 1079))),
    ],
)
def test_map_normalized_no_crop_uses_display_size(rotation: int, expected) -> None:
    geo = geometry(rotation, crop=None, stream=(800, 1600))
    assert mirror.map_normalized(0.0, 0.0, geo) == expected[0]
    assert mirror.map_normalized(1.0, 1.0, geo) == expected[1]


def test_map_normalized_center_and_max_size_independence() -> None:
    geo = geometry(0, crop=None)
    assert mirror.map_normalized(0.5, 0.5, geo) == (round(0.5 * 1079), round(0.5 * 2399))
    smaller = mirror.MirrorGeometry(
        stream_width=400,
        stream_height=800,
        natural_width=1080,
        natural_height=2400,
        rotation=0,
        crop=None,
        max_size=800,
    )
    assert mirror.map_normalized(0.5, 0.5, smaller) == mirror.map_normalized(0.5, 0.5, geo)
    assert geo.scale() == pytest.approx(800 / 1080)
    cropped = geometry(0, crop=(0, 0, 400, 800), stream=(400, 800))
    assert cropped.scale() == pytest.approx(1.0)


def test_map_normalized_clamps_out_of_range() -> None:
    geo = geometry(0, crop=(0, 0, 1080, 2400))
    assert mirror.map_normalized(-1.0, -1.0, geo) == (0, 0)
    assert mirror.map_normalized(2.0, 2.0, geo) == (1079, 2399)


def test_map_normalized_rejects_bad_input() -> None:
    geo = geometry(0, crop=None)
    with pytest.raises(ValueError):
        mirror.map_normalized(float("nan"), 0.5, geo)
    with pytest.raises(ValueError):
        mirror.map_normalized("0.5", 0.5, geo)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        mirror.map_normalized(0.5, 0.5, mirror.MirrorGeometry())


@pytest.mark.parametrize(
    ("stream", "natural", "expected"),
    [
        ((1080, 2400), (1080, 2400), 0),
        ((2400, 1080), (1080, 2400), 90),
        ((1080, 1920), (1080, 2400), 0),  # aspect 不匹配（crop/max_size）→ 保守 0
    ],
)
def test_infer_rotation(stream, natural, expected) -> None:
    assert mirror.infer_rotation(stream[0], stream[1], natural[0], natural[1]) == expected


def test_parse_crop() -> None:
    assert mirror.parse_crop("800:1600:100:200") == (100, 200, 800, 1600)
    assert mirror.parse_crop("") is None
    with pytest.raises(ValueError):
        mirror.parse_crop("1:2:3")
    with pytest.raises(ValueError):
        mirror.parse_crop("0:100:0:0")


def test_normalize_rotation_accepts_quarter_turns() -> None:
    assert mirror.normalize_rotation(1) == 90
    assert mirror.normalize_rotation(2) == 180
    assert mirror.normalize_rotation(3) == 270
    assert mirror.normalize_rotation(270) == 270


# ---------------------------------------------------------------- 假 server / reader
class FakeReader:
    """循环发送固定帧；stop_event 置位后抛断线。"""

    def __init__(self, frames: list[tuple[int, bytes]], stop_event) -> None:
        self.frames = frames
        self.stop_event = stop_event
        self.index = 0

    def read_frame(self) -> tuple[int, bytes]:
        while not self.stop_event.is_set():
            frame = self.frames[self.index % len(self.frames)]
            self.index += 1
            time.sleep(0.002)  # 避免假 reader 空转把 CPU 打满
            return frame
        raise scrcpy.ScrcpyDisconnected("stopped")

    def close(self) -> None:
        pass


class FakeServer:
    def __init__(self, frames: list[tuple[int, bytes]], geometry: VideoGeometry = VIDEO_GEOMETRY) -> None:
        self.stop_event = __import__("threading").Event()
        self.geometry = geometry
        self.first_frame = frames[0]
        self.reader = FakeReader(frames[1:] or frames, self.stop_event)
        self.stopped = False
        self.key_requests = 0

    def request_keyframe(self) -> bool:
        self.key_requests += 1
        return True

    def stop(self) -> None:
        self.stopped = True
        self.stop_event.set()


def make_session(serial: str = "fake-1", *, frames=None) -> tuple[mirror.MirrorSession, list[FakeServer]]:
    frames = frames or [(1, KEY_PAYLOAD), (2, SLICE_PAYLOAD), (3, SLICE_PAYLOAD)]
    servers: list[FakeServer] = []

    def starter(_cfg):
        server = FakeServer(list(frames))
        servers.append(server)
        return server

    session = mirror.MirrorSession(serial, scrcpy.ServerConfig(), starter=starter, size_probe=lambda: NATURAL)
    return session, servers


# ---------------------------------------------------------------- 广播 / hello / stats
async def test_session_broadcast_and_hello() -> None:
    session, _servers = make_session()
    session.start()
    try:
        sub = session.subscribe(maxsize=32)
        await asyncio.sleep(0.1)
        items = drain_queue(sub)
        binaries = [item for item in items if isinstance(item, bytes)]
        assert binaries, "should receive video frames"
        pts_us, size = scrcpy.unpack_frame_header(binaries[0][:12])
        payload = binaries[0][12:]
        assert size == len(payload)
        assert payload in (KEY_PAYLOAD, SLICE_PAYLOAD)
        assert pts_us >= 1

        hello = session.hello_payload()
        assert hello["kind"] == "hello"
        assert hello["serial"] == "fake-1"
        assert hello["session_id"] == session.session_id
        assert hello["width"] == 1080
        assert hello["height"] == 1920
        assert hello["rotation"] == 0
        assert hello["codec"] == "avc1.64001F"
        assert hello["max_size"] == 1600
        assert hello["max_fps"] == 30
        assert hello["sps_b64"] and hello["pps_b64"]

        status = session.status()
        assert status["state"] == "running"
        assert status["width"] == 1080
        assert status["subscribers"] == 1

        stats = session.stats()
        assert stats["frames"] > 0
        assert stats["fps"] >= 0
        before = _servers[0].key_requests
        assert session.request_keyframe() is True
        assert _servers[0].key_requests == before + 1
        session.unsubscribe(sub)
        assert session.subscriber_count() == 0
    finally:
        session.stop()
    assert session.state == "stopped"
    assert _servers[0].stopped is True


# ---------------------------------------------------------------- 背压：丢到下一 IDR
async def test_backpressure_drops_to_next_idr_with_flush() -> None:
    session, _servers = make_session()
    session._state = "running"
    session._device_size = NATURAL
    session._video_geometry = VIDEO_GEOMETRY
    sub = session.subscribe(maxsize=2)
    try:
        session._ingest(1, KEY_PAYLOAD)  # config + 第一帧占满队列
        await asyncio.sleep(0.02)
        session._ingest(2, SLICE_PAYLOAD)
        session._ingest(3, SLICE_PAYLOAD)
        session._ingest(4, SLICE_PAYLOAD)
        await asyncio.sleep(0.02)
        assert sub.dropping is True
        dropped_mid = session.stats()["dropped"]
        assert dropped_mid >= 2

        session._ingest(5, KEY_PAYLOAD)  # 下一个 IDR → 清积压 + flush + 补发
        await asyncio.sleep(0.02)
        items = drain_queue(sub)
        assert items[0] == {"kind": "flush", "reason": "keyframe_drop"}
        binaries = [item for item in items if isinstance(item, bytes)]
        assert binaries
        pts_us, size = scrcpy.unpack_frame_header(binaries[-1][:12])
        assert pts_us == 5
        assert size == len(KEY_PAYLOAD)
        assert binaries[-1][12:] == KEY_PAYLOAD
        assert sub.dropping is False
        assert session.stats()["dropped"] > dropped_mid
    finally:
        session.stop()


async def test_control_message_survives_full_queue() -> None:
    session, _servers = make_session()
    session._state = "running"
    sub = session.subscribe(maxsize=1)
    try:
        session._deliver_control(sub, {"kind": "config", "width": 1080})
        session._deliver_control(sub, {"kind": "error", "code": "X", "message": "test"})
        await asyncio.sleep(0.02)
        items = drain_queue(sub)
        assert {"kind": "error", "code": "X", "message": "test"} in items
    finally:
        session.stop()


# ---------------------------------------------------------------- 断线重启
class InstantDisconnectReader:
    def read_frame(self) -> tuple[int, bytes]:
        raise scrcpy.ScrcpyDisconnected("boom")

    def close(self) -> None:
        pass


class DisconnectingServer:
    def __init__(self) -> None:
        self.geometry = VIDEO_GEOMETRY
        self.first_frame = (1, KEY_PAYLOAD)
        self.reader = InstantDisconnectReader()
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True

    def request_keyframe(self) -> bool:
        return False


async def test_restart_limit_reports_error(monkeypatch) -> None:
    # 留出订阅窗口，保证 error 控制消息能被订阅者收到
    monkeypatch.setattr(mirror, "RESTART_DELAY_S", 0.05)
    calls: list[DisconnectingServer] = []

    def starter(_cfg):
        server = DisconnectingServer()
        calls.append(server)
        return server

    session = mirror.MirrorSession("fake-restart", scrcpy.ServerConfig(), starter=starter, size_probe=lambda: NATURAL)
    session.start()
    try:
        sub = session.subscribe(maxsize=64)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and session.state != "error":
            await asyncio.sleep(0.02)
        assert session.state == "error"
        # state 由读线程先置位、error 控制消息经 call_soon_threadsafe 稍后投递
        await asyncio.sleep(0.05)
        assert len(calls) == mirror.MAX_RESTARTS_PER_WINDOW + 1  # 初次 + 3 次重启
        assert "自动重启超过" in (session.status()["error"] or "")
        items = drain_queue(sub)
        errors = [item for item in items if isinstance(item, dict) and item.get("kind") == "error"]
        assert errors and errors[0]["code"] == "SCRCPY_FAILED"
    finally:
        session.stop()


# ---------------------------------------------------------------- 模块级 registry / §14.3
async def test_module_registry_mapping_and_status() -> None:
    frames = [(1, KEY_PAYLOAD), (2, SLICE_PAYLOAD)]
    servers: list[FakeServer] = []

    def starter(_cfg):
        server = FakeServer(list(frames))
        servers.append(server)
        return server

    serial = "fake-registry"
    session = mirror.start_session(serial, starter=starter, size_probe=lambda: NATURAL)
    try:
        assert mirror.get_session(serial) is session
        status = mirror.mirror_status(serial)
        assert status["active"] is True
        assert status["width"] == 1080
        assert status["height"] == 1920
        assert status["rotation"] == 0
        coords = mirror.map_normalized_to_device(serial, 0.5, 0.5)
        assert coords == (540, 960)
        coords = mirror.map_normalized_to_device(serial, 0.0, 0.0)
        assert coords == (0, 0)
        # 无效坐标 → INVALID_ARGUMENT（调用方负责报错）
        from app.errors import ApiError

        with pytest.raises(ApiError) as exc:
            mirror.map_normalized_to_device(serial, float("nan"), 0.5)
        assert exc.value.code == "INVALID_ARGUMENT"
    finally:
        stopped = mirror.stop_session(serial)
    assert stopped == {"active": False, "serial": serial}
    assert mirror.map_normalized_to_device(serial, 0.5, 0.5) is None
    inactive = mirror.mirror_status(serial)
    assert inactive["active"] is False
    assert inactive["state"] == "idle"


async def test_start_session_reuses_running_session() -> None:
    frames = [(1, KEY_PAYLOAD)]
    servers: list[FakeServer] = []

    def starter(_cfg):
        server = FakeServer(list(frames))
        servers.append(server)
        return server

    serial = "fake-reuse"
    first = mirror.start_session(serial, starter=starter, size_probe=lambda: NATURAL)
    try:
        second = mirror.start_session(serial, starter=starter, size_probe=lambda: NATURAL)
        assert first is second
        assert len(servers) == 1
    finally:
        mirror.stop_session(serial)


async def test_start_session_starter_failure_cleans_registry() -> None:
    from app.errors import ApiError

    def starter(_cfg):
        raise ApiError("SCRCPY_FAILED", "boom")

    serial = "fake-fail"
    with pytest.raises(ApiError) as exc:
        mirror.start_session(serial, starter=starter, size_probe=lambda: NATURAL)
    assert exc.value.code == "SCRCPY_FAILED"
    assert mirror.get_session(serial) is None
