"""执行引擎集成测试（FakeDriver + 确定性选择器桩 + 真实线程池/DB/事件）。

覆盖：顺序执行/事件 seq、when、retry、on_fail(abort|continue|goto)、断言不重试、
shell、sub_script 嵌套与深度、三环保护、pause/resume、stop、debug step/breakpoint/run_from。
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from app.android.driver import CommandResult
from app.capture import logcat, network
from app.db import session_scope
from app.models import Artifact
from app.runner import controls, engine, executors
from app.runner.context import RunSnapshot
from tests.test_runner_kits import (
    click_step,
    get_events,
    get_run,
    get_steps,
    insert_run,
    insert_script,
    install_selectors,
    make_ctx,
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
def _when(condition: dict, *, mode: str = "require", timeout_ms: int = 0) -> dict:
    return {
        "mode": mode,
        "timeout_ms": timeout_ms,
        "poll_interval_ms": 50,
        "condition": condition,
    }


def _step_events(run_id: int, step_id: str) -> list[dict]:
    return [
        event
        for event in get_events(run_id)
        if (event["payload"] or {}).get("step_id") == step_id
    ]


def _event_types(run_id: int, step_id: str) -> list[str]:
    return [event["type"] for event in _step_events(run_id, step_id)]


def test_when_require_not_met_goes_on_fail_continue(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    script = make_script(
        [
            click_step(
                "s1",
                when=_when({"type": "target", "target": {"by": "text", "value": "不存在"}}),
                on_fail={"action": "continue"},
            ),
            click_step("s2"),
        ]
    )

    run_id, run = run_script(script)

    assert run["status"] == "failed"
    assert run["summary"]["failed_steps"] == 1
    assert run["summary"]["passed_steps"] == 1
    assert run["error"]["details"]["when"]["met"] is False
    steps = get_steps(run_id)
    assert [step["status"] for step in steps] == ["failed", "passed"]
    assert steps[0]["error"]["code"] == "WHEN_NOT_MET"
    when_info = steps[0]["error"]["details"]["when"]
    assert when_info["met"] is False
    assert when_info["waited_ms"] >= 0
    assert "target(text='不存在'" in when_info["condition_summary"]
    assert steps[0]["output"]["when"] == when_info
    assert len(runner_env.driver.calls_of("click")) == 1
    # 顺序：成功/失败事件对：when_started -> when_result -> step_finished
    assert _event_types(run_id, "s1") == ["when_started", "when_result", "step_finished"]
    finished = next(
        event
        for event in get_events(run_id)
        if event["type"] == "step_finished" and event["payload"]["step_id"] == "s1"
    )
    assert finished["payload"]["when"] == when_info
    assert finished["payload"]["duration_ms"] == 0


def test_when_optional_not_met_is_skipped(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    script = make_script(
        [
            click_step(
                "s1",
                when=_when(
                    {"type": "target", "target": {"by": "text", "value": "不存在"}},
                    mode="optional",
                ),
            ),
            click_step("s2"),
        ]
    )

    run_id, run = run_script(script)

    assert run["status"] == "succeeded"
    assert run["summary"]["skipped_steps"] == 1
    steps = get_steps(run_id)
    assert [step["status"] for step in steps] == ["skipped", "passed"]
    when_info = steps[0]["output"]["when"]
    assert when_info["met"] is False
    assert steps[0]["output"]["reason"] == "when_not_met"
    assert _event_types(run_id, "s1") == ["when_started", "when_result", "step_skipped"]
    skipped = next(
        event
        for event in get_events(run_id)
        if event["type"] == "step_skipped" and event["payload"]["step_id"] == "s1"
    )
    assert skipped["payload"]["reason"] == "when_not_met"
    assert skipped["payload"]["when"] == when_info


def test_when_require_met_executes_step(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    script = make_script(
        [
            click_step(
                "s1",
                when=_when({"type": "target", "target": {"by": "text", "value": "登录"}}),
            )
        ]
    )

    run_id, run = run_script(script)

    assert run["status"] == "succeeded"
    step = get_steps(run_id)[0]
    assert step["status"] == "passed"
    assert step["output"]["when"]["met"] is True
    assert step["output"]["when"]["waited_ms"] >= 0
    assert "target(text='登录'" in step["output"]["when"]["condition_summary"]
    assert step["output"]["x"] == 540  # 动作输出保留
    # 顺序：when_started -> when_result -> step_started -> step_finished
    assert _event_types(run_id, "s1") == [
        "when_started",
        "when_result",
        "step_started",
        "step_finished",
    ]
    assert len(runner_env.driver.calls_of("click")) == 1


def test_unconditional_step_output_and_events_have_no_when(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    script = make_script([click_step("s1")])

    run_id, run = run_script(script)

    assert run["status"] == "succeeded"
    step = get_steps(run_id)[0]
    assert "when" not in (step["output"] or {})
    finished = next(
        event
        for event in get_events(run_id)
        if event["type"] == "step_finished" and event["payload"]["step_id"] == "s1"
    )
    assert "when" not in finished["payload"]


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


def test_script_default_retry_applies_when_node_inherits(runner_env, monkeypatch):
    """节点 retry=null 时继承脚本 settings.retry。"""
    stub = install_selectors(monkeypatch)
    stub.resolve_errors = [executors.TargetNotFound("first"), None]
    script = make_script(
        [click_step("s1")],
        settings={"retry": {"count": 1, "interval_ms": 10}},
    )

    run_id, run = run_script(script)

    assert run["status"] == "succeeded"
    assert get_steps(run_id)[0]["attempts"] == 2
    assert [event["type"] for event in get_events(run_id)].count("step_retry") == 1


def test_node_retry_zero_disables_script_default(runner_env, monkeypatch):
    """节点 retry={count:0} 是显式覆盖：不再继承脚本默认重试。"""
    stub = install_selectors(monkeypatch)
    stub.resolve_errors = [executors.TargetNotFound("only attempt")]
    script = make_script(
        [click_step("s1", retry={"count": 0, "interval_ms": 10})],
        settings={"retry": {"count": 3, "interval_ms": 10}},
    )

    run_id, run = run_script(script)

    assert run["status"] == "failed"
    steps = get_steps(run_id)
    assert steps[0]["attempts"] == 1
    assert [event["type"] for event in get_events(run_id)].count("step_retry") == 0


def test_legacy_defaults_retry_is_used_at_runtime(runner_env, monkeypatch):
    """旧脚本只有 defaults.retry：运行时应折叠到 settings.retry 后生效。"""
    stub = install_selectors(monkeypatch)
    stub.resolve_errors = [executors.TargetNotFound("first"), None]
    script = make_script([click_step("s1")])
    script["settings"] = {"retry": None}
    script["defaults"] = {"retry": {"count": 1, "interval_ms": 10}}

    run_id, run = run_script(script)

    assert run["status"] == "succeeded"
    assert get_steps(run_id)[0]["attempts"] == 2
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


def test_logcat_packages_from_script_settings() -> None:
    """logcat_scope=app：显式包名优先，否则收集 app 节点包名。"""
    app_step = make_step("s1", "app", {"action": "launch", "package": "com.example.one"})
    second = make_step("s2", "app", {"action": "restart", "package": "com.example.two"})
    explicit = RunSnapshot.from_data(
        make_script(
            [app_step, second],
            settings={"logcat": True, "logcat_scope": "app", "logcat_package": "com.explicit"},
        )
    )
    assert engine._logcat_packages(explicit) == ["com.explicit"]

    inferred = RunSnapshot.from_data(
        make_script([app_step, second], settings={"logcat": True, "logcat_scope": "app"})
    )
    assert engine._logcat_packages(inferred) == ["com.example.one", "com.example.two"]

    device = RunSnapshot.from_data(
        make_script([app_step], settings={"logcat": True, "logcat_scope": "device"})
    )
    assert engine._logcat_packages(device) == []


def test_runner_hooks_network_capture_lifecycle(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    calls: list[tuple] = []
    # 引擎必须把脚本 settings 传给抓包决策（network_capture 默认 false）。
    monkeypatch.setattr(
        network,
        "attach_if_needed",
        lambda run_id, settings=None: calls.append(
            ("attach", run_id, getattr(settings, "network_capture", "missing"))
        ),
    )
    monkeypatch.setattr(
        network,
        "stop_run_capture",
        lambda run_id, serial=None: calls.append(("stop", run_id, serial)),
    )
    run_id, run = run_script(make_script([click_step("s1")]))
    assert run["status"] == "succeeded"
    assert calls[0] == ("attach", run_id, False)
    assert calls[-1] == ("stop", run_id, "fake-test")


# ---------------------------------------------------------------- failure evidence
def test_failed_step_saves_logcat_and_network_evidence(runner_env, monkeypatch):
    stub = install_selectors(monkeypatch)
    stub.check_result = False
    monkeypatch.setattr(logcat, "recent", lambda serial, lines, active_only=False: ["failure-line"])
    monkeypatch.setattr(
        network,
        "recent_records",
        lambda serial, limit=200, since_iso=None: [
            {"ts": "2026-01-01T00:00:00.000+00:00", "host": "api.example.com", "port": 443, "scheme": "https"}
        ],
    )
    script = make_script(
        [
            make_step(
                "a1",
                "assert",
                {"kind": "exists", "target": {"by": "text", "value": "登录"}},
            )
        ]
    )
    run_id, run = run_script(script)
    assert run["status"] == "failed"
    step = get_steps(run_id)[0]

    with session_scope() as db:
        artifacts = list(
            db.scalars(
                select(Artifact).where(
                    Artifact.run_id == run_id,
                    Artifact.kind.in_(["logcat", "network"]),
                )
            )
        )
    assert {artifact.kind for artifact in artifacts} == {"logcat", "network"}
    assert all(artifact.step_result_id == step["id"] for artifact in artifacts)
    logcat_artifact = next(artifact for artifact in artifacts if artifact.kind == "logcat")
    network_artifact = next(artifact for artifact in artifacts if artifact.kind == "network")
    assert "failure-line" in Path(logcat_artifact.path).read_text(encoding="utf-8")
    assert "api.example.com" in Path(network_artifact.path).read_text(encoding="utf-8")


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


def test_when_goto_loop_consumes_step_budget(runner_env, monkeypatch):
    """when require 失败 + on_fail=goto 指回自身：不能绕过步骤预算空转到总超时。"""
    install_selectors(monkeypatch)
    runner_env.configure({"engine.max_steps": 5})
    script = make_script(
        [
            click_step(
                "s1",
                when=_when({"type": "target", "target": {"by": "text", "value": "不存在"}}),
                on_fail={"action": "goto", "target_id": "s1"},
            )
        ]
    )

    run_id, run = run_script(script)

    assert run["status"] == "failed"
    assert run["error"]["code"] == "MAX_STEPS_EXCEEDED"
    steps = get_steps(run_id)
    assert 1 <= len(steps) <= 6
    assert all(step["status"] == "failed" for step in steps)


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


def test_step_timeout_cancels_worker_before_late_injection(runner_env, monkeypatch):
    """节点超时后，执行线程不能再把迟到的点击注入设备。"""

    def slow_resolve(driver, target, timeout_ms=0, poll_interval_ms=350, should_abort=None):
        time.sleep(0.25)
        return SimpleNamespace(x=540, y=710, node=None, bounds=[100, 600, 980, 800])

    monkeypatch.setattr(executors, "resolve_target", slow_resolve)
    script = make_script([click_step("s1", timeout_ms=100)])

    run_id, run = run_script(script)

    assert run["status"] == "failed"
    steps = get_steps(run_id)
    assert steps[0]["status"] == "failed"
    assert steps[0]["error"]["code"] == "STEP_TIMEOUT"
    time.sleep(0.35)  # 等被放弃的 worker 走到注入前检查
    assert runner_env.driver.calls_of("click") == []


def test_timeout_with_stuck_worker_suppresses_retry(runner_env, monkeypatch):
    """超时后 worker 仍未退出：不立即重试，避免同一节点两个并发执行。"""

    def stuck_resolve(driver, target, timeout_ms=0, poll_interval_ms=350, should_abort=None):
        time.sleep(0.6)
        return SimpleNamespace(x=540, y=710, node=None, bounds=[100, 600, 980, 800])

    monkeypatch.setattr(executors, "resolve_target", stuck_resolve)
    script = make_script(
        [
            click_step(
                "s1",
                timeout_ms=100,
                retry={"count": 2, "interval_ms": 10},
            )
        ]
    )

    run_id, run = run_script(script)

    assert run["status"] == "failed"
    started = [event for event in get_events(run_id) if event["type"] == "step_started"]
    assert len(started) == 1
    steps = get_steps(run_id)
    assert steps[0]["attempts"] == 1
    time.sleep(0.6)  # 等旧 worker 醒来：它必须放弃注入
    assert runner_env.driver.calls_of("click") == []


def test_step_timeout_covers_long_gesture_duration(runner_env) -> None:
    """10s 录制滑动不能沿用默认 8s 节点超时，否则回放必失败。"""
    script = make_script(
        [
            make_step(
                "s1",
                "swipe",
                {
                    "from": {"by": "point", "x": 0, "y": 0},
                    "to": {"by": "point", "x": 100, "y": 0},
                    "duration_ms": 10_000,
                },
            )
        ]
    )
    ctx = make_ctx(runner_env, script)
    step = RunSnapshot.from_data(script).parsed.steps[0]
    assert engine._step_timeout(ctx, step) >= 12_000


def test_step_timeout_covers_wait_sleep_duration(runner_env) -> None:
    """wait sleep 的时长也是动作耗时：继承的默认节点超时不能把它静默截断。"""
    script = make_script([wait_step("s1", ms=10_000)])
    ctx = make_ctx(runner_env, script)
    step = RunSnapshot.from_data(script).parsed.steps[0]
    assert engine._step_timeout(ctx, step) >= 12_000


def test_step_timeout_covers_wait_until_timeout(runner_env) -> None:
    """wait until 的等待超时同样要能覆盖继承来的 8s 节点超时。"""
    script = make_script(
        [
            make_step(
                "s1",
                "wait",
                {"mode": "until", "target": {"by": "text", "value": "登录"}, "timeout_ms": 300_000},
            )
        ]
    )
    ctx = make_ctx(runner_env, script)
    step = RunSnapshot.from_data(script).parsed.steps[0]
    assert engine._step_timeout(ctx, step) >= 302_000


def test_step_delay_follows_script_default_when_node_unset(runner_env) -> None:
    """节点停顿填 0 = 跟随脚本 settings 的全节点默认停顿。"""
    script = make_script(
        [wait_step("s1", ms=10)],
        settings={"default_delay_before_ms": 250, "default_delay_after_ms": 120},
    )
    ctx = make_ctx(runner_env, script)
    step = RunSnapshot.from_data(script).parsed.steps[0]
    assert engine._step_delay_ms(ctx, step, "delay_before_ms") == 250
    assert engine._step_delay_ms(ctx, step, "delay_after_ms") == 120


def test_step_delay_node_value_overrides_script_default(runner_env) -> None:
    """节点填非 0 值 = 该节点自定义，优先于脚本默认（不是叠加）。"""
    script = make_script(
        [click_step("s1", delay_before_ms=0, delay_after_ms=800)],
        settings={"default_delay_before_ms": 250, "default_delay_after_ms": 120},
    )
    ctx = make_ctx(runner_env, script)
    step = RunSnapshot.from_data(script).parsed.steps[0]
    assert engine._step_delay_ms(ctx, step, "delay_before_ms") == 250
    assert engine._step_delay_ms(ctx, step, "delay_after_ms") == 800


def test_step_delay_defaults_to_zero_for_legacy_scripts(runner_env) -> None:
    """旧脚本没有默认停顿字段：解析后按 0 处理，行为与旧版一致。"""
    script = make_script([click_step("s1")])
    ctx = make_ctx(runner_env, script)
    step = RunSnapshot.from_data(script).parsed.steps[0]
    assert engine._step_delay_ms(ctx, step, "delay_before_ms") == 0
    assert engine._step_delay_ms(ctx, step, "delay_after_ms") == 0


def test_script_default_delay_applied_to_every_node(runner_env, monkeypatch) -> None:
    """集成回归：默认前后停顿要真正作用到每个节点（不是只写在 JSON 里）。"""
    install_selectors(monkeypatch)
    script = make_script(
        [click_step("s1"), click_step("s2")],
        settings={"default_delay_before_ms": 150, "default_delay_after_ms": 50},
    )

    started = time.monotonic()
    run_id, run = run_script(script)
    elapsed_ms = (time.monotonic() - started) * 1000

    assert run["status"] == "succeeded", run.get("error")
    assert {step["status"] for step in get_steps(run_id)} == {"passed"}
    assert elapsed_ms >= 400  # 2 个节点 × (150 + 50) ms
    assert len(runner_env.driver.calls_of("click")) == 2


def test_step_timeout_keeps_explicit_node_timeout(runner_env) -> None:
    """显式设置的节点超时仍是硬上限（调试/兜底场景不能被等待时长覆盖）。"""
    script = make_script([wait_step("s1", ms=10_000, timeout_ms=120)])
    ctx = make_ctx(runner_env, script)
    step = RunSnapshot.from_data(script).parsed.steps[0]
    timeout_ms = engine._step_timeout(ctx, step)
    assert timeout_ms == 120
    # 这是用户显式设的硬上限，不是引擎上限截断，错误信息里不该出现“调大 step_max_timeout_ms”的误导
    assert engine._timeout_clamp_note(ctx, step, timeout_ms) == ""


def test_sub_script_inherited_timeout_uses_engine_ceiling(runner_env) -> None:
    """子脚本节点没显式设超时：不能被脚本默认 8s 把整段子脚本切掉。"""
    script = make_script([make_step("p1", "sub_script", {"script_id": "child-id"})])
    ctx = make_ctx(runner_env, script)
    step = RunSnapshot.from_data(script).parsed.steps[0]
    assert engine._step_timeout(ctx, step) >= 600_000


def test_timeout_clamp_note_explains_truncation(runner_env) -> None:
    """真正撞到引擎上限时，错误信息要说明「等待 X ms 被上限 Y ms 截断」。"""
    runner_env.configure({"engine.step_max_timeout_ms": 300_000})
    script = make_script(
        [
            make_step(
                "s1",
                "wait",
                {"mode": "until", "target": {"by": "text", "value": "登录"}, "timeout_ms": 600_000},
            )
        ]
    )
    ctx = make_ctx(runner_env, script)
    step = RunSnapshot.from_data(script).parsed.steps[0]
    timeout_ms = engine._step_timeout(ctx, step)
    assert timeout_ms == 300_000
    note = engine._timeout_clamp_note(ctx, step, timeout_ms)
    assert "等待 600000 ms" in note
    assert "300000 ms" in note


def test_wait_sleep_succeeds_beyond_default_node_timeout(runner_env, monkeypatch) -> None:
    """集成回归：默认节点超时 300ms 时，sleep 1200ms 的 wait 不能被切失败。"""
    install_selectors(monkeypatch)
    runner_env.configure({"engine.default_timeout_ms": 300})
    script = make_script([wait_step("s1", ms=1200)])

    run_id, run = run_script(script)

    assert run["status"] == "succeeded", run.get("error")
    steps = get_steps(run_id)
    assert steps[0]["status"] == "passed"


def test_sub_script_not_cut_by_parent_default_timeout(runner_env, monkeypatch) -> None:
    """集成回归：父脚本默认节点超时 300ms 时，子脚本里的 1200ms wait 仍能跑完。"""
    install_selectors(monkeypatch)
    runner_env.configure({"engine.default_timeout_ms": 300})
    child_id = insert_script(make_script([wait_step("c1", ms=1200)], name="慢子脚本"))
    parent = make_script(
        [make_step("p1", "sub_script", {"script_id": child_id})],
        name="父脚本",
    )

    run_id, run = run_script(parent, timeout=10)

    assert run["status"] == "succeeded", run.get("error")
    assert {step["status"] for step in get_steps(run_id)} == {"passed"}


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
