"""设备列表 / 手动控制 / 录制挂钩 / 占用 409（契约 §7.1、§8、§14.2）。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

import tests.test_core_support as support
from app import db, models
from app.android.driver import CommandResult
from app.errors import ApiError
from app.services import device_registry

core_client = support.core_client
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_list_devices_includes_fake_when_no_real_device(core_client, monkeypatch) -> None:
    monkeypatch.delenv("AUTOTEST_FAKE_DEVICE", raising=False)
    monkeypatch.setattr(device_registry, "_real_devices", lambda: [])
    response = core_client.get("/api/devices")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    fake = next(item for item in body["items"] if item["serial"] == "fake-1")
    assert fake["state"] == "device"
    assert fake["fake"] is True
    assert fake["screen_width"] == 1080
    assert fake["screen_height"] == 2400
    assert fake["busy"] is False
    assert fake["mirror"]["active"] is False


def test_fake_hidden_when_real_device_online(core_client, monkeypatch) -> None:
    monkeypatch.delenv("AUTOTEST_FAKE_DEVICE", raising=False)
    monkeypatch.setattr(device_registry, "_real_devices", lambda: [{"serial": "real-1", "state": "device"}])
    body = core_client.get("/api/devices").json()
    serials = [item["serial"] for item in body["items"]]
    assert serials == ["real-1"]
    assert body["items"][0]["fake"] is False
    # 显式传 serial 仍可拿 fake 驱动联调
    assert core_client.get("/api/devices/fake-1/info").status_code == 200


def test_fake_forced_by_env_even_with_real_device(core_client, monkeypatch) -> None:
    monkeypatch.setenv("AUTOTEST_FAKE_DEVICE", "1")
    monkeypatch.setattr(device_registry, "_real_devices", lambda: [{"serial": "real-1", "state": "device"}])
    serials = [item["serial"] for item in core_client.get("/api/devices").json()["items"]]
    assert "real-1" in serials
    assert "fake-1" in serials


def test_list_devices_refresh(core_client, monkeypatch) -> None:
    monkeypatch.setattr(device_registry, "_real_devices", lambda: [])
    response = core_client.get("/api/devices", params={"refresh": "true"})
    assert response.status_code == 200
    assert response.json()["total"] >= 1


def test_device_info(core_client) -> None:
    response = core_client.get("/api/devices/fake-1/info")
    assert response.status_code == 200
    body = response.json()
    assert body["serial"] == "fake-1"
    assert body["state"] == "device"
    assert body["current_app"]["package"] == "com.example.fake"
    assert body["busy"] is False


def test_hierarchy_tree_and_raw(core_client) -> None:
    response = core_client.get("/api/devices/fake-1/hierarchy")
    assert response.status_code == 200
    node = response.json()["node"]
    assert node["class_name"] == "android.widget.FrameLayout"

    def texts(item: dict) -> list[str]:
        found = [item.get("text") or ""]
        for child in item.get("children") or []:
            found.extend(texts(child))
        return found

    assert "登录" in texts(node)

    raw = core_client.get("/api/devices/fake-1/hierarchy", params={"raw": "true"})
    assert raw.status_code == 200
    assert "raw" in raw.json()


def test_screenshot_png(core_client) -> None:
    response = core_client.get("/api/devices/fake-1/screenshot")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(PNG_MAGIC)


def test_apps_list_and_keyword_filter(core_client) -> None:
    driver = support.fake_driver()
    driver.shell_responses["pm list packages -3"] = CommandResult(
        0, "package:com.alpha.app\npackage:com.beta.app\n", ""
    )
    response = core_client.get("/api/devices/fake-1/apps")
    assert response.status_code == 200
    assert [item["package"] for item in response.json()["items"]] == ["com.alpha.app", "com.beta.app"]

    filtered = core_client.get("/api/devices/fake-1/apps", params={"keyword": "beta"})
    assert [item["package"] for item in filtered.json()["items"]] == ["com.beta.app"]

    bad = core_client.get("/api/devices/fake-1/apps", params={"type": "weird"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "INVALID_ARGUMENT"


def test_logcat_lines(core_client) -> None:
    driver = support.fake_driver()
    driver.shell_responses["logcat -d -t 3"] = CommandResult(0, "line-a\nline-b\n", "")
    response = core_client.get("/api/devices/fake-1/logcat", params={"lines": 3})
    assert response.status_code == 200
    assert response.json()["lines"] == ["line-a", "line-b"]


def test_shell_endpoint(core_client) -> None:
    response = core_client.post("/api/devices/fake-1/shell", json={"command": "echo hello", "timeout_ms": 5000})
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["stdout"].strip() == "hello"


def test_tap_absolute_hooks_and_call(core_client) -> None:
    recorder = support.register_recorder()
    response = core_client.post("/api/devices/fake-1/tap", json={"x": 540, "y": 710})
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "tap"
    driver = support.fake_driver()
    clicks = driver.calls_of("click")
    assert clicks and clicks[-1].kwargs == {"x": 540, "y": 710}
    assert recorder.before_pointer_calls == 1
    assert recorder.actions and recorder.actions[0][0] == "tap"
    assert recorder.actions[0][1]["x"] == 540
    assert recorder.actions[0][1]["y"] == 710
    assert recorder.actions[0][2] == {"class_name": "fake-root", "children": []}


def test_long_press(core_client) -> None:
    response = core_client.post("/api/devices/fake-1/long_press", json={"x": 100, "y": 200})
    assert response.status_code == 200
    last = support.fake_driver().calls_of("long_press")[-1]
    assert last.kwargs["duration_ms"] == 800


def test_swipe_absolute(core_client) -> None:
    recorder = support.register_recorder()
    response = core_client.post(
        "/api/devices/fake-1/swipe", json={"x1": 10, "y1": 20, "x2": 30, "y2": 40, "duration_ms": 250}
    )
    assert response.status_code == 200
    last = support.fake_driver().calls_of("swipe")[-1]
    assert last.kwargs == {"x1": 10, "y1": 20, "x2": 30, "y2": 40, "duration_ms": 250}
    assert recorder.actions[0][0] == "swipe"


def test_normalized_tap_requires_active_mirror(core_client, monkeypatch) -> None:
    from app.api import devices as devices_api

    monkeypatch.setattr(devices_api, "_mirror_module", lambda: None)
    response = core_client.post("/api/devices/fake-1/tap", json={"nx": 0.5, "ny": 0.5})
    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "INVALID_ARGUMENT"
    assert "active mirror" in body["message"]


def test_normalized_swipe_requires_active_mirror(core_client, monkeypatch) -> None:
    from app.api import devices as devices_api

    monkeypatch.setattr(devices_api, "_mirror_module", lambda: None)
    response = core_client.post(
        "/api/devices/fake-1/swipe",
        json={"nx1": 0.1, "ny1": 0.9, "nx2": 0.1, "ny2": 0.1, "duration_ms": 200},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


def test_key_and_text_and_app(core_client) -> None:
    driver = support.fake_driver()
    keyed = core_client.post("/api/devices/fake-1/key", json={"key": "back"})
    assert keyed.status_code == 200
    assert driver.calls_of("key")[-1].kwargs["keycode"] == 4

    typed = core_client.post("/api/devices/fake-1/text", json={"text": "hello", "clear_first": True})
    assert typed.status_code == 200
    ops = driver.ops()
    assert "clear_text" in ops
    assert driver.calls_of("input_text")[-1].kwargs["text"] == "hello"

    launched = core_client.post(
        "/api/devices/fake-1/app", json={"action": "launch", "package": "com.example.fake"}
    )
    assert launched.status_code == 200
    assert driver.calls_of("app_start")[-1].kwargs["package"] == "com.example.fake"


def test_key_without_key_or_keycode_rejected(core_client) -> None:
    response = core_client.post("/api/devices/fake-1/key", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_run_occupancy_returns_409(core_client) -> None:
    support.insert_run(1, status="running")
    device_registry.acquire("fake-1", "run", "1")
    try:
        response = core_client.post("/api/devices/fake-1/tap", json={"x": 1, "y": 2})
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "DEVICE_BUSY"
        assert error["details"]["serial"] == "fake-1"
        assert error["details"]["holder"]["purpose"] == "run"
    finally:
        device_registry.release("fake-1", "run", "1")


def test_paused_run_allows_manual_with_note(core_client) -> None:
    support.insert_run(2, status="paused")
    device_registry.acquire("fake-1", "run", "2")
    recorder = support.register_recorder()
    try:
        response = core_client.post("/api/devices/fake-1/tap", json={"x": 3, "y": 4})
        assert response.status_code == 200, response.text
        assert recorder.actions and recorder.actions[0][0] == "tap"
        assert recorder.notes and recorder.notes[0][0] == "tap"
        assert recorder.notes[0][1]["x"] == 3
    finally:
        device_registry.release("fake-1", "run", "2")


def _manual_intervention_events(run_id: int) -> list[models.RunEvent]:
    with db.session_scope() as session:
        return list(
            session.execute(
                select(models.RunEvent).where(
                    models.RunEvent.run_id == run_id,
                    models.RunEvent.type == "manual_intervention",
                )
            )
            .scalars()
            .all()
        )


def test_paused_run_manual_tap_emits_manual_intervention_event(core_client) -> None:
    support.insert_run(21, status="paused")
    device_registry.acquire("fake-1", "run", "21")
    try:
        response = core_client.post("/api/devices/fake-1/tap", json={"x": 11, "y": 22})
        assert response.status_code == 200, response.text
    finally:
        device_registry.release("fake-1", "run", "21")

    events = _manual_intervention_events(21)
    assert len(events) == 1
    event = events[0]
    assert event.seq >= 1
    assert event.payload["kind"] == "tap"
    assert event.payload["payload"]["x"] == 11
    assert event.payload["payload"]["y"] == 22
    assert event.created_at


def test_paused_run_manual_key_and_text_emit_events(core_client) -> None:
    support.insert_run(22, status="paused")
    device_registry.acquire("fake-1", "run", "22")
    try:
        assert core_client.post("/api/devices/fake-1/key", json={"key": "back"}).status_code == 200
        assert core_client.post("/api/devices/fake-1/text", json={"text": "hi"}).status_code == 200
    finally:
        device_registry.release("fake-1", "run", "22")
    kinds = [event.payload["kind"] for event in _manual_intervention_events(22)]
    assert kinds == ["key", "input"]
    seqs = [event.seq for event in _manual_intervention_events(22)]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_normal_manual_tap_emits_no_run_event(core_client) -> None:
    response = core_client.post("/api/devices/fake-1/tap", json={"x": 1, "y": 2})
    assert response.status_code == 200
    with db.session_scope() as session:
        count = session.execute(
            select(models.RunEvent).where(models.RunEvent.type == "manual_intervention")
        ).scalars().all()
    assert count == []


def test_recording_and_mirror_occupancy_do_not_block(core_client) -> None:
    device_registry.acquire("fake-1", "recording", "rec-9")
    device_registry.acquire("fake-1", "mirror", "mir-9")
    try:
        tap = core_client.post("/api/devices/fake-1/tap", json={"x": 7, "y": 8})
        assert tap.status_code == 200, tap.text
        swipe = core_client.post(
            "/api/devices/fake-1/swipe", json={"x1": 1, "y1": 2, "x2": 3, "y2": 4}
        )
        assert swipe.status_code == 200
    finally:
        device_registry.release("fake-1", "recording", "rec-9")
        device_registry.release("fake-1", "mirror", "mir-9")


def test_busy_flag_in_device_list(core_client, monkeypatch) -> None:
    monkeypatch.setattr(device_registry, "_real_devices", lambda: [])
    device_registry.acquire("fake-1", "run", "5")
    try:
        listed = core_client.get("/api/devices").json()
        fake = next(item for item in listed["items"] if item["serial"] == "fake-1")
        assert fake["busy"] is True
        assert fake["busy_reason"] == "run:5"
    finally:
        device_registry.release("fake-1", "run", "5")


def test_acquire_is_idempotent_and_release_silent() -> None:
    device_registry.reset_for_tests()
    device_registry.acquire("fake-1", "run", "9")
    device_registry.acquire("fake-1", "run", "9")  # 幂等
    assert len(device_registry.occupancy("fake-1")) == 1
    with pytest.raises(ApiError) as excinfo:
        device_registry.acquire("fake-1", "recording", "rec-x")
    assert excinfo.value.code == "DEVICE_BUSY"
    device_registry.release("fake-1", "run", "9")
    device_registry.release("fake-1", "run", "9")  # 静默
    assert device_registry.occupancy("fake-1") == []


def test_screenrecord_unsupported_when_driver_module_missing(core_client, monkeypatch) -> None:
    from app.api import devices as devices_api

    monkeypatch.setattr(devices_api, "_screen_module", lambda: None)
    response = core_client.post("/api/devices/fake-1/screenrecord/start", json={})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "UNSUPPORTED"
    assert "screen" in error["message"]


def test_screenrecord_stop_without_start_404(core_client) -> None:
    response = core_client.post("/api/devices/fake-1/screenrecord/stop")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_network_capture_disabled_unsupported(core_client) -> None:
    response = core_client.post("/api/devices/fake-1/network/start")
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "UNSUPPORTED"
    assert error["details"]["setting"] == "capture.network.enabled"


def test_connect_unavailable_without_adb_module(core_client, monkeypatch) -> None:
    from app.api import devices as devices_api

    monkeypatch.setattr(devices_api, "_adb_module", lambda: None)
    response = core_client.post("/api/devices/connect", json={"address": "127.0.0.1:5555"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEVICE_UNAVAILABLE"


class _StubAdb:
    def __init__(self, code: int = 0, stdout: str = "") -> None:
        self.code = code
        self.stdout = stdout
        self.calls: list[list[str]] = []

    def run_adb(self, args, timeout_s: float = 15):  # noqa: ANN001, ANN201 - 测试桩
        self.calls.append(list(args))
        return CommandResult(self.code, self.stdout, "")


def test_connect_via_stub_adb(core_client, monkeypatch) -> None:
    from app.api import devices as devices_api

    stub = _StubAdb(stdout="connected to 127.0.0.1:5555\n")
    monkeypatch.setattr(devices_api, "_adb_module", lambda: stub)
    response = core_client.post("/api/devices/connect", json={"address": "127.0.0.1:5555"})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert stub.calls == [["connect", "127.0.0.1:5555"]]


def test_disconnect_via_stub_adb(core_client, monkeypatch) -> None:
    from app.api import devices as devices_api

    stub = _StubAdb(stdout="disconnected 127.0.0.1:5555\n")
    monkeypatch.setattr(devices_api, "_adb_module", lambda: stub)
    response = core_client.post("/api/devices/127.0.0.1:5555/disconnect")
    assert response.status_code == 200
    assert stub.calls == [["disconnect", "127.0.0.1:5555"]]
