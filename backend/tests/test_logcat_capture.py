"""logcat 端到端采集：手动 API + 引擎自动 attach/stop + 幂等与归属。

全部使用 fake 进程/驱动，不依赖真实设备或 adb。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config
from app.api import logcat_capture
from app.capture import logcat
from app.db import session_scope
from app.errors import install_error_handlers
from app.models import Artifact, Run
from app.runner import engine
from app.services import device_registry
from tests.test_runner_kits import (
    get_artifacts,
    insert_run,
    make_script,
    run_script,
    wait_run,
    wait_step,
)
from tests.test_runner_kits import runner_env as runner_env


# ---------------------------------------------------------------- fake 进程
class _FakeStream:
    """读完给定行后阻塞，直到 terminate（模拟持续 logcat）。"""

    def __init__(self, lines: list[str]) -> None:
        self._iterator = iter(lines)
        self._gate = threading.Event()

    def __iter__(self):
        return self

    def __next__(self) -> str:
        try:
            return next(self._iterator)
        except StopIteration:
            self._gate.wait(5)
            raise StopIteration from None

    def open_gate(self) -> None:
        self._gate.set()


class _FakeProcess:
    def __init__(self, lines: list[str]) -> None:
        self.stdout = _FakeStream(lines)
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True
        self.stdout.open_gate()

    def kill(self) -> None:
        self.killed = True
        self.stdout.open_gate()

    def wait(self, timeout: float | None = None) -> int:
        return 0


def _wait_for(predicate, timeout: float = 2.0, message: str = "condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError(f"timeout waiting for {message}")


# ---------------------------------------------------------------- fixtures
@pytest.fixture()
def logcat_env(tmp_path, temp_db):
    config.reset_to_defaults()
    data_dir = tmp_path / "data"
    config.set_file_layer(
        {"runtime.data_dir": str(data_dir), "capture.logcat_auto_attach": True}
    )
    logcat.shutdown_all()
    logcat._ACTIVE.clear()
    logcat._LAST.clear()
    yield data_dir
    logcat.shutdown_all()
    logcat._ACTIVE.clear()
    logcat._LAST.clear()
    config.reset_to_defaults()


@pytest.fixture()
def logcat_cleanup():
    logcat.shutdown_all()
    logcat._ACTIVE.clear()
    logcat._LAST.clear()
    yield
    logcat.shutdown_all()
    logcat._ACTIVE.clear()
    logcat._LAST.clear()


@pytest.fixture()
def api_client(logcat_env):
    app = FastAPI()
    app.include_router(logcat_capture.router)
    install_error_handlers(app)
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------- 手动 API
def test_logcat_api_start_status_stop_and_idempotent_stop(api_client, logcat_env, monkeypatch) -> None:
    process = _FakeProcess(["line1", "line2", "line3"])
    monkeypatch.setattr(logcat, "_spawn_logcat", lambda serial: process)
    with session_scope() as db:
        db.add(Run(id=7, script_name="manual", device_serial="fake-logcat", status="running"))

    response = api_client.post(
        "/api/devices/fake-logcat/logcat/start",
        json={"run_id": 7, "max_lines": 2},
    )
    assert response.status_code == 200, response.text
    started = response.json()
    assert started["active"] is True
    assert started["started"] is True
    assert started["max_lines"] == 2
    assert started["run_id"] == 7
    assert started["package_requested"] is None
    assert started["package_filter"] is False

    status = _wait_for(
        lambda: (
            body
            if (body := api_client.get("/api/devices/fake-logcat/logcat/status").json())["lines"] == 2
            else None
        ),
        message="ring buffer to reach max_lines",
    )
    assert status["active"] is True
    assert status["max_lines"] == 2
    assert status["started_at"]
    assert status["path"] is None
    assert status["size"] == 0

    response = api_client.post("/api/devices/fake-logcat/logcat/stop")
    assert response.status_code == 200, response.text
    stopped = response.json()
    assert stopped["stopped"] is True
    assert stopped["already_stopped"] is False
    assert stopped["active"] is False
    assert stopped["lines"] == 2
    assert stopped["run_id"] == 7
    assert stopped["url"].startswith("/artifacts/logcat/")
    path = Path(stopped["path"])
    assert path.is_file()
    assert path.read_text(encoding="utf-8").splitlines() == ["line2", "line3"]
    assert stopped["size"] == path.stat().st_size

    with session_scope() as db:
        artifacts = list(db.scalars(select(Artifact).where(Artifact.kind == "logcat")))
        assert len(artifacts) == 1
        assert artifacts[0].run_id == 7
        assert artifacts[0].path == str(path)
        assert artifacts[0].meta["started_by_run"] is False
        assert artifacts[0].meta["lines"] == 2

    # 重复 stop：幂等返回最近一次结果，不重复写 artifact
    repeat = api_client.post("/api/devices/fake-logcat/logcat/stop").json()
    assert repeat["stopped"] is False
    assert repeat["already_stopped"] is True
    assert repeat["path"] == str(path)
    assert repeat["lines"] == 2
    with session_scope() as db:
        assert len(list(db.scalars(select(Artifact).where(Artifact.kind == "logcat")))) == 1

    status = api_client.get("/api/devices/fake-logcat/logcat/status").json()
    assert status["active"] is False
    assert status["path"] == str(path)
    assert status["size"] == path.stat().st_size
    assert status["last"]["path"] == str(path)


def test_logcat_package_filter_keeps_only_matching_pid(api_client, logcat_env, monkeypatch) -> None:
    """package 过滤按 PID 保留日志；堆栈续行跟随上一条，其他进程丢弃。"""
    process = _FakeProcess(
        [
            "06-12 10:00:00.000  111  222 I App: keep",
            "    at com.example.keep(File.kt:1)",
            "06-12 10:00:00.100  999  222 I Other: drop",
            "06-12 10:00:00.200  111  222 E App: keep2",
        ]
    )
    monkeypatch.setattr(logcat, "_spawn_logcat", lambda serial: process)
    monkeypatch.setattr(logcat, "_resolve_pids", lambda serial, packages: {"111"})

    response = api_client.post(
        "/api/devices/fake-logcat/logcat/start",
        json={"package": "com.example.app", "max_lines": 10},
    )
    assert response.status_code == 200, response.text
    started = response.json()
    assert started["package_requested"] == "com.example.app"
    assert started["package_filter"] is True
    assert started["packages"] == ["com.example.app"]
    assert started["pids"] == ["111"]

    status = _wait_for(
        lambda: (
            body
            if (body := api_client.get("/api/devices/fake-logcat/logcat/status").json())["lines"] == 3
            else None
        ),
        message="filtered ring buffer to reach 3 lines",
    )
    assert status["package_filter"] is True

    stopped = api_client.post("/api/devices/fake-logcat/logcat/stop").json()
    path = Path(stopped["path"])
    assert path.read_text(encoding="utf-8").splitlines() == [
        "06-12 10:00:00.000  111  222 I App: keep",
        "    at com.example.keep(File.kt:1)",
        "06-12 10:00:00.200  111  222 E App: keep2",
    ]


def test_logcat_api_start_accepts_empty_body(api_client, monkeypatch) -> None:
    process = _FakeProcess(["only"])
    monkeypatch.setattr(logcat, "_spawn_logcat", lambda serial: process)
    response = api_client.post("/api/devices/fake-empty/logcat/start")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["active"] is True
    assert body["run_id"] is None
    api_client.post("/api/devices/fake-empty/logcat/stop")


def test_logcat_api_start_requires_enabled(api_client, logcat_env) -> None:
    """总开关关闭时手动采集也被拒绝；状态仍可读，方便停止残留会话。"""
    config.set_file_layer(
        {"runtime.data_dir": str(logcat_env), "capture.logcat_auto_attach": False}
    )
    response = api_client.post("/api/devices/fake-empty/logcat/start")
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "UNSUPPORTED"
    assert error["details"]["setting"] == "capture.logcat_auto_attach"

    status = api_client.get("/api/devices/fake-empty/logcat/status")
    assert status.status_code == 200
    assert status.json()["enabled"] is False


def test_logcat_api_start_rejects_offline_device(api_client) -> None:
    driver = device_registry.get_driver("fake-offline")
    driver.close()
    response = api_client.post("/api/devices/fake-offline/logcat/start")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "DEVICE_UNAVAILABLE"
    assert body["error"]["details"]["serial"] == "fake-offline"


# ---------------------------------------------------------------- 归属 / 幂等
def test_attach_is_idempotent_and_stop_only_own_run(logcat_env, monkeypatch) -> None:
    config.set_file_layer(
        {"runtime.data_dir": str(logcat_env), "capture.logcat_auto_attach": True}
    )
    process = _FakeProcess(["attached"])
    monkeypatch.setattr(logcat, "_spawn_logcat", lambda serial: process)
    with session_scope() as db:
        db.add(Run(id=42, script_name="x", device_serial="fake-idem", status="running"))

    first = logcat.attach_if_needed(42, {"logcat": True})
    assert first is not None
    second = logcat.attach_if_needed(42, {"logcat": True})
    assert second is first
    _wait_for(lambda: first.lines >= 1, message="attached line")
    assert logcat.is_capturing("fake-idem") is True

    # 别的 run / 手动 stop 入口都不能停它
    assert logcat.stop_run_capture(99, "fake-idem") is None
    assert logcat.stop_run_capture(42, "another-serial") is None
    assert logcat.is_capturing("fake-idem") is True

    path = logcat.stop_run_capture(42, "fake-idem")
    assert path is not None and path.is_file()
    assert logcat.stop_run_capture(42, "fake-idem") is None
    assert logcat.is_capturing("fake-idem") is False
    assert first.stop() == path  # 同实例重复 stop 也幂等

    with session_scope() as db:
        artifacts = list(db.scalars(select(Artifact).where(Artifact.kind == "logcat")))
        assert len(artifacts) == 1
        assert artifacts[0].run_id == 42
        assert artifacts[0].meta["started_by_run"] is True


def test_stop_run_capture_does_not_stop_manual_capture(logcat_env, monkeypatch) -> None:
    process = _FakeProcess(["manual"])
    monkeypatch.setattr(logcat, "_spawn_logcat", lambda serial: process)
    manual = logcat.start_capture("fake-manual", max_lines=5)
    try:
        _wait_for(lambda: manual.lines >= 1, message="manual line")
        assert logcat.stop_run_capture(1, "fake-manual") is None
        assert logcat.active_capture("fake-manual") is manual
        assert manual.started_by_run is False
    finally:
        path = logcat.stop_capture("fake-manual")
    assert path is not None and path.is_file()


# ---------------------------------------------------------------- 引擎生命周期
def test_engine_auto_attach_logcat_and_save_artifact(runner_env, logcat_cleanup, monkeypatch) -> None:
    runner_env.configure({"capture.logcat_auto_attach": True})
    process = _FakeProcess(["run-line1", "run-line2"])
    monkeypatch.setattr(logcat, "_spawn_logcat", lambda serial: process)

    script = make_script([wait_step("s1", ms=120)], settings={"logcat": True})
    run_id, run = run_script(script)

    assert run["status"] == "succeeded"
    assert logcat.is_capturing("fake-test") is False
    assert logcat.stop_run_capture(run_id, "fake-test") is None

    artifacts = [artifact for artifact in get_artifacts(run_id) if artifact.kind == "logcat"]
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.run_id == run_id
    assert artifact.meta["serial"] == "fake-test"
    assert artifact.meta["started_by_run"] is True
    path = Path(str(artifact.path))
    assert path.is_file()
    assert path.parent == config.artifacts_dir() / "logcat"
    assert artifact.size == path.stat().st_size


def test_engine_stops_logcat_when_run_raises(runner_env, logcat_cleanup, monkeypatch) -> None:
    runner_env.configure({"capture.logcat_auto_attach": True})
    process = _FakeProcess(["boom-line"])
    monkeypatch.setattr(logcat, "_spawn_logcat", lambda serial: process)

    def _boom(_ctx):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(engine, "run_script_with_context", _boom)
    script = make_script([wait_step("s1", ms=10)], settings={"logcat": True})
    run_id = insert_run(script)
    engine.start_run(run_id)
    run = wait_run(run_id)

    assert run["status"] == "failed"
    assert run["error"]["code"] == "INTERNAL"
    assert logcat.is_capturing("fake-test") is False
    artifacts = [artifact for artifact in get_artifacts(run_id) if artifact.kind == "logcat"]
    assert len(artifacts) == 1
    assert artifacts[0].run_id == run_id


def test_engine_does_not_stop_manual_logcat(runner_env, logcat_cleanup, monkeypatch) -> None:
    runner_env.configure({"capture.logcat_auto_attach": True})
    process = _FakeProcess(["manual-line"])
    monkeypatch.setattr(logcat, "_spawn_logcat", lambda serial: process)
    manual = logcat.start_capture("fake-test", max_lines=10)
    try:
        _wait_for(lambda: manual.lines >= 1, message="manual line")
        script = make_script([wait_step("s1", ms=60)], settings={"logcat": True})
        run_id, run = run_script(script)

        assert run["status"] == "succeeded"
        assert logcat.active_capture("fake-test") is manual
        assert [artifact for artifact in get_artifacts(run_id) if artifact.kind == "logcat"] == []
    finally:
        assert logcat.stop_capture("fake-test") is not None
    assert manual.started_by_run is False
