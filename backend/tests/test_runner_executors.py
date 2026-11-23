"""12 类节点执行器单测（FakeDriver + 确定性选择器桩）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.android.driver import CommandResult
from app.runner import executors
from app.runner.context import RunSnapshot
from tests.test_runner_kits import (
    click_step,
    get_events,
    get_steps,
    insert_run,
    insert_script,
    install_selectors,
    make_ctx,
    make_script,
    make_step,
    parsed_step,
)
from tests.test_runner_kits import (
    get_artifacts as get_artifacts_rows,
)
from tests.test_runner_kits import runner_env as runner_env


# ---------------------------------------------------------------- 动作类
def test_click_resolves_target_and_taps(runner_env, monkeypatch):
    stub = install_selectors(monkeypatch)
    script = make_script([click_step("s1")])
    ctx = make_ctx(runner_env, script)

    output = executors.execute_click(ctx, parsed_step(script))

    assert output == {"x": 540, "y": 710, "bounds": [100, 640, 980, 780]}
    assert runner_env.driver.ops() == ["click"]
    assert runner_env.driver.calls[-1].kwargs == {"x": 540, "y": 710}
    assert len(stub.resolve_calls) == 1


def test_click_target_not_found_propagates(runner_env, monkeypatch):
    stub = install_selectors(monkeypatch)
    stub.resolve_errors = [executors.TargetNotFound("target not found: 登录")]
    script = make_script([click_step("s1")])
    ctx = make_ctx(runner_env, script)

    with pytest.raises(executors.TargetNotFound):
        executors.execute_click(ctx, parsed_step(script))


def test_long_click_swipe_drag_use_driver_actions(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    script = make_script(
        [
            make_step("s1", "long_click", {"target": {"by": "text", "value": "登录"}, "duration_ms": 900}),
            make_step(
                "s2",
                "swipe",
                {
                    "from": {"by": "point", "x": 100, "y": 200},
                    "to": {"by": "point", "x": 300, "y": 400},
                    "duration_ms": 450,
                },
            ),
            make_step(
                "s3",
                "drag",
                {
                    "from": {"by": "point", "x": 10, "y": 20},
                    "to": {"by": "point", "x": 30, "y": 40},
                    "duration_ms": 1200,
                },
            ),
        ]
    )
    ctx = make_ctx(runner_env, script)
    steps = RunSnapshot.from_data(script).parsed.steps

    assert executors.execute_long_click(ctx, steps[0])["duration_ms"] == 900
    assert runner_env.driver.calls_of("long_press")[-1].kwargs == {
        "x": 540,
        "y": 710,
        "duration_ms": 900,
    }

    swipe_output = executors.execute_swipe(ctx, steps[1])
    assert swipe_output["duration_ms"] == 450
    assert swipe_output["from"]["x"] == 540  # 桩把两端都解析到同一点
    swipe_call = runner_env.driver.calls_of("swipe")[-1].kwargs
    assert swipe_call["duration_ms"] == 450

    drag_output = executors.execute_drag(ctx, steps[2])
    assert drag_output["duration_ms"] == 1200


def test_swipe_with_recorded_path_replays_exact_trajectory(runner_env, monkeypatch):
    """录制轨迹：swipe 节点带 path 时按真实轨迹回放（不是两点直线）。"""
    install_selectors(monkeypatch)
    script = make_script(
        [
            make_step(
                "s1",
                "swipe",
                {
                    "from": {"by": "point", "x": 100, "y": 200},
                    "to": {"by": "point", "x": 300, "y": 400},
                    "duration_ms": 450,
                    "path": [
                        {"x": 100, "y": 200, "dt_ms": 0},
                        {"x": 160, "y": 260, "dt_ms": 40},
                        {"x": 220, "y": 340, "dt_ms": 90},
                        {"x": 300, "y": 400, "dt_ms": 140},
                    ],
                },
            )
        ]
    )
    ctx = make_ctx(runner_env, script)
    steps = RunSnapshot.from_data(script).parsed.steps

    output = executors.execute_swipe(ctx, steps[0])

    assert output["path_points"] == 4
    call = runner_env.driver.calls_of("swipe_path")[-1]
    assert call.kwargs["duration_ms"] == 450
    assert call.kwargs["points"] == [(100, 200), (160, 260), (220, 340), (300, 400)]
    assert not runner_env.driver.calls_of("swipe"), "带轨迹时不应再走直线 swipe"


def test_input_interpolates_clear_and_types(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    script = make_script(
        [
            make_step(
                "s1",
                "input",
                {
                    "text": "user={{name}}",
                    "target": {"by": "text", "value": "账号"},
                    "clear_first": True,
                },
            )
        ],
        variables={"name": "bob"},
    )
    ctx = make_ctx(runner_env, script)

    output = executors.execute_input(ctx, parsed_step(script))

    assert output["text"] == "user=bob"
    assert output["length"] == len("user=bob")
    assert output["cleared"] is True
    assert runner_env.driver.ops() == ["click", "clear_text", "input_text"]
    assert runner_env.driver.calls_of("input_text")[-1].kwargs == {"text": "user=bob"}


def test_key_uses_name_or_keycode(runner_env):
    script = make_script([make_step("s1", "key", {"key": "back"})])
    ctx = make_ctx(runner_env, script)

    output = executors.execute_key(ctx, parsed_step(script))

    assert output == {"key": "back"}
    assert runner_env.driver.calls_of("key")[-1].kwargs == {"keycode": 4}


# ---------------------------------------------------------------- wait
def test_wait_sleep_uses_interruptible_sleep(runner_env):
    script = make_script([make_step("s1", "wait", {"mode": "sleep", "ms": 40})])
    ctx = make_ctx(runner_env, script)

    started = time.monotonic()
    output = executors.execute_wait(ctx, parsed_step(script))
    elapsed = time.monotonic() - started

    assert output == {"mode": "sleep", "waited_ms": 40}
    assert elapsed >= 0.03


def test_wait_until_success(runner_env, monkeypatch):
    stub = install_selectors(monkeypatch)
    stub.wait_result = True
    script = make_script(
        [
            make_step(
                "s1",
                "wait",
                {
                    "mode": "until",
                    "target": {"by": "text", "value": "登录"},
                    "state": "visible",
                    "timeout_ms": 500,
                },
            )
        ]
    )
    ctx = make_ctx(runner_env, script)

    output = executors.execute_wait(ctx, parsed_step(script))

    assert output["mode"] == "until"
    assert output["state"] == "visible"
    assert stub.wait_calls[0][2] == 500


def test_wait_until_timeout_raises_target_not_found(runner_env, monkeypatch):
    stub = install_selectors(monkeypatch)
    stub.wait_result = False
    script = make_script(
        [
            make_step(
                "s1",
                "wait",
                {
                    "mode": "until",
                    "target": {"by": "text", "value": "登录"},
                    "timeout_ms": 100,
                },
            )
        ]
    )
    ctx = make_ctx(runner_env, script)

    with pytest.raises(executors.TargetNotFound):
        executors.execute_wait(ctx, parsed_step(script))


# ---------------------------------------------------------------- app / shell
def test_app_launch_falls_back_to_foreground_package(runner_env):
    script = make_script([make_step("s1", "app", {"action": "launch"})])
    ctx = make_ctx(runner_env, script)

    output = executors.execute_app(ctx, parsed_step(script))

    assert output["package"] == "com.example.fake"
    assert runner_env.driver.calls_of("app_start")[-1].kwargs == {
        "package": "com.example.fake",
        "activity": None,
    }


def test_app_restart_stops_then_starts(runner_env):
    script = make_script(
        [make_step("s1", "app", {"action": "restart", "package": "com.demo.app"})]
    )
    ctx = make_ctx(runner_env, script)

    output = executors.execute_app(ctx, parsed_step(script))

    assert output["package"] == "com.demo.app"
    ops = [op for op in runner_env.driver.ops() if op in ("app_stop", "app_start")]
    assert ops == ["app_stop", "app_start"]


def test_shell_success_and_check_exit_code(runner_env):
    runner_env.driver.shell_responses["boom"] = CommandResult(code=1, stdout="", stderr="bad")
    script = make_script(
        [
            make_step("s1", "shell", {"command": "echo hi"}),
            make_step("s2", "shell", {"command": "boom", "check_exit_code": True}),
        ]
    )
    ctx = make_ctx(runner_env, script)
    steps = RunSnapshot.from_data(script).parsed.steps

    output = executors.execute_shell(ctx, steps[0])
    assert output["code"] == 0
    assert output["stdout"].strip() == "hi"

    with pytest.raises(executors.ShellCommandFailed) as excinfo:
        executors.execute_shell(ctx, steps[1])
    assert excinfo.value.output["code"] == 1


def test_shell_exit_code_ignored_when_check_disabled(runner_env):
    runner_env.driver.shell_responses["boom"] = CommandResult(code=1, stdout="", stderr="bad")
    script = make_script(
        [make_step("s1", "shell", {"command": "boom", "check_exit_code": False})]
    )
    ctx = make_ctx(runner_env, script)

    output = executors.execute_shell(ctx, parsed_step(script))

    assert output["code"] == 1


# ---------------------------------------------------------------- assert
def _assert_step(**params) -> dict:
    return make_step("a1", "assert", params)


def test_assert_exists_passes_and_emits_event(runner_env, monkeypatch):
    stub = install_selectors(monkeypatch)
    stub.check_result = True
    script = make_script(
        [_assert_step(kind="exists", target={"by": "text", "value": "登录"})]
    )
    ctx = make_ctx(runner_env, script)

    output = executors.execute_assert(ctx, parsed_step(script))

    assert output["passed"] is True
    assert output["kind"] == "exists"
    assert len(stub.check_calls) == 1
    assert stub.check_calls[0][1] == "visible"
    events = [event for event in get_events(ctx.run_id) if event["type"] == "assert_result"]
    assert events[-1]["payload"]["passed"] is True


def test_assert_exists_failure_raises_assertion_failed(runner_env, monkeypatch):
    stub = install_selectors(monkeypatch)
    stub.check_result = False
    script = make_script(
        [_assert_step(kind="exists", target={"by": "text", "value": "登录"})]
    )
    ctx = make_ctx(runner_env, script)

    with pytest.raises(executors.AssertionFailed):
        executors.execute_assert(ctx, parsed_step(script))
    events = [event for event in get_events(ctx.run_id) if event["type"] == "assert_result"]
    assert events[-1]["payload"]["passed"] is False


def test_assert_text_contains_uses_resolved_node(runner_env, monkeypatch):
    stub = install_selectors(monkeypatch)
    stub.node.text = "登录成功"
    script = make_script(
        [
            _assert_step(
                kind="text_contains",
                target={"by": "text", "value": "登录"},
                value="成功",
            )
        ]
    )
    ctx = make_ctx(runner_env, script)

    output = executors.execute_assert(ctx, parsed_step(script))

    assert output["passed"] is True
    assert output["actual"] == "登录成功"


def test_assert_foreground_matches_package(runner_env):
    script = make_script(
        [_assert_step(kind="foreground", value="com.example.fake")]
    )
    ctx = make_ctx(runner_env, script)

    output = executors.execute_assert(ctx, parsed_step(script))

    assert output["passed"] is True
    assert output["foreground"]["package"] == "com.example.fake"


# ---------------------------------------------------------------- screenshot
def test_screenshot_saves_artifact(runner_env):
    script = make_script([make_step("s1", "screenshot", {"name": "首页"})])
    run_id = insert_run(script)
    ctx = make_ctx(runner_env, script, run_id=run_id)

    output = executors.execute_screenshot(ctx, parsed_step(script))

    assert output["artifact_id"]
    artifacts = get_artifacts_rows(run_id)
    assert len(artifacts) == 1
    assert Path(artifacts[0].path).is_file()
    assert [event for event in get_events(run_id) if event["type"] == "screenshot"]


# ---------------------------------------------------------------- sub_script
def _sub_script_step(script_id: str, **params) -> dict:
    return make_step("sub", "sub_script", {"script_id": script_id, **params})


def test_sub_script_executes_nested_steps(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    child_script = make_script([click_step("c1", name="子点击")], name="子脚本")
    child_id = insert_script(child_script)
    parent_script = make_script([_sub_script_step(child_id)])
    run_id = insert_run(parent_script)
    ctx = make_ctx(runner_env, parent_script, run_id=run_id)

    output = executors.execute_sub_script(ctx, parsed_step(parent_script))

    assert output["script_id"] == child_id
    assert output["summary"]["passed_steps"] == 1
    steps = get_steps(run_id)
    assert [step["step_id"] for step in steps] == ["c1"]
    assert steps[0]["status"] == "passed"
    assert len(runner_env.driver.calls_of("click")) == 1


def test_sub_script_overrides_variables(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    child_script = make_script(
        [
            make_step(
                "c1",
                "input",
                {"text": "{{greeting}}", "clear_first": False},
            )
        ],
        name="子脚本",
        variables={"greeting": "default"},
    )
    child_id = insert_script(child_script)
    parent_script = make_script(
        [_sub_script_step(child_id, variables={"greeting": "override"})],
        variables={"outer": "x"},
    )
    ctx = make_ctx(runner_env, parent_script, run_id=insert_run(parent_script))

    executors.execute_sub_script(ctx, parsed_step(parent_script))

    assert runner_env.driver.calls_of("input_text")[-1].kwargs == {"text": "override"}


def test_sub_script_depth_limit(runner_env):
    child_script = make_script([click_step("c1")], name="深")
    child_id = insert_script(child_script)
    parent_script = make_script([_sub_script_step(child_id)])
    ctx = make_ctx(runner_env, parent_script, depth=5, run_id=insert_run(parent_script))

    with pytest.raises(executors.SubScriptDepthExceeded):
        executors.execute_sub_script(ctx, parsed_step(parent_script))


def test_sub_script_missing_is_non_retryable(runner_env):
    parent_script = make_script([_sub_script_step("does-not-exist")])
    ctx = make_ctx(runner_env, parent_script, run_id=insert_run(parent_script))

    with pytest.raises(executors.NonRetryableError):
        executors.execute_sub_script(ctx, parsed_step(parent_script))


def test_execute_step_dispatches(runner_env, monkeypatch):
    install_selectors(monkeypatch)
    script = make_script([click_step("s1")])
    ctx = make_ctx(runner_env, script)

    output = executors.execute_step(ctx, parsed_step(script))

    assert output["x"] == 540


def test_drag_with_recorded_path_prefers_native_drag(runner_env, monkeypatch):
    """drag 不应走 swipe_points 轨迹回放：真机上可能卡到节点超时。"""
    install_selectors(monkeypatch)
    script = make_script(
        [
            make_step(
                "s1",
                "drag",
                {
                    "from": {"by": "point", "x": 10, "y": 20},
                    "to": {"by": "point", "x": 30, "y": 40},
                    "duration_ms": 400,
                    "path": [
                        {"x": 10, "y": 20, "dt_ms": 0},
                        {"x": 20, "y": 30, "dt_ms": 100},
                        {"x": 30, "y": 40, "dt_ms": 200},
                    ],
                },
            )
        ]
    )
    ctx = make_ctx(runner_env, script)
    calls: list[dict] = []

    def native_drag(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 1000) -> None:
        calls.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration_ms": duration_ms})

    monkeypatch.setattr(ctx.driver, "drag", native_drag, raising=False)
    output = executors.execute_drag(ctx, parsed_step(script))

    assert calls == [{"x1": 540, "y1": 710, "x2": 540, "y2": 710, "duration_ms": 400}]
    assert output["duration_ms"] == 400
    assert runner_env.driver.calls_of("swipe_path") == []
