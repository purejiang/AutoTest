"""流式触摸端点（/api/devices/{serial}/touch）集成测试。

真机语义（已手工验证）：down+up = 点击、down+move…+up = 拖动/滚动、单条消息 3~5ms。
这里用假镜像会话验证：参数校验、无镜像时的明确报错、手势归类（tap/long_press/swipe/drag）。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import mirror as mirror_mod


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import config, db

    monkeypatch.setenv("AUTOTEST_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AUTOTEST_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AUTOTEST_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("AUTOTEST_FAKE_DEVICE", "1")
    config.reset_to_defaults()
    config.ensure_dirs()
    db.reset_engine_for_tests(tmp_path / "touch.db")

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    config.reset_to_defaults()


def _touch(client: TestClient, action: str, nx: float, ny: float, serial: str = "fake-1") -> dict:
    resp = client.post(
        f"/api/devices/{serial}/touch", json={"action": action, "nx": nx, "ny": ny}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_touch_requires_mirror_session(client: TestClient):
    """没有活跃镜像会话 → 400 UNSUPPORTED（前端据此回落到一次性手势），不能 500。"""
    resp = client.post("/api/devices/fake-1/touch", json={"action": "down", "nx": 0.5, "ny": 0.5})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "UNSUPPORTED"
    assert body["error"]["details"]["serial"] == "fake-1"


def test_touch_validation(client: TestClient):
    bad_action = client.post("/api/devices/fake-1/touch", json={"action": "press", "nx": 0.5, "ny": 0.5})
    assert bad_action.status_code == 422
    out_of_range = client.post("/api/devices/fake-1/touch", json={"action": "move", "nx": 1.5, "ny": 0.5})
    assert out_of_range.status_code == 422
    assert out_of_range.json()["error"]["code"] == "VALIDATION_ERROR"


def test_touch_stream_sequence_and_classification(client: TestClient, monkeypatch):
    """模拟镜像可用：down → move… → up 应被归类成一条 swipe（而不是三条触摸事件）。"""
    calls: list[tuple[str, float, float]] = []

    def fake_inject(serial: str, action: str, nx: float, ny: float) -> bool:
        calls.append((action, nx, ny))
        return True

    monkeypatch.setattr(mirror_mod, "inject_touch", fake_inject)
    # 设备像素映射：镜像 1200x2670 无 crop，归一化 * 设备尺寸
    monkeypatch.setattr(
        mirror_mod,
        "map_normalized_to_device",
        lambda serial, nx, ny: (int(nx * 1200), int(ny * 2670)),
    )

    down = _touch(client, "down", 0.5, 0.8)
    assert down["action"] == "down"
    for i in range(1, 6):
        _touch(client, "move", 0.5, 0.8 - 0.1 * i)
    up = _touch(client, "up", 0.5, 0.3)
    assert up["kind"] == "swipe"
    assert 0 <= up["duration_ms"] < 5000
    assert up["event"]["x1"] == 600
    assert up["event"]["y2"] == int(0.3 * 2670)
    assert [c[0] for c in calls] == ["down", "move", "move", "move", "move", "move", "up"]


def test_touch_short_press_is_tap(client: TestClient, monkeypatch):
    monkeypatch.setattr(mirror_mod, "inject_touch", lambda *a, **k: True)
    monkeypatch.setattr(
        mirror_mod, "map_normalized_to_device", lambda serial, nx, ny: (int(nx * 1200), int(ny * 2670))
    )
    _touch(client, "down", 0.5, 0.5)
    up = _touch(client, "up", 0.5, 0.5)
    assert up["kind"] == "tap"
    assert up["event"]["x"] == 600


def test_touch_hold_without_move_is_long_press(client: TestClient, monkeypatch):
    monkeypatch.setattr(mirror_mod, "inject_touch", lambda *a, **k: True)
    monkeypatch.setattr(
        mirror_mod, "map_normalized_to_device", lambda serial, nx, ny: (int(nx * 1200), int(ny * 2670))
    )
    _touch(client, "down", 0.5, 0.5)
    time.sleep(0.55)
    up = _touch(client, "up", 0.5, 0.5)
    assert up["kind"] == "long_press"


def test_touch_circle_with_same_endpoint_is_drag(client: TestClient, monkeypatch):
    """画圈回到起点：起点→终点距离≈0，但轨迹范围大，仍应被归为 drag 并保留 path。"""
    monkeypatch.setattr(mirror_mod, "inject_touch", lambda *a, **k: True)
    monkeypatch.setattr(
        mirror_mod, "map_normalized_to_device", lambda serial, nx, ny: (int(nx * 1200), int(ny * 2670))
    )
    _touch(client, "down", 0.5, 0.5)
    for nx, ny in ((0.6, 0.5), (0.6, 0.6), (0.5, 0.6), (0.4, 0.6), (0.4, 0.5), (0.5, 0.5)):
        _touch(client, "move", nx, ny)
    time.sleep(0.35)
    up = _touch(client, "up", 0.5, 0.5)
    assert up["kind"] == "drag"
    assert up["event"]["x1"] == up["event"]["x2"] == 600
    assert up["event"]["y1"] == up["event"]["y2"] == int(0.5 * 2670)
    assert len(up["event"]["path"]) >= 6


def test_touch_slow_drag_is_drag(client: TestClient, monkeypatch):
    monkeypatch.setattr(mirror_mod, "inject_touch", lambda *a, **k: True)
    monkeypatch.setattr(
        mirror_mod, "map_normalized_to_device", lambda serial, nx, ny: (int(nx * 1200), int(ny * 2670))
    )
    _touch(client, "down", 0.5, 0.8)
    time.sleep(0.35)  # 超过 300ms + 有位移 → drag
    _touch(client, "move", 0.5, 0.6)
    up = _touch(client, "up", 0.5, 0.5)
    assert up["kind"] == "drag"
