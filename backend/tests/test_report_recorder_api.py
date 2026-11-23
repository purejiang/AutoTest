"""录制 REST + WS（契约 §7.4 / §6.3）：TestClient + FakeDriver 桩设备注册表。"""

from __future__ import annotations

import types
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.android.driver import FakeDriver
from app.api import recorder as recorder_api
from app.api import reports as reports_api
from app.errors import ApiError, install_error_handlers
from app.recorder import hooks
from app.recorder import session as rec


@pytest.fixture()
def api(monkeypatch, tmp_path, temp_db):
    config.reset_to_defaults()
    config.set_db_overrides({"runtime.data_dir": str(tmp_path / "data")})
    drivers: dict[str, FakeDriver] = {}
    acquired: list[tuple[str, str]] = []
    released: list[tuple[str, str]] = []

    def get_driver(serial: str) -> FakeDriver:
        return drivers.setdefault(str(serial), FakeDriver(str(serial)))

    stub = types.SimpleNamespace(
        get_driver=get_driver,
        occupancy=lambda serial: [],
        acquire=lambda serial, purpose, ref_id: None,
        release=lambda serial, purpose, ref_id: None,
    )
    monkeypatch.setattr(recorder_api, "_registry", lambda: stub)
    monkeypatch.setattr(rec, "_default_driver_getter", get_driver)
    monkeypatch.setattr(
        recorder_api,
        "_acquire_device",
        lambda serial, recording_id: acquired.append((serial, recording_id)),
    )
    monkeypatch.setattr(
        recorder_api,
        "_release_device",
        lambda serial, recording_id: released.append((serial, recording_id)),
    )
    monkeypatch.setattr(
        rec,
        "_release_device",
        lambda serial, recording_id: released.append((serial, recording_id)),
    )

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(recorder_api.router)
    app.include_router(reports_api.router)
    client = TestClient(app)
    yield types.SimpleNamespace(
        client=client,
        drivers=drivers,
        acquired=acquired,
        released=released,
        stub=stub,
    )
    rec.shutdown_all()
    config.reset_to_defaults()


def _create(client: TestClient, serial: str = "fake-1", mode: str = "web", name: str | None = None) -> dict:
    body: dict[str, Any] = {"serial": serial, "mode": mode}
    if name:
        body["name"] = name
    response = client.post("/api/recordings", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------- CRUD
def test_create_list_get_pause_resume_stop(api) -> None:
    recording = _create(api.client, name="我的录制")
    assert recording["name"] == "我的录制"
    assert recording["serial"] == "fake-1"
    assert recording["mode"] == "web"
    assert recording["status"] == "recording"
    assert recording["event_count"] == 0
    assert recording["script_id"] is None
    assert recording["script_name"] is None
    assert api.acquired and api.acquired[0][0] == "fake-1"

    listing = api.client.get("/api/recordings").json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == recording["id"]
    assert api.client.get("/api/recordings?mode=web").json()["total"] == 1
    assert api.client.get("/api/recordings?mode=physical").json()["total"] == 0
    paged = api.client.get("/api/recordings?limit=1&offset=1").json()
    assert paged["total"] == 1
    assert paged["items"] == []

    paused = api.client.post(f"/api/recordings/{recording['id']}/pause").json()
    assert paused["status"] == "paused"
    assert api.client.get("/api/recordings?status=paused").json()["total"] == 1
    resumed = api.client.post(f"/api/recordings/{recording['id']}/resume").json()
    assert resumed["status"] == "recording"

    hooks.on_action("fake-1", "tap", {"x": 540, "y": 710}, None)
    hooks.on_action("fake-1", "key", {"key": "back"}, None)
    stopped = api.client.post(f"/api/recordings/{recording['id']}/stop").json()
    assert stopped["status"] == "stopped"
    assert stopped["event_count"] == 2
    assert stopped["ended_at"] is not None
    assert hooks.active("fake-1") is None
    assert api.released
    assert api.client.get(f"/api/recordings/{recording['id']}").status_code == 200


def test_create_requires_serial(api) -> None:
    response = api.client.post("/api/recordings", json={"serial": "", "mode": "web"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


def test_duplicate_active_recording_conflict(api) -> None:
    _create(api.client)
    response = api.client.post("/api/recordings", json={"serial": "fake-1", "mode": "web"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_device_busy_when_run_occupies(api, monkeypatch) -> None:
    monkeypatch.setattr(api.stub, "occupancy", lambda serial: [{"purpose": "run", "ref_id": "run-7"}])
    response = api.client.post("/api/recordings", json={"serial": "fake-1", "mode": "web"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DEVICE_BUSY"


def test_device_unavailable_when_driver_missing(api, monkeypatch) -> None:
    def broken(serial: str):
        raise RuntimeError("adb offline")

    monkeypatch.setattr(api.stub, "get_driver", broken)
    response = api.client.post("/api/recordings", json={"serial": "fake-1", "mode": "web"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEVICE_UNAVAILABLE"


def test_physical_mode_requires_touch_device(api, monkeypatch) -> None:
    def unsupported(serial: str):
        raise ApiError("UNSUPPORTED", "无法识别触摸设备，物理录制不可用")

    monkeypatch.setattr(rec, "_probe_touch_device", unsupported)
    response = api.client.post("/api/recordings", json={"serial": "fake-1", "mode": "physical"})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "UNSUPPORTED"
    assert "触摸设备" in body["error"]["message"]
    assert api.client.get("/api/recordings").json()["total"] == 0


def test_unknown_recording_returns_404(api) -> None:
    for method, path in (
        ("get", "/api/recordings/does-not-exist"),
        ("post", "/api/recordings/does-not-exist/pause"),
        ("post", "/api/recordings/does-not-exist/stop"),
        ("delete", "/api/recordings/does-not-exist"),
    ):
        response = getattr(api.client, method)(path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------- 事件 / to_script / 删除
def test_events_to_script_and_delete(api) -> None:
    recording = _create(api.client)
    recording_id = recording["id"]
    pre = hooks.on_before_pointer("fake-1")
    hooks.on_action("fake-1", "tap", {"x": 540, "y": 710, "duration_ms": 0}, pre)
    hooks.on_action(
        "fake-1",
        "swipe",
        {"from": {"x": 540, "y": 1800}, "to": {"x": 540, "y": 600}, "duration_ms": 250},
        None,
    )

    events = api.client.get(f"/api/recordings/{recording_id}/events").json()
    assert [item["seq"] for item in events["items"]] == [1, 2]
    assert events["items"][0]["kind"] == "tap"
    assert events["items"][0]["payload"]["node"] == {"by": "text", "value": "登录", "index": 0}
    assert events["items"][1]["kind"] == "swipe"

    paged = api.client.get(f"/api/recordings/{recording_id}/events?since_seq=1").json()
    assert [item["seq"] for item in paged["items"]] == [2]

    deleted = api.client.delete(f"/api/recordings/{recording_id}/events/1").json()
    assert deleted["deleted"] is True
    assert deleted["event_count"] == 1
    assert [item["seq"] for item in api.client.get(f"/api/recordings/{recording_id}/events").json()["items"]] == [2]
    assert api.client.delete(f"/api/recordings/{recording_id}/events/1").status_code == 404

    assert api.client.get(f"/api/recordings/{recording_id}").json()["script_name"] is None

    result = api.client.post(
        f"/api/recordings/{recording_id}/to_script",
        json={"name": "录制脚本B", "exclude_seqs": []},
    )
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["source"] == "mirror"
    assert payload["origin_recording_id"] == recording_id
    assert payload["name"] == "录制脚本B"
    assert [step["type"] for step in payload["data"]["steps"]] == ["swipe"]
    assert all(step["when"] is None for step in payload["data"]["steps"])
    assert payload["warnings"] is not None

    detail = api.client.get(f"/api/recordings/{recording_id}").json()
    assert detail["script_id"] == payload["script_id"]
    assert detail["script_name"] == "录制脚本B"
    listed = api.client.get("/api/recordings").json()["items"]
    assert listed[0]["script_name"] == "录制脚本B"

    removed = api.client.delete(f"/api/recordings/{recording_id}").json()
    assert removed == {"deleted": True, "id": recording_id}
    assert api.client.get(f"/api/recordings/{recording_id}").status_code == 404
    # 删除录制保留脚本 source/origin（软关联）
    from app.db import session_scope
    from app.models import Script

    with session_scope() as db:
        script = db.get(Script, payload["script_id"])
        assert script is not None
        assert script.source == "mirror"
        assert script.origin_recording_id == recording_id


# ---------------------------------------------------------------- 批量删除
def test_bulk_delete_recordings_partial_success(api) -> None:
    from sqlalchemy import func, select

    from app.db import session_scope
    from app.models import RecordingEvent

    first = _create(api.client, serial="fake-bulk-1", name="批量录制一")
    hooks.on_action("fake-bulk-1", "tap", {"x": 540, "y": 710}, None)
    assert api.client.post(f"/api/recordings/{first['id']}/stop").status_code == 200
    second = _create(api.client, serial="fake-bulk-2", name="批量录制二")

    response = api.client.post(
        "/api/recordings/bulk-delete",
        json={"ids": [first["id"], second["id"], "missing-recording", first["id"]]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == 1
    failures = {item["id"]: item["code"] for item in body["failed"]}
    assert failures == {second["id"]: "CONFLICT", "missing-recording": "NOT_FOUND"}
    assert api.client.get(f"/api/recordings/{first['id']}").status_code == 404
    assert api.client.get(f"/api/recordings/{second['id']}").json()["status"] == "recording"
    with session_scope() as db:
        events = db.scalar(
            select(func.count())
            .select_from(RecordingEvent)
            .where(RecordingEvent.recording_id == first["id"])
        )
        assert events == 0


def test_bulk_delete_recording_events_partial_success_and_recount(api) -> None:
    recording = _create(api.client, serial="fake-bulk-events")
    recording_id = recording["id"]
    for x in (540, 541, 542):
        hooks.on_action("fake-bulk-events", "tap", {"x": x, "y": 710}, None)

    response = api.client.post(
        f"/api/recordings/{recording_id}/events/bulk-delete",
        json={"seqs": [1, 3, 99, 1]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == 2
    assert [(item["id"], item["code"]) for item in body["failed"]] == [(99, "NOT_FOUND")]
    events = api.client.get(f"/api/recordings/{recording_id}/events").json()["items"]
    assert [event["seq"] for event in events] == [2]
    assert api.client.get(f"/api/recordings/{recording_id}").json()["event_count"] == 1


def test_bulk_delete_empty_lists_are_400(api) -> None:
    recording = _create(api.client, serial="fake-bulk-empty")

    recordings = api.client.post("/api/recordings/bulk-delete", json={"ids": []})
    events = api.client.post(
        f"/api/recordings/{recording['id']}/events/bulk-delete", json={"seqs": []}
    )

    assert recordings.status_code == 400
    assert recordings.json()["error"]["code"] == "INVALID_ARGUMENT"
    assert events.status_code == 400
    assert events.json()["error"]["code"] == "INVALID_ARGUMENT"


# ---------------------------------------------------------------- WS
def test_ws_state_event_ping_pong(api) -> None:
    recording = _create(api.client, serial="fake-ws")
    recording_id = recording["id"]
    with api.client.websocket_connect(f"/ws/recordings/{recording_id}") as websocket:
        state = websocket.receive_json()
        assert state["kind"] == "state"
        assert state["status"] == "recording"
        assert state["event_count"] == 0
        assert state["last_seq"] == 0
        assert state["recording_id"] == recording_id

        pre = hooks.on_before_pointer("fake-ws")
        hooks.on_action("fake-ws", "tap", {"x": 540, "y": 710}, pre)
        event = websocket.receive_json()
        assert event["kind"] == "event"
        assert event["seq"] == 1
        assert event["type"] == "tap"
        assert event["confidence"] == "high"
        assert event["payload"]["x"] == 540
        assert event["ts"]

        websocket.send_json({"kind": "ping"})
        assert websocket.receive_json() == {"kind": "pong"}


def test_ws_unknown_recording_sends_error(api) -> None:
    with api.client.websocket_connect("/ws/recordings/missing-recording") as websocket:
        frame = websocket.receive_json()
        assert frame["kind"] == "error"
        assert frame["code"] == "NOT_FOUND"
