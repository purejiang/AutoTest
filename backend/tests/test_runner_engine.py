"""执行引擎集成测试（FakeDriver + 确定性选择器桩 + 真实线程池/DB/事件）。

覆盖：顺序执行/事件 seq、when、retry、on_fail(abort|continue|goto)、断言不重试、
shell、sub_script 嵌套与深度、三环保护、pause/resume、stop、debug step/breakpoint/run_from。
"""

from __future__ import annotations

from app.android.driver import CommandResult
from app.runner import controls, engine, executors
from tests.test_runner_kits import (
    click_step,
    get_events,
    get_run,
    get_steps,
    insert_run,
    insert_script,
    install_selectors,
    make_script,
    make_step,
    run_script,
    wait_run,
    wait_step,
    wait_until,
)
from tests.test_runner_kits import runner_env as runner_env


def _fail_events(run_id: int) -> list[dict]:
    return [event for event in get_events(run_id) if event["type"] == "error"]


def _paused_events(run_id: int) -> list[dict]:
    return [event for event in get_events(run_id) if event["type"] == "run_paused"]


# ---------------------------------------------------------------- 基础顺序
def test_sequential_execution_events_and_results(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    script = make_script([click_step("s1"), click_step("s2"), wait_step("s3", ms=30)])

    run_id, run = run_script(script)

    assert run["status"] == "succeeded"
    assert run["ended_at"]
    assert run["summary"]["passed_steps"] == 3
    assert run["summary"]["failed_steps"] == 0
    assert run["summary"]["duration_ms"] >= 30

    steps = get_steps(run_id)
    assert [step["step_id"] for step in steps] == ["s1", "s2", "s3"]
    assert {step["status"] for step in steps} == {"passed"}
    assert len(runner_env.driver.calls_of("click")) == 2
    assert ("fake-test", "run", str(run_id)) in runner_env.releases

    events = get_events(run_id)
    seqs = [event["seq"] for event in events]
    assert seqs == list(range(1, len(events) + 1))
    types = [event["type"] for event in events]
    assert types[0] == "run_started"
    assert types.count("step_started") == 3
    assert types.count("step_finished") == 3
    assert "run_finished" in types
    finished = next(event for event in events if event["type"] == "run_finished")
    assert finished["payload"]["status"] == "succeeded"
    assert finished["payload"]["passed_steps"] == 3
    assert types[-1] == "run_state"


def test_disabled_step_is_skipped(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    script = make_script([click_step("s1", enabled=False), click_step("s2")])

    run_id, run = run_script(script)

    assert run["status"] == "succeeded"
    steps = get_steps(run_id)
    assert [(step["step_id"], step["status"]) for step in steps] == [("s1", "skipped"), ("s2", "passed")]
    skipped = [event for event in get_events(run_id) if event["type"] == "step_skipped"]
    assert skipped[0]["payload"] == {"step_id": "s1", "reason": "disabled"}


# ---------------------------------------------------------------- when
def test_when_require_not_met_goes_on_fail_continue(runner_env, monkeypatch):
    stub = install_selectors(monkeypatch)
    stub.wait_result = False
    script = make_script(
        [
            click_step(
                "s1",
                when={"target": {"by": "text", "value": "不存在"}, "mode": "require", "timeout_ms": 100},
                on_fail={"action": "continue"},
            ),
            click_step("s2"),
        ]
    )

    run_id, run = run_script(script)

    assert run["status"] == "failed"
    assert run["summary"]["failed_steps"] == 1
    assert run["summary"]["passed_steps"] == 1
    steps = get_steps(run_id)
    assert [step["status"] for step in steps] == ["failed", "passed"]
    assert steps[0]["error"]["code"] == "WHEN_NOT_MET"
    assert len(runner_env.driver.calls_of("click")) == 1
    assert stub.wait_calls and stub.wait_calls[0][2] == 100


def test_when_optional_not_met_is_skipped(runner_env, monkeypatch):
    stub = install_selectors(monkeypatch)
    stub.wait_result = False
    script = make_script(
        [
            click_step(
                "s1",
                when={"target": {"by": "text", "value": "不存在"}, "mode": "optional", "timeout_ms": 100},
            ),
            click_step("s2"),
        ]
    )

    run_id, run = run_script(script)

    assert run["status"] == "succeeded"
    assert run["summary"]["skipped_steps"] == 1
    assert [step["status"] for step in get_steps(run_id)] == ["skipped", "passed"]
    skipped = [event for event in get_events(run_id) if event["type"] == "step_skipped"]
    assert skipped[0]["payload"]["reason"] == "when_not_met"


def test_when_require_met_executes_step(runner_env, monkeypatch):
    stub = install_selectors(monkeypatch)
    stub.wait_result = True
    script = make_script(
        [
            click_step(
                "s1",
                when={"target": {"by": "text", "value": "登录"}, "mode": "require", "timeout_ms": 200},
            )
        ]
    )

    run_id, run = run_script(script)

    assert run["status"] == "succeeded"
    assert get_steps(run_id)[0]["status"] == "passed"
    assert stub.wait_calls


# ---------------------------------------------------------------- retry
def test_retry_then_success(runner_env, monkeypatch):
    stub = install_selectors(monkeypatch)
    stub.resolve_errors = [executors.TargetNotFound("first attempt"), None]
    script = make_script([click_step("s1", retry={"count": 1, "interval_ms": 10})])

    run_id, run = run_script(script)

    assert run["status"] == "succeeded"
    steps = get_steps(run_id)
    assert len(steps) == 1
    assert steps[0]["status"] == "passed"
    assert steps[0]["attempts"] == 2
    types = [event["type"] for event in get_events(run_id)]
    assert types.count("step_retry") == 1
    assert types.count("step_started") == 2
    retry_event = next(event for event in get_events(run_id) if event["type"] == "step_retry")
    assert retry_event["payload"]["attempt"] == 1
    assert retry_event["payload"]["error"]["code"] == "TARGET_NOT_FOUND"


def test_retry_exhausted_aborts(runner_env, monkeypatch):
    stub = install_selectors(monkeypatch)
    stub.resolve_errors = [executors.TargetNotFound("a"), executors.TargetNotFound("b")]
    script = make_script(
        [click_step("s1", retry={"count": 1, "interval_ms": 10}), click_step("s2")]
    )

    run_id, run = run_script(script)

    assert run["status"] == "failed"
    assert run["error"]["code"] == "TARGET_NOT_FOUND"
    steps = get_steps(run_id)
    assert steps[0]["status"] == "failed"
    assert steps[0]["attempts"] == 2
    assert steps[1]["status"] == "skipped"
    assert steps[1]["error"]["code"] == "AFTER_FAIL"
    assert [event["type"] for event in get_events(run_id)].count("step_retry") == 1


def test_goto_jumps_and_resets_retry(runner_env, monkeypatch):
    stub = install_selectors(monkeypatch)
    stub.resolve_errors = [None, executors.TargetNotFound("fail once"), None, None]
    script = make_script(
        [
            click_step("s1"),
            click_step("s2", on_fail={"action": "goto", "target_id": "s1"}),
            click_step("s3"),
        ]
    )

    run_id, run = run_script(script)

    assert run["status"] == "succeeded"
    steps = get_steps(run_id)
    assert [step["step_id"] for step in steps] == ["s1", "s2", "s1", "s2", "s3"]
    assert [step["status"] for step in steps] == ["passed", "failed", "passed", "passed", "passed"]
    # s2 第一次在目标解析阶段失败（未注入 click），之后 4 次点击均成功
    assert len(stub.resolve_calls) == 5
    assert len(runner_env.driver.calls_of("click")) == 4


# ---------------------------------------------------------------- assert
def test_assert_failure_is_not_retried(runner_env, monkeypatch):
    stub = install_selectors(monkeypatch)
    stub.check_result = False
    script = make_script(
        [
            make_step(
                "a1",
                "assert",
                {"kind": "exists", "target": {"by": "text", "value": "登录"}},
                retry={"count": 3, "interval_ms": 10},
            ),
            click_step("s2"),
        ]
    )

    run_id, run = run_script(script)

    assert run["status"] == "failed"
    steps = get_steps(run_id)
    assert steps[0]["status"] == "failed"
    assert steps[0]["attempts"] == 1
    assert steps[0]["error"]["code"] == "ASSERTION_FAILED"
    types = [event["type"] for event in get_events(run_id)]
    assert types.count("step_retry") == 0
    assert types.count("assert_result") == 1
    assert types.count("step_started") == 1


# ---------------------------------------------------------------- shell
def test_shell_exit_code_behavior(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    runner_env.driver.shell_responses["boom"] = CommandResult(code=1, stdout="", stderr="bad")
    ok_script = make_script(
        [
            make_step("s1", "shell", {"command": "boom", "check_exit_code": False}),
            make_step("s2", "shell", {"command": "echo hi"}),
        ]
    )

    run_id, run = run_script(ok_script)

    assert run["status"] == "succeeded"
    steps = get_steps(run_id)
    assert steps[0]["output"]["code"] == 1
    assert steps[1]["output"]["stdout"].strip() == "hi"

    fail_script = make_script([make_step("x1", "shell", {"command": "boom"})])
    _fail_id, failed_run = run_script(fail_script)
    assert failed_run["status"] == "failed"
    assert failed_run["error"]["code"] == "SHELL_EXIT_CODE"


# ---------------------------------------------------------------- sub_script
def test_sub_script_nested_execution(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    child_id = insert_script(make_script([click_step("c1")], name="子脚本"))
    parent = make_script([make_step("p1", "sub_script", {"script_id": child_id})], name="父脚本")

    run_id, run = run_script(parent)

    assert run["status"] == "succeeded"
    steps = get_steps(run_id)
    assert {step["step_id"] for step in steps} == {"c1", "p1"}
    assert {step["status"] for step in steps} == {"passed"}
    assert len(runner_env.driver.calls_of("click")) == 1


def test_sub_script_depth_limit_fails_run(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    leaf_id = insert_script(make_script([click_step("leaf")], name="leaf"))
    current_id = leaf_id
    for depth in range(5):
        current_id = insert_script(
            make_script(
                [make_step(f"sub{depth}", "sub_script", {"script_id": current_id})],
                name=f"chain-{depth}",
            )
        )
    parent = make_script([make_step("p1", "sub_script", {"script_id": current_id})])

    run_id, run = run_script(parent)

    assert run["status"] == "failed"
    codes = {(step["error"] or {}).get("code") for step in get_steps(run_id)}
    assert "SUB_SCRIPT_DEPTH_EXCEEDED" in codes


# ---------------------------------------------------------------- 三环保护
def test_max_steps_budget_truncates_run(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    runner_env.configure({"engine.max_steps": 3})
    script = make_script([click_step(f"s{i}") for i in range(1, 6)])

    run_id, run = run_script(script)

    assert run["status"] == "failed"
    assert run["error"]["code"] == "MAX_STEPS_EXCEEDED"
    steps = get_steps(run_id)
    assert [step["status"] for step in steps] == ["passed", "passed", "passed", "failed"]
    assert run["summary"]["passed_steps"] == 3
    assert run["summary"]["failed_steps"] == 1


def test_total_timeout_protection(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    runner_env.configure({"engine.max_total_ms": 150})
    script = make_script([wait_step("s1", ms=300, timeout_ms=5000), click_step("s2")])

    run_id, run = run_script(script)

    assert run["status"] == "failed"
    assert run["error"]["code"] == "TOTAL_TIMEOUT"
    statuses = [step["status"] for step in get_steps(run_id)]
    assert "failed" in statuses


def test_step_timeout_marks_step_failed(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    script = make_script([wait_step("s1", ms=5000, timeout_ms=120)])

    run_id, run = run_script(script)

    assert run["status"] == "failed"
    steps = get_steps(run_id)
    assert steps[0]["status"] == "failed"
    assert steps[0]["error"]["code"] == "STEP_TIMEOUT"


# ---------------------------------------------------------------- pause / resume
def test_pause_then_hot_resume(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    script = make_script([wait_step("s1", ms=500, timeout_ms=5000), click_step("s2")])
    run_id = insert_run(script)
    engine.start_run(run_id)
    wait_until(lambda: controls.has_control(run_id), message="engine control registered")
    wait_until(lambda: get_run(run_id)["status"] == "running", message="run running")

    paused_state = controls.request_pause(run_id)
    assert paused_state["paused"] is True
    wait_until(lambda: get_run(run_id)["status"] == "paused", message="run paused")
    assert runner_env.driver.calls_of("click") == []

    resumed_state = controls.request_resume(run_id)
    assert resumed_state["paused"] is False
    run = wait_run(run_id)

    assert run["status"] == "succeeded"
    paused_events = _paused_events(run_id)
    assert paused_events and paused_events[0]["payload"]["reason"] == "user"
    resumed_events = [event for event in get_events(run_id) if event["type"] == "run_resumed"]
    assert resumed_events and resumed_events[0]["payload"]["resume_kind"] == "hot"
    assert resumed_events[0]["payload"]["warnings"] == []


# ---------------------------------------------------------------- stop
def test_stop_interrupts_running_step(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    script = make_script([wait_step("s1", ms=5000, timeout_ms=10_000)])
    run_id = insert_run(script)
    engine.start_run(run_id)
    wait_until(
        lambda: any(event["type"] == "step_started" for event in get_events(run_id)),
        message="step started",
    )

    controls.request_stop(run_id)
    run = wait_run(run_id, timeout=3)

    assert run["status"] == "stopped"
    assert run["error"]["code"] == "STOPPED"
    steps = get_steps(run_id)
    assert steps and steps[0]["status"] == "failed"
    assert steps[0]["error"]["code"] == "STOPPED"
    assert ("fake-test", "run", str(run_id)) in runner_env.releases


# ---------------------------------------------------------------- debug
def test_debug_step_mode_pauses_after_each_step(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    script = make_script([click_step("s1"), click_step("s2")])
    run_id = insert_run(script, debug={"step_mode": True, "breakpoints": []})
    engine.start_run(run_id)

    wait_until(lambda: len(_paused_events(run_id)) >= 1, message="first step_mode pause")
    assert get_run(run_id)["status"] == "paused"
    assert len(runner_env.driver.calls_of("click")) == 1
    assert _paused_events(run_id)[0]["payload"]["reason"] == "step_mode"

    controls.request_debug(run_id, "step")
    wait_until(lambda: len(_paused_events(run_id)) >= 2, message="second step_mode pause")
    assert len(runner_env.driver.calls_of("click")) == 2

    controls.request_debug(run_id, "continue")
    run = wait_run(run_id)
    assert run["status"] == "succeeded"


def test_breakpoint_pauses_before_step_and_resumes(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    script = make_script([click_step("s1"), click_step("s2")])
    run_id = insert_run(script, debug={"step_mode": False, "breakpoints": ["s1"]})
    engine.start_run(run_id)

    wait_until(lambda: len(_paused_events(run_id)) >= 1, message="breakpoint pause")
    assert _paused_events(run_id)[0]["payload"]["reason"] == "breakpoint"
    assert runner_env.driver.calls_of("click") == []

    controls.request_resume(run_id)
    run = wait_run(run_id)
    assert run["status"] == "succeeded"
    assert len(runner_env.driver.calls_of("click")) == 2


def test_debug_run_from_jumps_to_target_step(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    script = make_script([click_step("s1"), click_step("s2"), click_step("s3")])
    run_id = insert_run(script, debug={"step_mode": False, "breakpoints": ["s1"]})
    engine.start_run(run_id)

    wait_until(lambda: len(_paused_events(run_id)) >= 1, message="breakpoint pause")
    controls.request_debug(run_id, "run_from", "s3")
    run = wait_run(run_id)

    assert run["status"] == "succeeded"
    steps = get_steps(run_id)
    assert [step["step_id"] for step in steps] == ["s3"]
    assert len(runner_env.driver.calls_of("click")) == 1


# ---------------------------------------------------------------- 入口健壮性
def test_start_run_is_idempotent_for_active_run(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    script = make_script([wait_step("s1", ms=80)])
    run_id = insert_run(script)
    engine.start_run(run_id)
    engine.start_run(run_id)  # 第二次必须被忽略
    run = wait_run(run_id)
    assert run["status"] == "succeeded"
    types = [event["type"] for event in get_events(run_id)]
    assert types.count("run_started") == 1


def test_invalid_snapshot_fails_run(runner_env):
    run_id = insert_run({"name": "坏脚本", "steps": []})
    engine.start_run(run_id)
    run = wait_run(run_id)
    assert run["status"] == "failed"
    assert run["error"]["code"] == "VALIDATION_ERROR"


def test_engine_does_not_hang_when_driver_missing(runner_env, monkeypatch):
    monkeypatch.setattr(
        engine,
        "get_driver",
        lambda serial=None: (_ for _ in ()).throw(RuntimeError("no device")),
    )
    script = make_script([click_step("s1")])
    run_id = insert_run(script)
    engine.start_run(run_id)
    run = wait_run(run_id)
    assert run["status"] == "failed"
    assert run["error"]["code"] == "DEVICE_UNAVAILABLE"
