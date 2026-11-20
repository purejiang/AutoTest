"""be-runner 测试基座（不是测试用例本身）：独立 DB + FakeDriver + 脚本/运行构造器。

约定：
- 引擎永远在后台线程跑，测试用 `wait_run()` 轮询 DB 终态，保证每个用例 < 5s；
- `runner_env` 把 `engine.get_driver/acquire/release` 全部替换为内存实现，
  不依赖真机、也不依赖 be-core 的 device_registry 是否已落地；
- 选择器（`executors.resolve_target/check_condition/wait_for/wait_condition`）由
  `install_selectors()` 打成确定性桩，测试只通过 monkeypatch 本模块引用控制行为。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app import config, db, models
from app.android.driver import FakeDriver, HierarchyNode
from app.events import RunEventBus
from app.runner import controls, engine, executors
from app.runner.context import RunContext, RunSnapshot, default_limits

TERMINAL = {"succeeded", "failed", "aborted", "stopped", "interrupted"}

_BASE_STEP: dict[str, Any] = {
    "name": "",
    "description": "",
    "enabled": True,
    "when": None,
    "timeout_ms": None,
    "delay_before_ms": 0,
    "delay_after_ms": 0,
    "retry": None,
    "on_fail": None,
    "screenshot_on_fail": False,
    "rerun_safety": "safe",
    "tags": [],
}


# ---------------------------------------------------------------- 脚本构造
def make_step(step_id: str, step_type: str, params: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    step = {"id": step_id, "type": step_type, "params": params, "name": step_id}
    step.update(_BASE_STEP)
    step.update(overrides)
    return step


def click_step(step_id: str, **overrides: Any) -> dict[str, Any]:
    return make_step(
        step_id,
        "click",
        {"target": {"by": "text", "value": "登录"}},
        **overrides,
    )


def wait_step(step_id: str, ms: int, **overrides: Any) -> dict[str, Any]:
    return make_step(step_id, "wait", {"mode": "sleep", "ms": ms}, **overrides)


def make_script(
    steps: list[dict[str, Any]],
    name: str = "测试脚本",
    variables: dict[str, str] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "name": name,
        "notes": "",
        "variables": dict(variables or {}),
        "settings": dict(settings or {}),
        "defaults": {"retry": None},
        "steps": steps,
    }


# ---------------------------------------------------------------- DB 构造
def insert_script(data: dict[str, Any], script_id: str | None = None) -> str:
    script_id = script_id or f"script-{uuid.uuid4().hex[:8]}"
    with db.session_scope() as session:
        session.add(models.Script(id=script_id, name=str(data.get("name") or script_id), data=data))
    return script_id


def insert_run(
    script_data: dict[str, Any],
    serial: str = "fake-test",
    variables: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
    status: str = "queued",
    script_id: str | None = None,
) -> int:
    with db.session_scope() as session:
        run = models.Run(
            script_id=script_id,
            script_name=str(script_data.get("name") or "run"),
            script_snapshot=script_data,
            device_serial=serial,
            variables=dict(variables or {}),
            debug=dict(debug or {}),
            status=status,
            step_index=0,
            total_steps=len(script_data.get("steps") or []),
        )
        session.add(run)
        session.flush()
        return int(run.id)


def set_run_fields(run_id: int, **fields: Any) -> None:
    with db.session_scope() as session:
        run = session.get(models.Run, int(run_id))
        if run is not None:
            for key, value in fields.items():
                setattr(run, key, value)


def get_run(run_id: int) -> dict[str, Any] | None:
    with db.session_scope() as session:
        run = session.get(models.Run, int(run_id))
        if run is None:
            return None
        return {
            "id": int(run.id),
            "script_id": run.script_id,
            "script_name": run.script_name,
            "device_serial": run.device_serial,
            "status": run.status,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "current_step_id": run.current_step_id,
            "step_index": run.step_index,
            "total_steps": run.total_steps,
            "error": run.error,
            "summary": run.summary,
            "debug": run.debug,
            "created_at": run.created_at,
        }


def get_steps(run_id: int) -> list[dict[str, Any]]:
    from sqlalchemy import select

    with db.session_scope() as session:
        rows = (
            session.execute(
                select(models.StepResult)
                .where(models.StepResult.run_id == int(run_id))
                .order_by(models.StepResult.id)
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": int(row.id),
                "step_id": row.step_id,
                "step_name": row.step_name,
                "step_type": row.step_type,
                "step_index": row.step_index,
                "status": row.status,
                "attempts": row.attempts,
                "duration_ms": row.duration_ms,
                "error": row.error,
                "output": row.output,
            }
            for row in rows
        ]


def get_artifacts(run_id: int) -> list[models.Artifact]:
    from sqlalchemy import select

    with db.session_scope() as session:
        rows = (
            session.execute(select(models.Artifact).where(models.Artifact.run_id == int(run_id)))
            .scalars()
            .all()
        )
        return list(rows)


def get_events(run_id: int) -> list[dict[str, Any]]:
    return RunEventBus.replay(run_id, 0)


def event_types(run_id: int) -> list[str]:
    return [event["type"] for event in get_events(run_id)]


# ---------------------------------------------------------------- 等待
def wait_until(predicate, timeout: float = 5.0, interval: float = 0.01, message: str = "condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timeout waiting for {message}")


def wait_run(run_id: int, timeout: float = 6.0) -> dict[str, Any]:
    def _finished() -> dict[str, Any] | None:
        run = get_run(run_id)
        return run if run is not None and run["status"] in TERMINAL else None

    run = wait_until(_finished, timeout=timeout, message=f"run {run_id} terminal")
    assert run is not None
    # 终态先落库，engine 的 finally（release/cleanup）紧随其后；等它彻底收尾
    engine.wait_for_idle(timeout_s=2.0)
    return run


def run_script(
    script_data: dict[str, Any],
    serial: str = "fake-test",
    variables: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
    script_id: str | None = None,
    timeout: float = 6.0,
) -> tuple[int, dict[str, Any]]:
    run_id = insert_run(
        script_data,
        serial=serial,
        variables=variables,
        debug=debug,
        script_id=script_id,
    )
    engine.start_run(run_id)
    return run_id, wait_run(run_id, timeout=timeout)


# ---------------------------------------------------------------- RunContext 构造
def make_ctx(
    env: RunnerEnv,
    script_data: dict[str, Any],
    run_id: int = 1,
    variables: dict[str, str] | None = None,
    controls_obj: Any = None,
    depth: int = 0,
    limits: dict[str, Any] | None = None,
) -> RunContext:
    snapshot = RunSnapshot.from_data(script_data)
    return RunContext(
        run_id=run_id,
        driver=env.driver,
        script=snapshot,
        variables=variables,
        depth=depth,
        controls=controls_obj,
        limits=limits or default_limits(),
    )


def parsed_step(script_data: dict[str, Any], position: int = 0) -> Any:
    return RunSnapshot.from_data(script_data).parsed.steps[position]


# ---------------------------------------------------------------- 选择器桩
class SelectorStub:
    """确定性选择器：默认命中 (540, 710)，可编程失败序列与 when 结果。"""

    def __init__(self) -> None:
        self.wait_result = True
        self.check_result = True
        self.node = HierarchyNode(text="登录", bounds=[100, 640, 980, 780])
        self.resolve_errors: list[BaseException | None] = []
        self.resolve_calls: list[Any] = []
        self.wait_calls: list[tuple[Any, str, int]] = []
        self.check_calls: list[tuple[Any, str]] = []

    def resolve_target(
        self,
        driver: Any,
        target: Any,
        timeout_ms: int = 0,
        poll_interval_ms: int = 350,
        should_abort: Any = None,
    ) -> SimpleNamespace:
        self.resolve_calls.append(target)
        if self.resolve_errors:
            error = self.resolve_errors.pop(0)
            if error is not None:
                raise error
        return SimpleNamespace(
            x=540,
            y=710,
            node=self.node,
            bounds=[100, 640, 980, 780],
        )

    def check_condition(self, driver: Any, target: Any, state: str = "visible") -> bool:
        self.check_calls.append((target, state))
        return bool(self.check_result)

    def wait_condition(self, ctx: Any, target: Any, state: str, timeout_ms: int, interval_ms: Any = None) -> bool:
        self.wait_calls.append((target, state, int(timeout_ms)))
        return bool(self.wait_result)

    def wait_for(
        self,
        driver: Any,
        target: Any,
        state: str,
        timeout_ms: int,
        poll_interval_ms: int = 350,
        should_abort: Any = None,
    ) -> bool:
        self.wait_calls.append((target, state, int(timeout_ms)))
        return bool(self.wait_result)


def install_selectors(monkeypatch: pytest.MonkeyPatch) -> SelectorStub:
    stub = SelectorStub()
    monkeypatch.setattr(executors, "resolve_target", stub.resolve_target)
    monkeypatch.setattr(executors, "check_condition", stub.check_condition)
    monkeypatch.setattr(executors, "wait_for", stub.wait_for)
    monkeypatch.setattr(executors, "wait_condition", stub.wait_condition)
    return stub


# ---------------------------------------------------------------- 主夹具
class RunnerEnv:
    def __init__(self, driver: FakeDriver, data_dir: Path, base_config: dict[str, Any]) -> None:
        self.driver = driver
        self.data_dir = data_dir
        self.acquisitions: list[tuple[str, str, str]] = []
        self.releases: list[tuple[str, str, str]] = []
        self._base_config = base_config
        self.started: list[int] = []

    def configure(self, updates: dict[str, Any]) -> None:
        values = dict(self._base_config)
        values.update(updates)
        config.set_db_overrides(values)


@pytest.fixture()
def runner_env(temp_db, tmp_path, monkeypatch):
    config.reset_to_defaults()
    RunEventBus.reset()
    controls.cleanup_all()
    data_dir = tmp_path / "data"
    base_config: dict[str, Any] = {
        "runtime.data_dir": str(data_dir),
        "engine.poll_interval_ms": 100,
        "engine.default_timeout_ms": 800,
        "engine.max_total_ms": 60_000,
        "engine.max_steps": 1000,
        "engine.pause_abandon_grace_s": 1,
        "engine.step_min_timeout_ms": 50,
    }
    config.set_db_overrides(base_config)
    env = RunnerEnv(FakeDriver(serial="fake-test"), data_dir, base_config)

    def _get_driver(serial: Any = None):
        return env.driver

    def _acquire(serial: str, purpose: str, ref_id: Any) -> None:
        env.acquisitions.append((str(serial), str(purpose), str(ref_id)))

    def _release(serial: str, purpose: str, ref_id: Any) -> None:
        env.releases.append((str(serial), str(purpose), str(ref_id)))

    monkeypatch.setattr(engine, "get_driver", _get_driver)
    monkeypatch.setattr(engine, "acquire", _acquire)
    monkeypatch.setattr(engine, "release", _release)
    try:
        yield env
    finally:
        engine.wait_for_idle(5.0)
        controls.cleanup_all()
        RunEventBus.reset()
        config.reset_to_defaults()
