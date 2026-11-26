"""无真机测试：api/mirror 的 REST（start/stop/status）与 WS（hello/帧/ping/token/错误）。

无 adb、无设备、无 scrcpy jar：错误路径必须给出清晰信封（4xx/5xx），不能 500。
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app import mirror as mirror_module
from app.android import scrcpy
from app.android.scrcpy import VideoGeometry
from app.api import mirror as api_mirror
from app.errors import ApiError, install_error_handlers
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


# ---------------------------------------------------------------- fixtures / helpers
@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(api_mirror, "_try_acquire", lambda serial: "")
    monkeypatch.setattr(api_mirror, "_try_release", lambda serial: None)
    app = FastAPI()
    app.include_router(api_mirror.router)
    install_error_handlers(app)
    with TestClient(app) as test_client:
        yield test_client
    mirror_module.shutdown_all()
    config.reset_to_defaults()


class FakeReader:
    def __init__(self, frames: list[tuple[int, bytes]], stop_event: threading.Event) -> None:
        self.frames = frames
        self.stop_event = stop_event
        self.index = 0

    def read_frame(self) -> tuple[int, bytes]:
        while not self.stop_event.is_set():
            frame = self.frames[self.index % len(self.frames)]
            self.index += 1
            return frame
        raise scrcpy.ScrcpyDisconnected("stopped")

    def close(self) -> None:
        pass


class FakeServer:
    def __init__(self, frames: list[tuple[int, bytes]] | None = None) -> None:
        self.frames = frames or [(1, KEY_PAYLOAD), (2, SLICE_PAYLOAD), (3, KEY_PAYLOAD)]
        self.stop_event = threading.Event()
        self.geometry = VIDEO_GEOMETRY
        self.first_frame = self.frames[0]
        self.reader = FakeReader(self.frames[1:] or self.frames, self.stop_event)
        self.key_requests = 0
        self.stopped = False

    def request_keyframe(self) -> bool:
        self.key_requests += 1
        return True

    def stop(self) -> None:
        self.stopped = True
        self.stop_event.set()


def start_fake_session(serial: str) -> tuple[mirror_module.MirrorSession, FakeServer]:
    server = FakeServer()

    def starter(_cfg):
        return server

    session = mirror_module.start_session(
        serial,
        starter=starter,
        size_probe=lambda: NATURAL,
    )
    return session, server


def receive_message(ws) -> tuple[str, object]:
    """TestClient 的 ws.receive() 返回的是 ASGI 原始消息（type=websocket.send）。"""
    message = ws.receive()
    text = message.get("text")
    if text is not None:
        return "text", json.loads(text)
    data = message.get("bytes")
    if data is not None:
        return "binary", data
    return receive_message(ws)


def wait_kind(ws, kind: str, limit: int = 500) -> dict:
    for _ in range(limit):
        message_type, payload = receive_message(ws)
        if message_type == "text" and payload.get("kind") == kind:
            return payload
    raise AssertionError(f"kind {kind!r} not received")


def recv_bytes(ws) -> bytes:
    for _ in range(500):
        message_type, payload = receive_message(ws)
        if message_type == "binary":
            return payload
    raise AssertionError("no binary message received")


# ---------------------------------------------------------------- REST
def test_status_inactive(client) -> None:
    response = client.get("/api/devices/ghost/mirror/status")
    assert response.status_code == 200
    body = response.json()
    assert body["active"] is False
    assert body["serial"] == "ghost"
    assert body["state"] == "idle"


def test_stop_is_idempotent(client) -> None:
    response = client.post("/api/devices/ghost/mirror/stop")
    assert response.status_code == 200
    assert response.json() == {"active": False, "serial": "ghost"}


def test_start_without_device_returns_clear_error(client, monkeypatch) -> None:
    def fake_start(serial, cfg=None, **_kwargs):
        raise ApiError("DEVICE_UNAVAILABLE", "no adb devices online", {"serial": serial})

    monkeypatch.setattr(mirror_module, "start_session", fake_start)
    response = client.post("/api/devices/ghost/mirror/start")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "DEVICE_UNAVAILABLE"
    assert "device" in body["error"]["message"].lower()


def test_start_scrcpy_failure_returns_502(client, monkeypatch) -> None:
    def fake_start(serial, cfg=None, **_kwargs):
        raise ApiError("SCRCPY_FAILED", "scrcpy server 启动失败", {"serial": serial})

    monkeypatch.setattr(mirror_module, "start_session", fake_start)
    response = client.post("/api/devices/ghost/mirror/start")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "SCRCPY_FAILED"


def test_start_unexpected_exception_becomes_scrpcpy_failed(client, monkeypatch) -> None:
    def fake_start(serial, cfg=None, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(mirror_module, "start_session", fake_start)
    response = client.post("/api/devices/ghost/mirror/start")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "SCRCPY_FAILED"


def test_start_rejects_unknown_options(client) -> None:
    response = client.post("/api/devices/ghost/mirror/start", json={"unknown": 1})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


def test_start_success_returns_status(client, monkeypatch) -> None:
    class StubSession:
        serial = "fake-ok"
        session_id = "session-42"

        def status(self):
            return {
                "active": True,
                "serial": self.serial,
                "state": "running",
                "width": 1080,
                "height": 1920,
                "rotation": 0,
                "codec": "avc1.64001F",
                "max_size": 1600,
                "max_fps": 30,
                "fps": 0.0,
                "dropped": 0,
            }

    monkeypatch.setattr(mirror_module, "start_session", lambda serial, cfg=None, **kw: StubSession())
    response = client.post("/api/devices/fake-ok/mirror/start", json={"max_size": 800, "max_fps": 15})
    assert response.status_code == 200
    body = response.json()
    assert body["active"] is True
    assert body["session_id"] == "session-42"
    assert body["width"] == 1080
    assert body["serial"] == "fake-ok"


def test_start_no_body_is_ok(client, monkeypatch) -> None:
    class StubSession:
        serial = "fake-nobody"
        session_id = "s"

        def status(self):
            return {"active": True, "serial": self.serial, "state": "running", "width": 1, "height": 1}

    monkeypatch.setattr(mirror_module, "start_session", lambda serial, cfg=None, **kw: StubSession())
    response = client.post("/api/devices/fake-nobody/mirror/start")
    assert response.status_code == 200


# ---------------------------------------------------------------- WS
def test_ws_hello_frames_ping_and_keyframe(client) -> None:
    serial = "fake-ws"
    session, server = start_fake_session(serial)
    try:
        with client.websocket_connect(f"/ws/mirror/{serial}") as ws:
            hello = wait_kind(ws, "hello")
            assert hello["serial"] == serial
            assert hello["session_id"] == session.session_id
            assert hello["width"] == 1080
            assert hello["height"] == 1920
            assert hello["rotation"] == 0
            assert hello["codec"] == "avc1.64001F"
            assert hello["max_size"] == 1632
            assert hello["max_fps"] == 0
            assert hello["sps_b64"] and hello["pps_b64"]

            frame = recv_bytes(ws)
            pts_us, size = scrcpy.unpack_frame_header(frame[:12])
            assert pts_us >= 1
            assert len(frame) == 12 + size
            assert size == len(frame) - 12

            ws.send_json({"kind": "ping"})
            pong = wait_kind(ws, "pong")
            assert pong["kind"] == "pong"

            # 新订阅者需要的 IDR 由 subscribe() 强制请求（客户端不必再自己要一次）
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and server.key_requests == 0:
                time.sleep(0.01)
            assert server.key_requests >= 1
    finally:
        mirror_module.stop_session(serial)
    assert server.stopped is True


def test_ws_keyframe_requests_are_coalesced(client, monkeypatch) -> None:
    """客户端 request_keyframe 走节流：open/hello/工具栏连发的多条只重启一次编码器（每次 100~300ms 不出帧）。"""
    serial = "fake-ws-keyframe"

    class SlowServer(FakeServer):
        """慢速出帧（50ms/帧），保证订阅队列不会溢出——丢帧路径也会请求关键帧，会干扰计数。"""

        def __init__(self) -> None:
            super().__init__()
            self.index = 0
            self.reader = self  # type: ignore[assignment]

        def read_frame(self) -> tuple[int, bytes]:
            if self.stop_event.wait(0.05):
                raise scrcpy.ScrcpyDisconnected("stopped")
            frame = self.frames[self.index % len(self.frames)]
            self.index += 1
            return frame

    server = SlowServer()
    real_start = mirror_module.start_session  # 先保存原函数，避免 monkeypatch 递归
    monkeypatch.setattr(
        mirror_module,
        "start_session",
        lambda s, cfg=None, **kw: real_start(s, starter=lambda _cfg: server, size_probe=lambda: NATURAL),
    )
    monkeypatch.setattr(mirror_module.MirrorSession, "_KEYFRAME_MIN_INTERVAL_S", 0.8)
    try:
        with client.websocket_connect(f"/ws/mirror/{serial}") as ws:
            wait_kind(ws, "hello")
            recv_bytes(ws)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and server.key_requests < 1:
                time.sleep(0.01)
            base = server.key_requests
            for _ in range(3):
                ws.send_json({"kind": "request_keyframe"})
            time.sleep(0.2)
            assert server.key_requests == base, "节流窗口内的连发请求不应再次重启编码器"

            monkeypatch.setattr(mirror_module.MirrorSession, "_KEYFRAME_MIN_INTERVAL_S", 0.05)
            time.sleep(0.1)
            ws.send_json({"kind": "request_keyframe"})
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and server.key_requests <= base:
                time.sleep(0.01)
            assert server.key_requests == base + 1, "过了节流窗口的请求应放行"
    finally:
        mirror_module.stop_session(serial)


def test_ws_auto_starts_session(client, monkeypatch) -> None:
    serial = "fake-ws-autostart"
    server = FakeServer()
    real_start = mirror_module.start_session  # 先保存原函数，避免 monkeypatch 递归

    def fake_start(session_serial, cfg=None, **_kwargs):
        return real_start(
            session_serial,
            starter=lambda _cfg: server,
            size_probe=lambda: NATURAL,
        )

    monkeypatch.setattr(mirror_module, "start_session", fake_start)
    try:
        with client.websocket_connect(f"/ws/mirror/{serial}") as ws:
            hello = wait_kind(ws, "hello")
            assert hello["serial"] == serial
            frame = recv_bytes(ws)
            assert len(frame) > 12
    finally:
        mirror_module.stop_session(serial)


def test_ws_without_device_sends_error_frame(client, monkeypatch) -> None:
    def fake_start(serial, cfg=None, **_kwargs):
        raise ApiError("DEVICE_UNAVAILABLE", "no adb devices online", {"serial": serial})

    monkeypatch.setattr(mirror_module, "start_session", fake_start)
    with client.websocket_connect("/ws/mirror/ghost") as ws:
        error = wait_kind(ws, "error")
        assert error["code"] == "DEVICE_UNAVAILABLE"
        assert "device" in error["message"].lower()


def test_ws_rejects_bad_token(client) -> None:
    config.set_file_layer({"security.token": "secret-token"})
    try:
        with client.websocket_connect("/ws/mirror/ghost?token=wrong") as ws:
            error = wait_kind(ws, "error")
            assert error["code"] == "UNAUTHORIZED"
    finally:
        config.reset_to_defaults()


def test_ws_accepts_valid_token(client, monkeypatch) -> None:
    config.set_file_layer({"security.token": "secret-token"})
    serial = "fake-ws-token"
    server = FakeServer()
    real_start = mirror_module.start_session  # 先保存原函数，避免 monkeypatch 递归

    def fake_start(session_serial, cfg=None, **_kwargs):
        return real_start(
            session_serial,
            starter=lambda _cfg: server,
            size_probe=lambda: NATURAL,
        )

    monkeypatch.setattr(mirror_module, "start_session", fake_start)
    try:
        with client.websocket_connect(f"/ws/mirror/{serial}?token=secret-token") as ws:
            hello = wait_kind(ws, "hello")
            assert hello["kind"] == "hello"
    finally:
        mirror_module.stop_session(serial)
        config.reset_to_defaults()


def test_ws_close_releases_stream_touch(client, monkeypatch) -> None:
    """WS 在 pointerup 之前断开：服务端必须为该 owner 补一个 UP。"""
    serial = "fake-ws-touch"
    start_fake_session(serial)
    calls: list[tuple[str, float, float]] = []

    monkeypatch.setattr(
        mirror_module,
        "inject_touch",
        lambda _serial, action, nx, ny: calls.append((action, nx, ny)) or True,
    )
    try:
        with client.websocket_connect(f"/ws/mirror/{serial}") as ws:
            wait_kind(ws, "hello")
            ws.send_json({"kind": "touch", "action": "down", "nx": 0.25, "ny": 0.75})
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not any(item[0] == "down" for item in calls):
                time.sleep(0.01)
            assert any(item[0] == "down" for item in calls)

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not any(item[0] == "up" for item in calls):
            time.sleep(0.01)
        assert any(item[0] == "up" for item in calls)
        up = [item for item in calls if item[0] == "up"][-1]
        assert up[1:] == (0.25, 0.75)
    finally:
        mirror_module.stop_session(serial)


def test_ws_touch_down_move_up_keep_order_on_fast_path(client, monkeypatch) -> None:
    """没有 run 占用、没有活跃录制时，down/up 与 move 一样在事件循环里同步注入：严格有序，
    且紧随 down 的 move 不会因为线程池排队被当成“无配对手势”丢掉。"""
    from app.api import devices as api_devices
    from app.recorder import hooks
    from app.services import device_registry

    serial = "fake-ws-order"
    start_fake_session(serial)
    calls: list[tuple[str, float, float]] = []
    monkeypatch.setattr(
        mirror_module,
        "inject_touch",
        lambda _serial, action, nx, ny: calls.append((action, nx, ny)) or True,
    )
    assert api_devices.stream_touch_is_fast(serial) is True
    try:
        with client.websocket_connect(f"/ws/mirror/{serial}") as ws:
            wait_kind(ws, "hello")
            ws.send_json({"kind": "touch", "action": "down", "nx": 0.1, "ny": 0.1})
            ws.send_json({"kind": "touch", "action": "move", "nx": 0.2, "ny": 0.2})
            ws.send_json({"kind": "touch", "action": "move", "nx": 0.3, "ny": 0.3})
            ws.send_json({"kind": "touch", "action": "up", "nx": 0.3, "ny": 0.3})
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not any(item[0] == "up" for item in calls):
                time.sleep(0.01)
        assert [item[0] for item in calls] == ["down", "move", "move", "up"]
    finally:
        mirror_module.stop_session(serial)

    # 录制中 / run 占用：down、up 要抓层级或查库 → 不走快路径（move 仍内联）
    class FakeRecorder:
        recording_id = "rec-1"

        def before_pointer(self):
            return None

        def handle(self, *_args, **_kwargs):
            return None

        def note_manual(self, *_args, **_kwargs):
            return None

    hooks.register(serial, FakeRecorder())
    try:
        assert api_devices.stream_touch_is_fast(serial) is False
    finally:
        hooks.unregister(serial)
    device_registry.acquire(serial, "run", 77)
    try:
        assert api_devices.stream_touch_is_fast(serial) is False
    finally:
        device_registry.release(serial, "run", 77)
    assert api_devices.stream_touch_is_fast(serial) is True


def test_post_mirror_diag_writes_jsonl(client) -> None:
    """浏览器上报的采样要原样落盘（data/diag/mirror-*.jsonl，一行一个 JSON）。"""
    from app import diagnostics

    response = client.post(
        "/api/devices/fake-diag/mirror/diag",
        json={
            "session_id": "sess-1",
            "width": 598,
            "height": 1334,
            "render_fps": 57.6,
            "server_fps": 111.0,
            "draw_ms": 2.34,
            "queue_size": 1,
            "dropped": 3,
            "max_fps": 0,
            "decoder_mode": "annexb",
            "awaiting_keyframe": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["stored"] is True

    path = diagnostics.current_file()
    lines = [json.loads(item) for item in path.read_text(encoding="utf-8").splitlines() if item]
    assert len(lines) == 1
    sample = lines[0]
    assert sample["source"] == "client"
    assert sample["serial"] == "fake-diag"
    assert sample["session_id"] == "sess-1"
    assert sample["render_fps"] == 57.6
    assert sample["server_fps"] == 111.0
    assert sample["draw_ms"] == 2.34
    assert sample["queue_size"] == 1
    assert sample["decoder_mode"] == "annexb"
    assert sample["ts"].endswith("+00:00")


def test_post_mirror_diag_rejects_bad_values(client) -> None:
    """离谱的数值直接 422，不让脏数据进分析文件。"""
    assert client.post(
        "/api/devices/fake-diag/mirror/diag", json={"render_fps": 9999}
    ).status_code == 422
    assert client.post(
        "/api/devices/fake-diag/mirror/diag", json={"queue_size": -1}
    ).status_code == 422


def test_post_mirror_diag_ignores_unknown_fields(client) -> None:
    """诊断上报要向前兼容：新前端多带的字段不能让整条样本 422（否则日志里完全静默）。"""
    response = client.post(
        "/api/devices/fake-diag/mirror/diag",
        json={"render_fps": 55.0, "phase": "streaming", "display_hz": 60, "brand_new_field": 1},
    )
    assert response.status_code == 200, response.text
    assert response.json()["stored"] is True
