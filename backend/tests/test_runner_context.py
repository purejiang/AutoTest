"""RunContext 单测：插值、暂停/停止、三环度量、截图产物、子上下文共享状态。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app import config, db, models
from app.events import RunEventBus
from app.runner import controls
from app.runner.context import (
    BudgetExceeded,
    RunSnapshot,
    RunStopped,
    TotalTimeout,
    default_limits,
)
from app.schemas import Target
from tests.test_runner_kits import click_step, get_events, insert_run, make_ctx, make_script, wait_step
from tests.test_runner_kits import get_artifacts as get_artifacts_rows
from tests.test_runner_kits import runner_env as runner_env


@pytest.fixture(autouse=True)
def _clean_bus():
    RunEventBus.reset()
    controls.cleanup_all()
    yield
    controls.cleanup_all()
    RunEventBus.reset()


# ---------------------------------------------------------------- 插值
def test_interpolate_known_and_unknown_keeps_placeholder(runner_env):
    ctx = make_ctx(runner_env, make_script([click_step("s1")], variables={"user": "alice"}))
    assert ctx.interpolate("hi {{user}} / {{missing}}") == "hi alice / {{missing}}"
    assert ctx.interpolate(123) == 123

    warnings = [
        event
        for event in get_events(ctx.run_id)
        if event["type"] == "log" and event["payload"]["level"] == "warn"
    ]
    assert len(warnings) == 1
    assert "missing" in warnings[0]["payload"]["message"]


def test_interpolate_target_keeps_point(runner_env):
    ctx = make_ctx(runner_env, make_script([click_step("s1")], variables={"value": "登录"}))
    target = Target(by="text", value="{{value}}")
    interpolated = ctx.interpolate_target(target)
    assert interpolated.value == "登录"

    point = Target(by="point", x=10, y=20)
    assert ctx.interpolate_target(point) is point
    assert ctx.interpolate_target(None) is None
    assert ctx.interpolate_target({"by": "text", "value": "{{user}}"})["value"] == "{{user}}"


# ---------------------------------------------------------------- 停止 / 暂停
def test_sleep_interruptible_raises_on_stop(runner_env):
    control = controls.create_control(100)
    ctx = make_ctx(runner_env, make_script([click_step("s1")]), controls_obj=control)
    control.stop_event.set()
    started = time.monotonic()
    with pytest.raises(RunStopped):
        ctx.sleep_interruptible(2000)
    assert time.monotonic() - started < 0.5


def test_sleep_interruptible_blocks_while_paused(runner_env):
    run_id = 101
    control = controls.create_control(run_id)
    ctx = make_ctx(runner_env, make_script([click_step("s1")]), run_id=run_id, controls_obj=control)
    controls.request_pause(run_id)
    finished = threading.Event()

    def worker() -> None:
        ctx.sleep_interruptible(150)
        finished.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    time.sleep(0.12)
    assert not finished.is_set()
    controls.request_resume(run_id)
    thread.join(2)
    assert finished.is_set()


def test_wait_if_paused_announces_once_and_resumes(runner_env):
    run_id = 102
    control = controls.create_control(run_id)
    ctx = make_ctx(runner_env, make_script([click_step("s1")]), run_id=run_id, controls_obj=control)
    phases: list[bool] = []
    ctx.on_pause = phases.append
    controls.request_pause(run_id)

    finished = threading.Event()

    def worker() -> None:
        ctx.wait_if_paused()
        finished.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    time.sleep(0.1)
    assert not finished.is_set()
    assert controls.is_paused(run_id)

    controls.request_resume(run_id)
    thread.join(2)
    assert finished.is_set()
    assert phases == [True, False]


def test_stop_while_paused_raises_and_unblocks(runner_env):
    run_id = 103
    control = controls.create_control(run_id)
    ctx = make_ctx(runner_env, make_script([click_step("s1")]), run_id=run_id, controls_obj=control)
    controls.request_pause(run_id)
    finished = threading.Event()
    result: dict[str, bool] = {}

    def worker() -> None:
        try:
            ctx.wait_if_paused()
        except RunStopped:
            result["stopped"] = True
        finally:
            finished.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    time.sleep(0.05)
    controls.request_stop(run_id)
    thread.join(2)

    assert finished.is_set()
    assert result.get("stopped") is True


# ---------------------------------------------------------------- 三环度量
def test_active_elapsed_excludes_paused_time(runner_env):
    ctx = make_ctx(runner_env, make_script([click_step("s1")]))
    time.sleep(0.06)
    first = ctx.active_elapsed_ms()
    assert first >= 40

    ctx.mark_pause_started()
    time.sleep(0.12)
    during_pause = ctx.active_elapsed_ms()
    assert during_pause - first < 60
    ctx.mark_pause_ended()

    time.sleep(0.06)
    after = ctx.active_elapsed_ms()
    assert after - during_pause >= 40


def test_step_budget_raises_when_exceeded(runner_env):
    limits = default_limits()
    limits["max_steps"] = 2
    ctx = make_ctx(runner_env, make_script([click_step("s1")]), limits=limits)
    assert ctx.check_step_budget() == 1
    assert ctx.check_step_budget() == 2
    with pytest.raises(BudgetExceeded):
        ctx.check_step_budget()


def test_total_timeout_raises(runner_env):
    limits = default_limits()
    limits["max_total_ms"] = 20
    ctx = make_ctx(runner_env, make_script([click_step("s1")]), limits=limits)
    time.sleep(0.05)
    with pytest.raises(TotalTimeout):
        ctx.check_total_timeout()


def test_step_deadline_marks_abort_requested(runner_env):
    ctx = make_ctx(runner_env, make_script([click_step("s1")]))
    assert not ctx.abort_requested()
    ctx.begin_step(1000)
    assert not ctx.step_expired()
    ctx.begin_step(0)
    time.sleep(0.01)
    assert ctx.step_expired()
    assert ctx.abort_requested()
    ctx.end_step()
    assert not ctx.step_expired()


# ---------------------------------------------------------------- 截图产物
def test_save_screenshot_writes_file_and_artifact(runner_env):
    script = make_script([wait_step("s1", ms=0)])
    run_id = insert_run(script)
    ctx = make_ctx(runner_env, script, run_id=run_id)
    ctx.current_step_id = "s1"

    info = ctx.save_screenshot("my-shot", step_result_id=None)
    path = Path(info["path"])
    assert path.is_file()
    assert path.read_bytes().startswith(b"\x89PNG")
    assert path.parent == config.artifacts_dir() / f"run_{run_id}"

    artifacts = get_artifacts_rows(run_id)
    assert len(artifacts) == 1
    assert artifacts[0].name == "my-shot"
    assert artifacts[0].size == info["size"]
    assert info["artifact_id"] == artifacts[0].id

    screenshots = [event for event in get_events(run_id) if event["type"] == "screenshot"]
    assert screenshots
    assert screenshots[-1]["payload"]["artifact_id"] == artifacts[0].id
    assert screenshots[-1]["payload"]["step_id"] == "s1"


# ---------------------------------------------------------------- 子上下文
def test_child_context_shares_shared_state(runner_env):
    parent = make_ctx(
        runner_env,
        make_script([click_step("s1")], variables={"outer": "1"}),
        variables={"request": "2"},
    )
    child_snapshot = RunSnapshot.from_data(
        make_script([click_step("c1")], name="子脚本", variables={"inner": "3"})
    )
    child = parent.child(child_snapshot, variables={"param": "4"})

    assert child.depth == 1
    assert child._shared is parent._shared
    assert child.variables == {"outer": "1", "request": "2", "inner": "3", "param": "4"}
    assert parent.depth == 0


# ---------------------------------------------------------------- Artifact ORM 冒烟
def test_artifact_row_model_fields(temp_db):
    with db.session_scope() as session:
        artifact = models.Artifact(run_id=None, kind="screenshot", name="n", path="/tmp/x.png", size=1)
        session.add(artifact)
        session.flush()
        assert artifact.id is not None
