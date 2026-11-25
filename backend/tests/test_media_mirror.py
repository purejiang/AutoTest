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
    """只取二进制帧队列（跳过控制通道唤醒哨兵）。"""
    items = []
    while not sub.queue.empty():
        item = sub.queue.get_nowait()
        if item is mirror.WAKE_SENTINEL:
            continue
        items.append(item)
    return items


def drain_outgoing(sub) -> list:
    """取控制消息（优先）+ 二进制帧，供断言使用。"""
    items = list(sub.controls)
    sub.controls.clear()
    items.extend(drain_queue(sub))
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
    frames = frames or [(1, KEY_PAYLOAD), (2, SLICE_PAYLOAD), (3, SLICE_PAYLOAD), (4, KEY_PAYLOAD)]
    servers: list[FakeServer] = []

    def starter(_cfg):
        server = FakeServer(list(frames))
        servers.append(server)
        return server

    session = mirror.MirrorSession(serial, scrcpy.ServerConfig(), starter=starter, size_probe=lambda: NATURAL)
    return session, servers


# ---------------------------------------------------------------- 按设备缩放
def test_start_resolves_max_size_from_device_long_edge() -> None:
    captured: dict[str, scrcpy.ServerConfig] = {}

    def starter(cfg):
        captured["cfg"] = cfg
        return FakeServer([(1, KEY_PAYLOAD)])

    cfg = scrcpy.ServerConfig(scale_pct=70, max_size=1440)
    session = mirror.MirrorSession(
        "scaled", cfg, starter=starter, size_probe=lambda: (1080, 1920)
    )
    session.start()
    try:
        # 1920 * 70% = 1344，低于 1440 上限
        assert captured["cfg"].max_size == 1344
        assert session.hello_payload()["max_size"] == 1344
    finally:
        session.stop()


def test_start_falls_back_to_cap_when_device_size_unknown() -> None:
    captured: dict[str, scrcpy.ServerConfig] = {}

    def starter(cfg):
        captured["cfg"] = cfg
        return FakeServer([(1, KEY_PAYLOAD)])

    cfg = scrcpy.ServerConfig(scale_pct=70, max_size=1440)
    session = mirror.MirrorSession(
        "scaled-unknown", cfg, starter=starter, size_probe=lambda: (0, 0)
    )
    session.start()
    try:
        assert captured["cfg"].max_size == 1440
        assert session.hello_payload()["max_size"] == 1440
    finally:
        session.stop()


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
        assert hello["max_size"] == 1920
        assert hello["max_fps"] == 0
        assert hello["sps_b64"] and hello["pps_b64"]

        status = session.status()
        assert status["state"] == "running"
        assert status["width"] == 1080
        assert status["subscribers"] == 1

        stats = session.stats()
        assert stats["frames"] > 0
        assert stats["fps"] >= 0
        before = _servers[0].key_requests
        # 显式请求用 force（UI 按钮语义）；默认调用受 0.8s 节流保护，防止前端循环重置编码器
        assert session.request_keyframe(force=True) is True
        assert _servers[0].key_requests == before + 1
        assert session.request_keyframe() is False, "节流应拦住紧邻的重复请求"
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
        assert {"kind": "flush", "reason": "keyframe_drop"} in sub.controls
        binaries = drain_queue(sub)
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
        assert {"kind": "config", "width": 1080} in sub.controls
        assert {"kind": "error", "code": "X", "message": "test"} in sub.controls
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
        errors = [item for item in sub.controls if isinstance(item, dict) and item.get("kind") == "error"]
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


# ---------------------------------------------------------------- v5 CONFIG 包 / session meta
async def test_config_packet_prepended_to_next_frame() -> None:
    session, _servers = make_session()
    session._state = "running"
    session._device_size = NATURAL
    session._video_geometry = VIDEO_GEOMETRY
    sub = session.subscribe(maxsize=16)
    config_payload = annexb(PORTRAIT_SPS, PPS)
    try:
        # CONFIG 包：更新 SPS/PPS + 广播 config，但不能当视频帧下发
        config_info = scrcpy.FrameInfo(
            pts_us=0,
            size=len(config_payload),
            config=True,
            geometry=VIDEO_GEOMETRY,
        )
        session._ingest(0, config_payload, config_info)
        await asyncio.sleep(0.02)
        assert not [item for item in drain_queue(sub) if isinstance(item, bytes)]

        # 下一视频帧：payload 前拼 in-band SPS/PPS
        key_info = scrcpy.FrameInfo(
            pts_us=42,
            size=len(KEY_PAYLOAD),
            keyframe=True,
            geometry=VIDEO_GEOMETRY,
        )
        session._ingest(42, KEY_PAYLOAD, key_info)
        await asyncio.sleep(0.02)
        binaries = [item for item in drain_queue(sub) if isinstance(item, bytes)]
        assert binaries
        pts_us, size = scrcpy.unpack_frame_header(binaries[-1][:12])
        assert pts_us == 42
        assert binaries[-1][12:] == config_payload + KEY_PAYLOAD
        assert size == len(config_payload) + len(KEY_PAYLOAD)
    finally:
        session.stop()


async def test_session_meta_updates_geometry_without_frame() -> None:
    session, _servers = make_session()
    session._state = "running"
    session._device_size = NATURAL
    session._video_geometry = VIDEO_GEOMETRY
    sub = session.subscribe(maxsize=16)
    try:
        rotated = scrcpy.VideoGeometry(
            width=1920,
            height=1080,
            codec=VIDEO_GEOMETRY.codec,
            sps=VIDEO_GEOMETRY.sps,
            pps=VIDEO_GEOMETRY.pps,
        )
        session._video_geometry = rotated
        meta_info = scrcpy.FrameInfo(
            pts_us=0,
            size=0,
            session_meta=True,
            geometry=rotated,
        )
        session._ingest(0, b"", meta_info)
        await asyncio.sleep(0.02)
        assert not drain_queue(sub)
        configs = [item for item in sub.controls if isinstance(item, dict) and item.get("kind") == "config"]
        assert configs
        assert (session.geometry.stream_width, session.geometry.stream_height) == (1920, 1080)
        assert session.geometry.rotation == 90
    finally:
        session.stop()


async def test_adopt_server_seeds_codec_config_for_inband() -> None:
    """启动自检消费掉的 CONFIG 包要在首帧前补发（真机 full 模式的实际情况）。"""
    class Reader:
        codec_config = annexb(PORTRAIT_SPS, PPS)
        last_info = scrcpy.FrameInfo(
            pts_us=7,
            size=len(KEY_PAYLOAD),
            keyframe=True,
            geometry=VIDEO_GEOMETRY,
        )

    class Starter:
        geometry = VIDEO_GEOMETRY
        first_frame = (7, KEY_PAYLOAD)
        reader = Reader()

        def stop(self) -> None:
            pass

    session = mirror.MirrorSession("fake-seed", scrcpy.ServerConfig(), size_probe=lambda: NATURAL)
    session._state = "running"
    sub = session.subscribe(maxsize=8)
    try:
        session._adopt_server(Starter())
        await asyncio.sleep(0.02)
        binaries = [item for item in drain_queue(sub) if isinstance(item, bytes)]
        assert binaries
        assert binaries[-1][12:] == annexb(PORTRAIT_SPS, PPS) + KEY_PAYLOAD
        assert session._pending_config == b""
    finally:
        session.stop()


async def test_keyframe_flush_preserves_control_messages() -> None:
    """背压补发 IDR 时清掉旧帧，但 pong/error 等控制消息不能丢。"""
    session, _servers = make_session()
    session._state = "running"
    sub = session.subscribe(maxsize=3)
    sub.awaiting_keyframe = False  # 模拟已在解码中的老订阅者
    try:
        session._deliver_frame_in_loop(sub, scrcpy.pack_frame(1, SLICE_PAYLOAD), False)
        session._deliver_frame_in_loop(sub, scrcpy.pack_frame(2, SLICE_PAYLOAD), False)
        session._deliver_frame_in_loop(sub, scrcpy.pack_frame(3, SLICE_PAYLOAD), False)
        session._deliver_control_in_loop(sub, {"kind": "pong"})
        session._deliver_frame_in_loop(sub, scrcpy.pack_frame(4, KEY_PAYLOAD), True)
        assert {"kind": "pong"} in sub.controls
        assert {"kind": "flush", "reason": "keyframe_drop"} in sub.controls
        binaries = drain_queue(sub)
        assert binaries and binaries[-1][12:] == KEY_PAYLOAD
    finally:
        session.stop()


def test_scrcpy_inject_path_keeps_move_timings(monkeypatch) -> None:
    """scrcpy 轨迹注入：DOWN → 逐点 MOVE（按 dt 等待）→ 等到 duration 再 UP。"""
    server = object.__new__(scrcpy.ScrcpyServer)
    sent: list[bytes] = []
    sleeps: list[float] = []
    clock = [0.0]

    monkeypatch.setattr(server, "send_control", lambda payload: sent.append(payload) or True)
    monkeypatch.setattr(scrcpy.time, "monotonic", lambda: clock[0])

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(scrcpy.time, "sleep", fake_sleep)

    samples = [(0, 10, 20), (100, 50, 60), (300, 70, 80)]
    assert server.inject_path(samples, 500, 1080, 1920) is True

    assert [payload[1] for payload in sent] == [
        scrcpy.MOTION_DOWN,
        scrcpy.MOTION_MOVE,
        scrcpy.MOTION_MOVE,
        scrcpy.MOTION_UP,
    ]
    # 100ms、200ms 两段移动 + 最后停到 500ms（last dt=300ms）
    assert len(sleeps) == 3
    assert sum(sleeps) == pytest.approx(0.5, abs=0.05)


def test_scrcpy_inject_path_abort_sends_up(monkeypatch) -> None:
    """stop/超时打断轨迹回放：必须补一个 UP，并抛 InjectionAborted 让调用方不要回退。"""
    server = object.__new__(scrcpy.ScrcpyServer)
    sent: list[bytes] = []
    clock = [0.0]

    monkeypatch.setattr(server, "send_control", lambda payload: sent.append(payload) or True)
    monkeypatch.setattr(scrcpy.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(scrcpy.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    # DOWN 之后、下一段 MOVE 之前取消（模拟 stop）
    def should_abort() -> bool:
        return len(sent) >= 1

    with pytest.raises(scrcpy.InjectionAborted):
        server.inject_path(
            [(0, 10, 20), (1000, 50, 60)],
            1000,
            1080,
            1920,
            should_abort=should_abort,
        )

    actions = [payload[1] for payload in sent]
    assert actions[0] == scrcpy.MOTION_DOWN
    assert actions[-1] == scrcpy.MOTION_UP


def test_scrcpy_inject_path_abort_before_down_sends_nothing(monkeypatch) -> None:
    server = object.__new__(scrcpy.ScrcpyServer)
    sent: list[bytes] = []
    monkeypatch.setattr(server, "send_control", lambda payload: sent.append(payload) or True)

    with pytest.raises(scrcpy.InjectionAborted):
        server.inject_path([(0, 10, 20), (100, 50, 60)], 100, 1080, 1920, should_abort=lambda: True)
    assert sent == []


def test_perf_log_emits_once_per_interval(caplog) -> None:
    """运行期每 10s 打一行可 grep 的性能日志（fps/丢帧/订阅者/发送队列/几何/帧率上限）。

    没有这行日志时，“不跟手”只能靠猜是编码、传输还是浏览器解码慢。
    """
    import logging

    session, _servers = make_session()
    with caplog.at_level(logging.INFO, logger=mirror.logger.name):
        # 首帧：_last_perf_log 从 0 起，必然满足间隔 → 打一条
        session._ingest(1, KEY_PAYLOAD)
        perf_lines = [r for r in caplog.records if "mirror perf:" in r.getMessage()]
        assert len(perf_lines) == 1, [r.getMessage() for r in caplog.records]

        text = perf_lines[0].getMessage()
        for expected in ("serial=fake-1", "fps=", "dropped=0", "subscribers=0", "queues=[]", "stream="):
            assert expected in text, text
        assert f"max_fps={session.cfg.max_fps}" in text

        # 同一间隔内的后续帧不再重复打点（10s 一条，避免刷屏）
        session._ingest(2, SLICE_PAYLOAD)
        session._ingest(3, SLICE_PAYLOAD)
        perf_lines = [r for r in caplog.records if "mirror perf:" in r.getMessage()]
        assert len(perf_lines) == 1

        # 把打点时间往前推 → 下一条立刻可打
        with session._lock:
            session._last_perf_log = 0.0
        session._ingest(4, KEY_PAYLOAD)
        perf_lines = [r for r in caplog.records if "mirror perf:" in r.getMessage()]
        assert len(perf_lines) == 2

    # 有订阅者时把发送队列积压也带出来（积压大 = 浏览器消费慢）
    assert "queues=" in perf_lines[-1].getMessage()


def test_perf_log_also_persists_jsonl_sample() -> None:
    """后端 perf 打点同时写一条 source=server 的 JSONL，供分析脚本和浏览器样本按时间对齐。"""
    import json

    from app import diagnostics

    session, _servers = make_session()
    session._ingest(1, KEY_PAYLOAD)
    session._ingest(2, SLICE_PAYLOAD)

    path = diagnostics.current_file()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    server_rows = [row for row in rows if row["source"] == "server"]
    assert len(server_rows) == 1, rows
    row = server_rows[0]
    assert row["serial"] == "fake-1"
    assert row["session_id"] == session.session_id
    # 未 start 的会话没有 video geometry（0x0），这里只校验字段齐全、类型正确
    assert isinstance(row["width"], int) and isinstance(row["height"], int)
    assert row["subscribers"] == 0
    assert row["queue_depths"] == []
    assert row["max_fps"] == session.cfg.max_fps
    assert "server_fps" in row
