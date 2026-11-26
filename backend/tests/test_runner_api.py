"""运行 REST API 测试（契约 §7.3）：创建/占用 409/列表详情/删除/events/暂停恢复停止/调试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app import db, models
from app import ws as ws_api
from app.api import runs as runs_api
from app.errors import ApiError, install_error_handlers
from app.events import RunEventBus
from app.recorder import hooks
from app.runner import controls, engine
from tests.test_runner_kits import (
    click_step,
    get_run,
    insert_run,
    insert_script,
    install_selectors,
    make_script,
    make_step,
    set_run_fields,
    wait_until,
)
from tests.test_runner_kits import (
    runner_env as runner_env,
)


@pytest.fixture()
def api_client(runner_env):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(runs_api.router)
    app.include_router(ws_api.router)
    with TestClient(app) as client:
        yield client


def _create_script(settings: dict | None = None) -> tuple[str, dict]:
    script = make_script([click_step("s1"), click_step("s2")], name="登录流程", settings=settings)
    return insert_script(script), script


def _assert_error(response, code: str) -> dict:
    assert response.status_code >= 400, response.text
    body = response.json()
    assert body["error"]["code"] == code, body
    return body["error"]


# ---------------------------------------------------------------- 创建
def test_create_run_persists_and_starts(api_client, runner_env, monkeypatch):
    script_id, _script = _create_script({"on_fail": "continue"})
    started: list[int] = []
    monkeypatch.setattr(engine, "start_run", started.append)

    response = api_client.post(
        "/api/runs",
        json={
            "script_id": script_id,
            "serial": "fake-test",
            "variables": {"user": "alice"},
            "debug": {"step_mode": False, "breakpoints": ["s2"]},
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["script_id"] == script_id
    assert body["device_serial"] == "fake-test"
    assert body["total_steps"] == 2
    assert body["debug"]["breakpoints"] == ["s2"]
    assert started == [body["id"]]
    assert runner_env.acquisitions == [("fake-test", "run", str(body["id"]))]

    with db.session_scope() as session:
        row = session.get(models.Run, body["id"])
        assert row.status == "queued"
        assert row.variables == {"user": "alice"}
        assert row.script_snapshot["name"] == "登录流程"
        assert row.total_steps == 2

    # 运行变量：variables 只放覆盖项，effective_variables = 脚本默认 + 覆盖
    assert body["variables"] == {"user": "alice"}
    assert body["effective_variables"] == {"user": "alice"}


def test_run_variables_merge_script_defaults_with_overrides(api_client, runner_env, monkeypatch):
    """运行记录回显：variables 只含覆盖，effective_variables 含脚本默认值。"""
    script = make_script(
        [click_step("s1")],
        name="带变量脚本",
        variables={"pkg": "com.example.default", "user": "default-user"},
    )
    script_id = insert_script(script)
    monkeypatch.setattr(engine, "start_run", lambda run_id: None)

    created = api_client.post(
        "/api/runs",
        json={"script_id": script_id, "serial": "fake-test", "variables": {"pkg": "com.example.override"}},
    ).json()

    assert created["variables"] == {"pkg": "com.example.override"}
    assert created["effective_variables"] == {
        "pkg": "com.example.override",
        "user": "default-user",
    }

    detail = api_client.get(f"/api/runs/{created['id']}").json()
    assert detail["variables"] == {"pkg": "com.example.override"}
    assert detail["effective_variables"] == {"pkg": "com.example.override", "user": "default-user"}

    listing = api_client.get("/api/runs").json()
    assert listing["items"][0]["effective_variables"]["pkg"] == "com.example.override"


def test_run_variables_interpolate_app_package_on_device(api_client, runner_env):
    """app.package 支持 {{var}}：请求覆盖值必须真的传给驱动（包名不再写死）。"""
    script = make_script(
        [make_step("s1", "app", {"action": "launch", "package": "{{pkg}}"})],
        name="启动应用",
        variables={"pkg": "com.example.default"},
    )
    script_id = insert_script(script)

    created = api_client.post(
        "/api/runs",
        json={
            "script_id": script_id,
            "serial": "fake-test",
            "variables": {"pkg": "com.example.override"},
        },
    ).json()
    wait_until(
        lambda: (get_run(created["id"]) or {}).get("status") in {"succeeded", "failed"},
        timeout=5,
        message="run finished",
    )
    detail = api_client.get(f"/api/runs/{created['id']}").json()
    assert detail["status"] == "succeeded", detail
    app_calls = [call for call in runner_env.driver.calls if call.op == "app_start"]
    assert app_calls, runner_env.driver.calls
    assert app_calls[-1].kwargs["package"] == "com.example.override"


def test_create_run_auto_resolves_serial(api_client, runner_env, monkeypatch):
    script_id, _script = _create_script()
    monkeypatch.setattr(engine, "start_run", lambda run_id: None)

    response = api_client.post("/api/runs", json={"script_id": script_id})

    assert response.status_code == 200, response.text
    assert response.json()["device_serial"] == "fake-test"


def test_create_run_script_not_found(api_client):
    response = api_client.post("/api/runs", json={"script_id": "missing"})
    _assert_error(response, "NOT_FOUND")


def test_create_run_invalid_script(api_client):
    script_id = insert_script({"name": "坏脚本", "steps": []})
    response = api_client.post("/api/runs", json={"script_id": script_id})
    error = _assert_error(response, "VALIDATION_ERROR")
    assert error["details"]["errors"]


def test_create_run_conflicts_with_recording(api_client):
    script_id, _script = _create_script()
    session = SimpleNamespace(
        recording_id="rec-1",
        before_pointer=lambda: None,
        handle=lambda *args, **kwargs: None,
        note_manual=lambda *args, **kwargs: None,
    )
    hooks.register("fake-test", session)
    try:
        response = api_client.post("/api/runs", json={"script_id": script_id, "serial": "fake-test"})
        _assert_error(response, "CONFLICT")
    finally:
        hooks.unregister("fake-test", "rec-1")


def test_create_run_device_busy_rolls_back(api_client, runner_env, monkeypatch):
    script_id, _script = _create_script()

    def _busy(serial: str, purpose: str, ref_id: object) -> None:
        raise ApiError("DEVICE_BUSY", "device is busy", {"serial": serial})

    monkeypatch.setattr(engine, "acquire", _busy)
    response = api_client.post("/api/runs", json={"script_id": script_id, "serial": "fake-test"})

    _assert_error(response, "DEVICE_BUSY")
    listing = api_client.get("/api/runs").json()
    assert listing["total"] == 0


# ---------------------------------------------------------------- 列表 / 详情
def test_list_and_detail(api_client, runner_env, monkeypatch):
    monkeypatch.setattr(engine, "start_run", lambda run_id: None)
    script_id, _script = _create_script()
    first = api_client.post("/api/runs", json={"script_id": script_id, "serial": "fake-test"}).json()
    second = api_client.post("/api/runs", json={"script_id": script_id, "serial": "fake-test"}).json()

    listing = api_client.get("/api/runs").json()
    assert listing["total"] == 2
    assert [item["id"] for item in listing["items"]] == [second["id"], first["id"]]

    queued = api_client.get("/api/runs", params={"status": "queued"}).json()
    assert queued["total"] == 2
    empty = api_client.get("/api/runs", params={"status": "succeeded"}).json()
    assert empty["total"] == 0

    detail = api_client.get(f"/api/runs/{first['id']}").json()
    assert detail["id"] == first["id"]
    assert detail["steps"] == []
    assert detail["artifacts"] == []

    _assert_error(api_client.get("/api/runs/99999"), "NOT_FOUND")


# ---------------------------------------------------------------- 删除
def test_delete_run_removes_rows_files_and_events(api_client, runner_env):
    script_id, script = _create_script()
    run_id = insert_run(script, status="failed")
    RunEventBus.emit(run_id, "run_started", {"total_steps": 2})
    artifact_path = runner_env.data_dir / "artifacts" / f"run_{run_id}" / "shot.png"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    with db.session_scope() as session:
        session.add(
            models.Artifact(
                run_id=run_id,
                kind="screenshot",
                name="shot",
                path=str(artifact_path),
                size=artifact_path.stat().st_size,
            )
        )
        session.add(
            models.StepResult(
                run_id=run_id,
                step_id="s1",
                step_name="s1",
                step_type="click",
                step_index=0,
                status="passed",
            )
        )
    _ = script_id

    response = api_client.delete(f"/api/runs/{run_id}")

    assert response.status_code == 200
    assert response.json() == {"deleted": True, "run_id": run_id}
    assert get_run(run_id) is None
    assert not artifact_path.exists()
    assert RunEventBus.replay(run_id, 0) == []
    with db.session_scope() as session:
        from sqlalchemy import func, select

        steps = session.execute(
            select(func.count()).select_from(models.StepResult).where(models.StepResult.run_id == run_id)
        ).scalar()
        artifacts = session.execute(
            select(func.count()).select_from(models.Artifact).where(models.Artifact.run_id == run_id)
        ).scalar()
        assert steps == 0
        assert artifacts == 0


def test_bulk_delete_runs_only_terminal_and_dedups(api_client, runner_env):
    script_id, script = _create_script()
    terminal_id = insert_run(script, status="failed", script_id=script_id)
    active_id = insert_run(script, status="running", script_id=script_id)
    artifact_path = runner_env.data_dir / "artifacts" / f"run_{terminal_id}" / "bulk.png"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    with db.session_scope() as session:
        session.add(
            models.Artifact(
                run_id=terminal_id,
                kind="screenshot",
                name="bulk",
                path=str(artifact_path),
                size=artifact_path.stat().st_size,
            )
        )

    response = api_client.post(
        "/api/runs/bulk-delete",
        json={"ids": [terminal_id, active_id, 99999, terminal_id]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == 1
    failures = {item["id"]: item["code"] for item in body["failed"]}
    assert failures == {active_id: "CONFLICT", 99999: "NOT_FOUND"}
    assert get_run(terminal_id) is None
    assert get_run(active_id)["status"] == "running"  # 不自动 stop
    assert not artifact_path.exists()
    assert not artifact_path.parent.exists()


def test_bulk_delete_runs_empty_list_is_400(api_client):
    response = api_client.post("/api/runs/bulk-delete", json={"ids": []})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


# ---------------------------------------------------------------- events
def test_events_endpoint_replays_since_seq(api_client):
    script = make_script([click_step("s1")])
    run_id = insert_run(script, status="running")
    seqs = [RunEventBus.emit(run_id, "log", {"message": f"m{i}"}) for i in range(3)]

    response = api_client.get(f"/api/runs/{run_id}/events", params={"since_seq": 1, "limit": 10})

    assert response.status_code == 200
    body = response.json()
    assert [item["seq"] for item in body["items"]] == seqs[1:]
    assert body["total"] == 2
    assert body["last_seq"] == 3
    _assert_error(api_client.get("/api/runs/99999/events"), "NOT_FOUND")


# ---------------------------------------------------------------- 控制
def _paused_run() -> int:
    script = make_script(
        [make_step("s1", "click", {"target": {"by": "text", "value": "登录"}}, rerun_safety="destructive")]
    )
    run_id = insert_run(script, status="running")
    controls.create_control(run_id)
    set_run_fields(run_id, current_step_id="s1")
    return run_id


def test_pause_resume_check_resume_stop_flow(api_client, runner_env):
    run_id = _paused_run()

    # 未 paused 时 resume-check / resume 都 409
    _assert_error(api_client.post(f"/api/runs/{run_id}/resume-check", json={}), "CONFLICT")
    _assert_error(api_client.post(f"/api/runs/{run_id}/resume", json={}), "CONFLICT")

    pause = api_client.post(f"/api/runs/{run_id}/pause")
    assert pause.status_code == 200
    assert pause.json()["paused"] is True
    assert get_run(run_id)["status"] == "paused"

    # cold resume 明确 UNSUPPORTED
    _assert_error(
        api_client.post(f"/api/runs/{run_id}/resume", json={"resume_kind": "cold"}),
        "UNSUPPORTED",
    )
    _assert_error(
        api_client.post(f"/api/runs/{run_id}/resume-check", json={"resume_kind": "cold"}),
        "UNSUPPORTED",
    )

    check = api_client.post(f"/api/runs/{run_id}/resume-check", json={})
    assert check.status_code == 200, check.text
    check_body = check.json()
    assert check_body["resume_kind"] == "hot"
    assert check_body["device_alive"] is True
    assert check_body["foreground_snapshot"]["package"] == "com.example.fake"
    assert check_body["destructive_current_node"] is True
    assert check_body["current_step_id"] == "s1"
    assert check_body["warnings"]

    resume = api_client.post(f"/api/runs/{run_id}/resume", json={"resume_kind": "hot"})
    assert resume.status_code == 200, resume.text
    assert resume.json()["paused"] is False
    assert get_run(run_id)["status"] == "running"

    _assert_error(api_client.post(f"/api/runs/{run_id}/resume", json={}), "CONFLICT")

    step_debug = api_client.post(f"/api/runs/{run_id}/debug", json={"action": "step"})
    assert step_debug.status_code == 200
    assert step_debug.json()["step_mode"] is True

    _assert_error(
        api_client.post(f"/api/runs/{run_id}/debug", json={"action": "run_from", "step_id": "nope"}),
        "INVALID_ARGUMENT",
    )
    jump = api_client.post(f"/api/runs/{run_id}/debug", json={"action": "run_from", "step_id": "s1"})
    assert jump.status_code == 200
    assert jump.json()["pending_run_from"] == "s1"

    stop = api_client.post(f"/api/runs/{run_id}/stop")
    assert stop.status_code == 200
    assert stop.json()["status"] == "stopping"
    assert get_run(run_id)["status"] == "stopping"


def test_control_endpoints_require_active_run(api_client):
    script = make_script([click_step("s1")])
    run_id = insert_run(script, status="queued")

    _assert_error(api_client.post(f"/api/runs/{run_id}/pause"), "CONFLICT")
    _assert_error(api_client.post(f"/api/runs/{run_id}/resume"), "CONFLICT")
    _assert_error(api_client.post(f"/api/runs/{run_id}/resume-check"), "CONFLICT")
    _assert_error(api_client.post(f"/api/runs/{run_id}/stop"), "CONFLICT")
    _assert_error(api_client.post(f"/api/runs/{run_id}/debug", json={"action": "step"}), "CONFLICT")


def test_control_endpoints_not_found(api_client):
    _assert_error(api_client.post("/api/runs/424242/pause"), "NOT_FOUND")
    _assert_error(api_client.post("/api/runs/424242/resume"), "NOT_FOUND")
    _assert_error(api_client.post("/api/runs/424242/stop"), "NOT_FOUND")
    _assert_error(api_client.post("/api/runs/424242/debug", json={"action": "step"}), "NOT_FOUND")
    _assert_error(api_client.delete("/api/runs/424242"), "NOT_FOUND")


def test_create_run_plain_function_available_for_other_modules(api_client, runner_env, monkeypatch):
    """be-core 的 POST /api/scripts/{id}/run 直接调用 runs.create_run()（无 FastAPI 依赖）。"""
    script_id, _script = _create_script()
    started: list[int] = []
    monkeypatch.setattr(engine, "start_run", started.append)

    created = runs_api.create_run(script_id, serial="fake-test", variables={"a": "b"}, debug=None)

    assert created["status"] == "queued"
    assert created["device_serial"] == "fake-test"
    assert started == [created["id"]]
    assert created["script_name"] == "登录流程"


def test_start_run_endpoint_after_engine_finishes(api_client, runner_env, monkeypatch):
    """真实引擎跑完一次后 API 详情能读到步骤与终态（summary/ended_at）。"""
    install_selectors(monkeypatch)
    script = make_script([click_step("s1")])
    script_id = insert_script(script)
    created = api_client.post("/api/runs", json={"script_id": script_id, "serial": "fake-test"}).json()
    wait_until(
        lambda: (get_run(created["id"]) or {}).get("status") in {"succeeded", "failed"},
        timeout=5,
        message="run finished",
    )
    detail = api_client.get(f"/api/runs/{created['id']}").json()
    assert detail["status"] == "succeeded"
    assert [step["step_id"] for step in detail["steps"]] == ["s1"]
    assert detail["summary"]["passed_steps"] == 1
