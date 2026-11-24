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
            assert hello["max_size"] == 1600
            assert hello["max_fps"] == 30
            assert hello["sps_b64"] and hello["pps_b64"]

            frame = recv_bytes(ws)
            pts_us, size = scrcpy.unpack_frame_header(frame[:12])
            assert pts_us >= 1
            assert len(frame) == 12 + size
            assert size == len(frame) - 12

            ws.send_json({"kind": "ping"})
            pong = wait_kind(ws, "pong")
            assert pong["kind"] == "pong"

            ws.send_json({"kind": "request_keyframe"})
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and server.key_requests == 0:
                time.sleep(0.01)
            assert server.key_requests >= 1
    finally:
        mirror_module.stop_session(serial)
    assert server.stopped is True


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
    config.set_db_overrides({"security.token": "secret-token"})
    try:
        with client.websocket_connect("/ws/mirror/ghost?token=wrong") as ws:
            error = wait_kind(ws, "error")
            assert error["code"] == "UNAUTHORIZED"
    finally:
        config.reset_to_defaults()


def test_ws_accepts_valid_token(client, monkeypatch) -> None:
    config.set_db_overrides({"security.token": "secret-token"})
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
