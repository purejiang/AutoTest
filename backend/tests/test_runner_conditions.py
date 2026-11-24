"""v0.2 when 条件树：schema、单轮共享快照、app/time/group/not、可打断等待。

全部使用 FakeDriver + 冻结时间，不依赖真机。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest

from app import schemas
from app.android.driver import HierarchyNode
from app.runner import conditions, controls
from app.runner.context import RunStopped
from tests.test_runner_kits import click_step, make_ctx, make_script
from tests.test_runner_kits import runner_env as runner_env

# ---------------------------------------------------------------- helpers
TARGET_VISIBLE = {"type": "target", "target": {"by": "text", "value": "登录"}}
TARGET_MISSING = {"type": "target", "target": {"by": "text", "value": "不存在"}}


def _when_dict(
    condition: dict,
    *,
    mode: str = "require",
    timeout_ms: int = 0,
    poll_interval_ms: int = 50,
) -> dict:
    return {
        "mode": mode,
        "timeout_ms": timeout_ms,
        "poll_interval_ms": poll_interval_ms,
        "condition": condition,
    }


def when(
    condition: dict,
    *,
    mode: str = "require",
    timeout_ms: int = 0,
    poll_interval_ms: int = 50,
) -> schemas.When:
    return schemas.When.model_validate(
        _when_dict(
            condition,
            mode=mode,
            timeout_ms=timeout_ms,
            poll_interval_ms=poll_interval_ms,
        )
    )


def script_with_when(condition: dict, **overrides) -> dict:
    # 故意直接构造 raw dict：非法样本要交给 validate_script 报 error，而不是在 helper 抛异常
    return make_script([click_step("s1", when=_when_dict(condition, **overrides))])


def validate_when(condition: dict, **overrides) -> dict:
    return schemas.validate_script(script_with_when(condition, **overrides))


def make_condition_ctx(runner_env, *, controls_obj=None, run_id: int = 1, variables=None):
    return make_ctx(
        runner_env,
        make_script([click_step("s1")]),
        run_id=run_id,
        variables=variables,
        controls_obj=controls_obj,
    )


# ---------------------------------------------------------------- schema
@pytest.mark.parametrize(
    "condition",
    [
        TARGET_VISIBLE,
        {"type": "target", "target": {"by": "text", "value": "登录"}, "state": "gone"},
        {"type": "app", "package": "com.example.fake", "state": "foreground"},
        {"type": "app", "package": "com.example.fake", "activity": ".MainActivity"},
        {"type": "time", "start": "09:00", "end": "18:00", "days": [1, 2, 3]},
        {
            "type": "group",
            "op": "and",
            "items": [
                TARGET_VISIBLE,
                {"type": "not", "item": {"type": "app", "package": "com.other"}},
            ],
        },
        {
            "type": "group",
            "op": "or",
            "items": [TARGET_MISSING, {"type": "time", "start": "22:00", "end": "06:00", "days": [5]}],
        },
    ],
)
def test_when_schema_accepts_v02_condition_tree(condition) -> None:
    result = validate_when(condition)
    assert result["valid"] is True, result["errors"]


def test_when_schema_rejects_legacy_target_state_shape() -> None:
    legacy = {"target": {"by": "text", "value": "登录"}, "mode": "require", "timeout_ms": 1000, "state": "visible"}
    script = make_script([click_step("s1", when=legacy)])
    result = schemas.validate_script(script)
    assert result["valid"] is False
    paths = " ".join(error["path"] for error in result["errors"])
    assert "when.condition" in paths
    assert "when.target" in paths  # 旧结构字段被 extra=forbid 拒绝


def test_when_schema_rejects_point_visibility_condition() -> None:
    result = validate_when({"type": "target", "target": {"by": "point", "x": 10, "y": 20}})
    assert result["valid"] is False
    assert any("point" in error["message"] for error in result["errors"])


@pytest.mark.parametrize(
    "condition",
    [
        {"type": "group", "op": "and", "items": []},
        {"type": "group", "op": "and", "items": [TARGET_VISIBLE] * 51},
        {"type": "time", "start": "9:00", "end": "18:00", "days": [1]},
        {"type": "time", "start": "09:00", "end": "24:00", "days": [1]},
        {"type": "time", "start": "09:00", "end": "18:00", "days": []},
        {"type": "time", "start": "09:00", "end": "18:00", "days": [0]},
        {"type": "time", "start": "09:00", "end": "18:00", "days": [1, 1]},
        {"type": "app", "package": ""},
        {"type": "group", "op": "xor", "items": [TARGET_VISIBLE]},
        {"type": "unknown"},
    ],
)
def test_when_schema_rejects_invalid_condition(condition) -> None:
    assert validate_when(condition)["valid"] is False


def test_group_accepts_fifty_items() -> None:
    condition = {"type": "group", "op": "and", "items": [TARGET_VISIBLE] * 50}
    assert validate_when(condition)["valid"] is True


def test_group_and_not_use_items_and_item_field_names() -> None:
    legacy_group = {"type": "group", "op": "and", "conditions": [TARGET_VISIBLE]}
    legacy_not = {"type": "not", "condition": TARGET_VISIBLE}
    assert validate_when(legacy_group)["valid"] is False
    assert validate_when(legacy_not)["valid"] is False


def test_time_days_only_accepts_one_to_seven() -> None:
    all_days = {"type": "time", "start": "09:00", "end": "18:00", "days": [1, 2, 3, 4, 5, 6, 7]}
    assert validate_when(all_days)["valid"] is True
    for bad_days in ([0], [8]):
        condition = {"type": "time", "start": "09:00", "end": "18:00", "days": bad_days}
        assert validate_when(condition)["valid"] is False


def test_when_schema_rejects_timeout_and_poll_bounds() -> None:
    assert validate_when(TARGET_VISIBLE, timeout_ms=-1)["valid"] is False
    assert validate_when(TARGET_VISIBLE, timeout_ms=600_001)["valid"] is False
    assert validate_when(TARGET_VISIBLE, poll_interval_ms=49)["valid"] is False
    assert validate_when(TARGET_VISIBLE, poll_interval_ms=5001)["valid"] is False
    assert validate_when(TARGET_VISIBLE, timeout_ms=0)["valid"] is True


def test_when_nesting_limit_is_four_group_levels() -> None:
    leaf = TARGET_VISIBLE
    level_four = leaf
    for _ in range(4):
        level_four = {"type": "group", "op": "and", "items": [level_four]}
    assert validate_when(level_four)["valid"] is True
    level_five = {"type": "group", "op": "and", "items": [level_four]}
    result = validate_when(level_five)
    assert result["valid"] is False
    assert any("nesting" in error["message"] for error in result["errors"])


# ---------------------------------------------------------------- 求值
def test_target_visible_and_gone(runner_env) -> None:
    ctx = make_condition_ctx(runner_env)
    assert conditions.evaluate_condition(when(TARGET_VISIBLE).condition, ctx) is True
    assert conditions.evaluate_condition(when(TARGET_MISSING).condition, ctx) is False
    assert conditions.evaluate_condition(when({**TARGET_MISSING, "state": "gone"}).condition, ctx) is True
    assert conditions.evaluate_condition(when({**TARGET_VISIBLE, "state": "gone"}).condition, ctx) is False


def test_target_dump_failure_is_unknown_not_gone(runner_env, monkeypatch) -> None:
    """层级抓取失败时 target(gone) 不能算满足；下一轮抓取成功后再按真实结果判断。"""

    def boom() -> None:
        raise RuntimeError("dump failed")

    monkeypatch.setattr(runner_env.driver, "dump_hierarchy", boom)
    ctx = make_condition_ctx(runner_env)
    assert conditions.evaluate_condition(when({**TARGET_MISSING, "state": "gone"}).condition, ctx) is False
    assert conditions.evaluate_condition(when(TARGET_VISIBLE).condition, ctx) is False


def test_group_target_leaves_share_one_snapshot_per_round(runner_env, monkeypatch) -> None:
    inner = runner_env.driver
    original = inner.dump_hierarchy
    calls = {"n": 0}

    def counting_dump():
        calls["n"] += 1
        return original()

    monkeypatch.setattr(inner, "dump_hierarchy", counting_dump)
    ctx = make_condition_ctx(runner_env)
    condition = when(
        {
            "type": "group",
            "op": "and",
            "items": [
                TARGET_VISIBLE,
                {"type": "target", "target": {"by": "text", "value": "忘记密码"}},
                {"type": "not", "item": TARGET_MISSING},
            ],
        }
    ).condition

    result = conditions.wait_condition_tree(ctx, when(condition.model_dump(mode="json")), now_fn=None)

    assert result.met is True
    assert calls["n"] == 1  # 一轮只 dump 一次，target 叶子共享


def test_wait_condition_tree_timeout_zero_checks_once(runner_env, monkeypatch) -> None:
    inner = runner_env.driver
    original = inner.dump_hierarchy
    calls = {"n": 0}

    def counting_dump():
        calls["n"] += 1
        return original()

    monkeypatch.setattr(inner, "dump_hierarchy", counting_dump)
    ctx = make_condition_ctx(runner_env)

    result = conditions.wait_condition_tree(
        ctx,
        when({"type": "group", "op": "or", "items": [TARGET_MISSING, TARGET_MISSING]}),
    )

    assert result.met is False
    assert calls["n"] == 1


def test_wait_condition_tree_polls_until_condition_appears(runner_env, monkeypatch) -> None:
    ctx = make_condition_ctx(runner_env)
    tree = HierarchyNode(
        class_name="android.widget.FrameLayout",
        bounds=[0, 0, 1080, 2400],
        children=[HierarchyNode(text="迟到元素", bounds=[0, 0, 100, 100])],
    )
    original = runner_env.driver.dump_hierarchy
    calls = {"n": 0}

    def dump_after_two_rounds():
        calls["n"] += 1
        return original() if calls["n"] < 3 else tree

    monkeypatch.setattr(runner_env.driver, "dump_hierarchy", dump_after_two_rounds)

    result = conditions.wait_condition_tree(
        ctx,
        when({"type": "target", "target": {"by": "text", "value": "迟到元素"}}, timeout_ms=1500),
    )

    assert result.met is True
    assert calls["n"] == 3
    assert result.waited_ms >= 50


def test_app_condition_foreground_background(runner_env) -> None:
    ctx = make_condition_ctx(runner_env)
    foreground = {"type": "app", "package": "com.example.fake", "state": "foreground"}
    background = {"type": "app", "package": "com.example.fake", "state": "background"}

    assert conditions.evaluate_condition(when(foreground).condition, ctx) is True
    assert conditions.evaluate_condition(when(background).condition, ctx) is False
    assert (
        conditions.evaluate_condition(
            when({**foreground, "activity": ".MainActivity"}).condition, ctx
        )
        is True
    )
    assert (
        conditions.evaluate_condition(
            when({**foreground, "activity": ".OtherActivity"}).condition, ctx
        )
        is False
    )

    runner_env.driver.foreground = {"package": "com.other", "activity": ".OtherActivity"}
    assert conditions.evaluate_condition(when(foreground).condition, ctx) is False
    assert conditions.evaluate_condition(when(background).condition, ctx) is True


@pytest.mark.parametrize(
    ("now", "start", "end", "days", "expected"),
    [
        (datetime(2026, 11, 23, 10, 0), "09:00", "18:00", [1], True),
        (datetime(2026, 11, 23, 8, 59), "09:00", "18:00", [1], False),
        (datetime(2026, 11, 23, 18, 0), "09:00", "18:00", [1], False),
        (datetime(2026, 11, 24, 10, 0), "09:00", "18:00", [1], False),
        (datetime(2026, 11, 28, 1, 0), "22:00", "06:00", [5], True),  # 周六凌晨属于周五窗口
        (datetime(2026, 11, 28, 1, 0), "22:00", "06:00", [6], False),
        (datetime(2026, 11, 23, 0, 30), "22:00", "06:00", [7], True),
        (datetime(2026, 11, 23, 23, 0), "22:00", "06:00", [1], True),
        (datetime(2026, 11, 23, 12, 0), "22:00", "06:00", [1], False),
        (datetime(2026, 11, 23, 12, 0), "00:00", "00:00", [1], True),  # 起止相同 = 全天
    ],
)
def test_time_condition_local_window_and_cross_midnight(
    runner_env, now, start, end, days, expected
) -> None:
    ctx = make_condition_ctx(runner_env)
    condition = when({"type": "time", "start": start, "end": end, "days": days}).condition
    assert conditions.evaluate_condition(condition, ctx, now=now) is expected


def test_time_condition_can_be_evaluated_without_run_context() -> None:
    condition = when({"type": "time", "start": "09:00", "end": "18:00", "days": [1]}).condition
    assert conditions.evaluate_condition(condition, now=datetime(2026, 11, 23, 10, 0)) is True


def test_time_condition_uses_injected_now_in_wait(runner_env) -> None:
    ctx = make_condition_ctx(runner_env)
    condition = {"type": "time", "start": "09:00", "end": "18:00", "days": [1]}
    frozen = datetime(2026, 11, 23, 10, 0)
    result = conditions.wait_condition_tree(
        ctx, when(condition, timeout_ms=0), now_fn=lambda: frozen
    )
    assert result.met is True


def test_group_and_or_not_semantics(runner_env) -> None:
    ctx = make_condition_ctx(runner_env)
    and_false = {
        "type": "group",
        "op": "and",
        "items": [TARGET_VISIBLE, TARGET_MISSING],
    }
    or_true = {
        "type": "group",
        "op": "or",
        "items": [TARGET_MISSING, TARGET_VISIBLE],
    }
    not_false = {"type": "not", "item": TARGET_VISIBLE}
    nested = {
        "type": "group",
        "op": "and",
        "items": [
            or_true,
            {"type": "not", "item": TARGET_MISSING},
            {"type": "not", "item": and_false},
        ],
    }

    assert conditions.evaluate_condition(when(and_false).condition, ctx) is False
    assert conditions.evaluate_condition(when(or_true).condition, ctx) is True
    assert conditions.evaluate_condition(when(not_false).condition, ctx) is False
    assert conditions.evaluate_condition(when(nested).condition, ctx) is True


def test_condition_tree_interpolates_target_and_app_variables(runner_env) -> None:
    ctx = make_condition_ctx(
        runner_env,
        variables={
            "account": "登录",
            "pkg": "com.example.fake",
            "act": ".MainActivity",
        },
    )
    target_group = {
        "type": "group",
        "op": "and",
        "items": [
            {"type": "target", "target": {"by": "text", "value": "{{account}}"}},
            {"type": "not", "item": TARGET_MISSING},
        ],
    }
    app_condition = {
        "type": "app",
        "package": "{{pkg}}",
        "activity": "{{act}}",
        "state": "foreground",
    }

    assert conditions.evaluate_condition(when(target_group).condition, ctx) is True
    assert conditions.evaluate_condition(when(app_condition).condition, ctx) is True


def test_condition_summary_is_stable_and_descriptive(runner_env) -> None:
    condition = when(
        {
            "type": "group",
            "op": "and",
            "items": [
                TARGET_VISIBLE,
                {"type": "app", "package": "com.example.fake", "activity": ".MainActivity"},
                {"type": "time", "start": "22:00", "end": "06:00", "days": [5, 6]},
                {"type": "not", "item": TARGET_MISSING},
            ],
        }
    ).condition
    summary = conditions.condition_summary(condition)
    assert summary.startswith("(")
    assert "target(text='登录'" in summary
    assert "app(com.example.fake, activity=.MainActivity) foreground" in summary
    assert "time(22:00-06:00, days=5,6)" in summary
    assert "not(target(text='不存在'" in summary


# ---------------------------------------------------------------- 打断
def test_wait_condition_tree_stop_raises(runner_env) -> None:
    control = controls.create_control(701)
    control.stop_event.set()
    try:
        ctx = make_condition_ctx(runner_env, controls_obj=control, run_id=701)
        with pytest.raises(RunStopped):
            conditions.wait_condition_tree(ctx, when(TARGET_MISSING, timeout_ms=5000))
    finally:
        controls.cleanup(701)


def test_wait_condition_tree_pause_can_be_stopped(runner_env) -> None:
    control = controls.create_control(702)
    control.pause_event.clear()  # 进入等待前已暂停
    ctx = make_condition_ctx(runner_env, controls_obj=control, run_id=702)
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            conditions.wait_condition_tree(ctx, when(TARGET_MISSING, timeout_ms=5000))
        except BaseException as exc:  # noqa: BLE001 - 测试收集打断异常
            errors.append(exc)

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    time.sleep(0.1)
    assert worker.is_alive()  # 暂停期间阻塞，没有空转超时
    control.stop_event.set()
    control.pause_event.set()
    worker.join(timeout=2)

    try:
        assert errors and isinstance(errors[0], RunStopped)
    finally:
        controls.cleanup(702)
