"""端到端集成测试（Lead 维护）：fake 设备跑通 脚本 CRUD → 执行 → 事件 → 产物 → 报告 → 录制转脚本。

全程不依赖真机/adb：设备注册表把 serial 以 fake 开头的设备路由到 FakeDriver。
"""

from __future__ import annotations

import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import config, db

    # 用环境变量层做隔离（优先级最高，不会被 lifespan 的 DB 层加载覆盖）
    monkeypatch.setenv("AUTOTEST_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AUTOTEST_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AUTOTEST_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("AUTOTEST_ADB", raising=False)
    monkeypatch.setenv("AUTOTEST_FAKE_DEVICE", "1")  # 保证 e2e 不依赖机器上是否插着真机
    config.reset_to_defaults()
    config.ensure_dirs()
    db.reset_engine_for_tests(tmp_path / "e2e.db")

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    config.reset_to_defaults()


E2E_SCRIPT = {
    "schema_version": 1,
    "name": "e2e 登录",
    "notes": "",
    "variables": {"account": "13800000000"},
    "settings": {"default_timeout_ms": 3000, "on_fail": "abort", "logcat": False, "retry": None,
                 "screenshot_on_fail": True},
    "defaults": {"retry": None},
    "steps": [
        {"id": "s1", "name": "启动应用", "type": "app", "description": "", "enabled": True, "when": None,
         "timeout_ms": 5000, "delay_before_ms": 0, "delay_after_ms": 0, "retry": None,
         "on_fail": {"action": "abort"}, "screenshot_on_fail": True, "rerun_safety": "destructive",
         "tags": [], "params": {"action": "launch", "package": "com.example.fake", "activity": None}},
        {"id": "s2", "name": "填账号", "type": "input", "description": "", "enabled": True, "when": None,
         "timeout_ms": 5000, "delay_before_ms": 0, "delay_after_ms": 0, "retry": None, "on_fail": None,
         "screenshot_on_fail": True, "rerun_safety": "safe", "tags": [],
         "params": {"text": "{{account}}", "target": {"by": "resource_id",
                    "value": "com.example.fake:id/et_account", "index": 0}, "clear_first": True}},
        {"id": "s3", "name": "点登录", "type": "click", "description": "", "enabled": True, "when": None,
         "timeout_ms": 5000, "delay_before_ms": 0, "delay_after_ms": 0,
         "retry": {"count": 1, "interval_ms": 100}, "on_fail": {"action": "abort"},
         "screenshot_on_fail": True, "rerun_safety": "safe", "tags": [],
         "params": {"target": {"by": "text", "value": "登录", "index": 0}}},
        {"id": "s4", "name": "断言前台", "type": "assert", "description": "", "enabled": True, "when": None,
         "timeout_ms": 3000, "delay_before_ms": 0, "delay_after_ms": 0, "retry": None,
         "on_fail": {"action": "abort"}, "screenshot_on_fail": True, "rerun_safety": "safe", "tags": [],
         "params": {"kind": "foreground", "value": "com.example.fake"}},
        {"id": "s5", "name": "留证", "type": "screenshot", "description": "", "enabled": True, "when": None,
         "timeout_ms": 3000, "delay_before_ms": 0, "delay_after_ms": 0, "retry": None, "on_fail": None,
         "screenshot_on_fail": False, "rerun_safety": "safe", "tags": [], "params": {"name": "after_login"}},
    ],
}

FAKE_SERIAL = "fake-1"  # 设备注册表默认 fake 设备（见 device_registry.FAKE_DEFAULT_SERIAL）


def _wait_run(client: TestClient, run_id: int, timeout_s: float = 30.0) -> dict:
    deadline = time.time() + timeout_s
    terminal = {"succeeded", "failed", "aborted", "stopped", "interrupted"}
    last = {}
    while time.time() < deadline:
        resp = client.get(f"/api/runs/{run_id}")
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if last["status"] in terminal:
            return last
        time.sleep(0.1)
    raise AssertionError(f"run 未在 {timeout_s}s 内结束，最后状态: {last.get('status')}")


def test_health_and_devices(client: TestClient):
    health = client.get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] in ("ok", "degraded")

    devices = client.get("/api/devices")
    assert devices.status_code == 200
    serials = [d["serial"] for d in devices.json()["items"]]
    assert FAKE_SERIAL in serials, serials


def test_script_crud_and_validation_envelope(client: TestClient):
    created = client.post("/api/scripts", json={"name": "空脚本"})
    assert created.status_code in (200, 201), created.text
    script = created.json()
    assert script["data"]["steps"], "新建脚本应带默认节点"

    bad = client.post("/api/scripts/validate", json={"data": {"schema_version": 1, "name": "坏",
                                                              "steps": [{"id": "s1", "type": "click"}]}})
    assert bad.status_code == 200
    assert bad.json()["valid"] is False
    assert bad.json()["errors"]

    resp = client.put(f"/api/scripts/{script['id']}", json={"data": {"name": "x", "steps": []}})
    assert resp.status_code == 422
    env = resp.json()
    assert env["error"]["code"] == "VALIDATION_ERROR"


def test_full_run_pipeline(client: TestClient):
    script = client.post("/api/scripts", json={"name": "e2e 登录", "data": E2E_SCRIPT}).json()
    run = client.post("/api/runs", json={"script_id": script["id"], "serial": FAKE_SERIAL})
    assert run.status_code in (200, 201), run.text
    run_id = run.json()["id"]

    final = _wait_run(client, run_id)
    assert final["status"] == "succeeded", final
    assert final["steps"], "应有 step_results"
    assert all(s["status"] == "passed" for s in final["steps"]), final["steps"]

    events = client.get(f"/api/runs/{run_id}/events").json()["items"]
    types = [e["type"] for e in events]
    assert "run_started" in types and "run_finished" in types
    assert "step_finished" in types
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    assert any(a["kind"] == "screenshot" for a in final["artifacts"]), final["artifacts"]

    report = client.post(f"/api/runs/{run_id}/report", json={"include_video": False})
    assert report.status_code in (200, 201), report.text
    report_id = report.json()["report_id"]
    dl = client.get(f"/api/reports/{report_id}/download")
    assert dl.status_code == 200
    with zipfile.ZipFile(io.BytesIO(dl.content)) as zf:
        names = zf.namelist()
        assert any(n.endswith("index.html") for n in names), names
        assert any(n.endswith("junit.xml") for n in names), names


def test_web_recording_to_script(client: TestClient):
    rec = client.post("/api/recordings", json={"serial": FAKE_SERIAL, "mode": "web", "name": "录制用例"})
    assert rec.status_code in (200, 201), rec.text
    recording_id = rec.json()["id"]

    tap = client.post(f"/api/devices/{FAKE_SERIAL}/tap", json={"x": 540, "y": 710})
    assert tap.status_code == 200, tap.text
    swipe = client.post(f"/api/devices/{FAKE_SERIAL}/swipe",
                        json={"x1": 540, "y1": 1800, "x2": 540, "y2": 900, "duration_ms": 300})
    assert swipe.status_code == 200, swipe.text
    client.post(f"/api/devices/{FAKE_SERIAL}/key", json={"key": "back"})

    events = client.get(f"/api/recordings/{recording_id}/events").json()["items"]
    assert len(events) >= 3, events
    kinds = [e["kind"] for e in events]
    assert "tap" in kinds and "swipe" in kinds and "key" in kinds

    stopped = client.post(f"/api/recordings/{recording_id}/stop")
    assert stopped.status_code == 200, stopped.text
    converted = client.post(f"/api/recordings/{recording_id}/to_script", json={"name": "回放录制"})
    assert converted.status_code in (200, 201), converted.text
    body = converted.json()
    assert body["data"]["steps"], body

    run = client.post("/api/runs", json={"script_id": body["script_id"], "serial": FAKE_SERIAL})
    assert run.status_code in (200, 201), run.text
    final = _wait_run(client, run.json()["id"])
    assert final["status"] in ("succeeded", "failed")  # 回放结果允许失败，但不能卡住


def test_normalized_coords_require_mirror(client: TestClient):
    resp = client.post(f"/api/devices/{FAKE_SERIAL}/tap", json={"nx": 0.5, "ny": 0.5})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_ARGUMENT"
