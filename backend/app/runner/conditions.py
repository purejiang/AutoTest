"""when 条件树求值（v0.2 契约 §3）。

结构：``when = {mode, timeout_ms, poll_interval_ms, condition}``，condition 是带 ``type``
判别字段的递归联合（target / app / time / group / not）。

关键语义：
- 每轮轮询只抓 **一次** 层级快照；同一轮里所有 target 叶子共享该快照（避免 N 次 dump）；
- 所有等待都可被 pause/stop 打断（走 ``RunContext.wait_if_paused`` / ``sleep_interruptible``）；
- ``timeout_ms=0`` 只求值一次，不 sleep、不轮询；
- time 条件使用**宿主本地时区**；``start > end`` 表示跨午夜，且 days 指窗口开始那天
  （例如周六 01:00 仍在「周五 22:00 - 周六 06:00、days=[5]」窗口内）；
- 时间可注入（``now_fn``），测试可以冻结时钟。

本模块只负责"条件是否满足"，事件/落库由 engine 负责。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..android.selectors import TargetNotFound, select_node
from .context import RunStopped

_UNSET = object()


# ---------------------------------------------------------------- 时间
def local_now() -> datetime:
    """宿主本地时区的当前时间（带 tzinfo）。"""
    return datetime.now().astimezone()


def _as_local(now: datetime) -> datetime:
    """带时区 -> 转宿主本地时区；naive 视为本地时间。"""
    if now.tzinfo is not None:
        return now.astimezone()
    return now


def _minutes(hhmm: str) -> int:
    hour, minute = str(hhmm).split(":", 1)
    return int(hour) * 60 + int(minute)


# ---------------------------------------------------------------- 快照
@dataclass
class ConditionSnapshot:
    """一轮条件求值共享的观测数据：层级树 / 前台应用各最多抓一次。"""

    driver: Any
    _tree: Any = field(default=_UNSET, repr=False)
    _foreground: Any = field(default=_UNSET, repr=False)

    def tree(self) -> Any:
        if self._tree is _UNSET:
            try:
                self._tree = self.driver.dump_hierarchy()
            except Exception:  # 层级抓取失败：本轮按“未知”处理，下一轮再试
                self._tree = None
        return self._tree

    def foreground(self) -> dict[str, Any] | None:
        if self._foreground is _UNSET:
            try:
                value = self.driver.current_app()
                self._foreground = value if isinstance(value, dict) else {}
            except Exception:  # 前台应用获取失败：本轮按"未知"处理
                self._foreground = None
        return self._foreground


# ---------------------------------------------------------------- 条件求值
def _field(condition: Any, name: str, default: Any = None) -> Any:
    if isinstance(condition, dict):
        return condition.get(name, default)
    return getattr(condition, name, default)


def _activity_matches(observed: str, expected: str) -> bool:
    """activity 匹配：支持全名与 ``.MainActivity`` 简写。"""
    if not expected:
        return True
    if observed == expected:
        return True
    if expected.startswith("."):
        return observed.endswith(expected)
    return observed.endswith("." + expected)


def _target_visible(
    condition: Any,
    snapshot: ConditionSnapshot,
    ctx: Any = None,
) -> bool | None:
    """True/False = 已观测；None = 本轮层级抓取失败（未知，visible/gone 都不成立）。"""
    target = _field(condition, "target")
    if ctx is not None:
        # 与旧 when 一致：条件目标也支持 {{var}} 插值
        target = ctx.interpolate_target(target)
    tree = snapshot.tree()
    if tree is None:
        return None
    try:
        return select_node(tree, target) is not None
    except TargetNotFound:
        return False


def _app_state(
    condition: Any,
    snapshot: ConditionSnapshot,
    ctx: Any = None,
) -> bool:
    foreground = snapshot.foreground()
    if foreground is None:
        return False
    package = str(foreground.get("package") or "")
    activity = str(foreground.get("activity") or "")
    expected_package = str(_field(condition, "package", "") or "")
    expected_activity = _field(condition, "activity")
    if ctx is not None:
        expected_package = str(ctx.interpolate(expected_package))
        expected_activity = ctx.interpolate(expected_activity)
    is_foreground = package == expected_package and _activity_matches(
        activity, str(expected_activity or "")
    )
    state = str(_field(condition, "state", "foreground") or "foreground")
    return is_foreground if state == "foreground" else not is_foreground


def _time_window(condition: Any, now: datetime) -> bool:
    local = _as_local(now)
    start = _minutes(str(_field(condition, "start")))
    end = _minutes(str(_field(condition, "end")))
    days = {int(day) for day in (_field(condition, "days") or [])}
    minute = local.hour * 60 + local.minute
    weekday = local.isoweekday()  # 1=周一 ... 7=周日

    if start == end:  # 起止相同 = 全天
        return weekday in days
    if start < end:
        return start <= minute < end and weekday in days
    # 跨午夜：00:00 之后属于"前一天开始"的窗口
    if minute >= start:
        return weekday in days
    if minute < end:
        effective_day = weekday - 1 or 7
        return effective_day in days
    return False


def _snapshot_for(ctx: Any, snapshot: ConditionSnapshot | None) -> ConditionSnapshot:
    if snapshot is not None:
        return snapshot
    if ctx is None:
        raise ValueError("target/app condition requires a run context")
    return ConditionSnapshot(ctx.driver)


def evaluate_condition(
    condition: Any,
    ctx: Any = None,
    *,
    snapshot: ConditionSnapshot | None = None,
    now: datetime | None = None,
) -> bool:
    """单次求值条件树。

    ``snapshot`` 由调用方在同一轮内复用（target/app 叶子共享一次 dump/current_app）；
    ``now`` 可注入用于冻结时间。target/app 需要 ``ctx``（或显式 snapshot）；纯 time
    条件可只传 ``now``。unknown type 抛 ``ValueError``（schema 校验应已拦截）。
    """
    if condition is None:
        return True
    kind = str(_field(condition, "type", "") or "")

    if kind == "target":
        visible = _target_visible(condition, _snapshot_for(ctx, snapshot), ctx)
        if visible is None:
            # 层级抓取失败 = 观测未知：visible 与 gone 都不能算满足
            return False
        state = str(_field(condition, "state", "visible") or "visible")
        return visible if state == "visible" else not visible
    if kind == "app":
        return _app_state(condition, _snapshot_for(ctx, snapshot), ctx)
    if kind == "time":
        return _time_window(condition, now or local_now())
    if kind == "group":
        children = list(_field(condition, "items") or [])
        op = str(_field(condition, "op", "and") or "and")
        if op == "and":
            return all(
                evaluate_condition(child, ctx, snapshot=snapshot, now=now) for child in children
            )
        if op == "or":
            return any(
                evaluate_condition(child, ctx, snapshot=snapshot, now=now) for child in children
            )
        raise ValueError(f"unsupported group op: {op}")
    if kind == "not":
        child = _field(condition, "item")
        return not evaluate_condition(child, ctx, snapshot=snapshot, now=now)
    raise ValueError(f"unsupported condition type: {kind}")


# ---------------------------------------------------------------- 摘要
def condition_summary(condition: Any) -> str:
    """面向事件/报告的稳定短摘要（不参与求值）。"""
    if condition is None:
        return "none"
    kind = str(_field(condition, "type", "") or "")
    if kind == "target":
        target = _field(condition, "target")
        by = str(_field(target, "by", "") or "")
        index = _field(target, "index", 0)
        if by == "point":
            value = f"point({_field(target, 'x')},{_field(target, 'y')})"
        else:
            value = f"{by}={_field(target, 'value')!r}, index={index}"
        return f"target({value}) {_field(condition, 'state', 'visible')}"
    if kind == "app":
        activity = _field(condition, "activity")
        suffix = f", activity={activity}" if activity else ""
        return f"app({_field(condition, 'package')}{suffix}) {_field(condition, 'state', 'foreground')}"
    if kind == "time":
        days = ",".join(str(day) for day in (_field(condition, "days") or []))
        return f"time({_field(condition, 'start')}-{_field(condition, 'end')}, days={days})"
    if kind == "group":
        children = list(_field(condition, "items") or [])
        op = str(_field(condition, "op", "and") or "and")
        return "(" + f" {op} ".join(condition_summary(child) for child in children) + ")"
    if kind == "not":
        return f"not({condition_summary(_field(condition, 'item'))})"
    return str(kind or "unknown")


# ---------------------------------------------------------------- 等待
@dataclass(frozen=True)
class ConditionWaitResult:
    met: bool
    waited_ms: int
    condition_summary: str

    def __bool__(self) -> bool:  # 便于 `if wait_condition_tree(...)`
        return self.met


def wait_condition_tree(
    ctx: Any,
    when: Any,
    *,
    now_fn: Callable[[], datetime] | None = None,
) -> ConditionWaitResult:
    """按 when 轮询条件树，返回 {met, waited_ms, condition_summary}。

    - 每轮一个 ``ConditionSnapshot``（target 叶子共享同一次层级抓取）；
    - ``timeout_ms=0`` 只求值一次；
    - pause/stop 可打断：暂停期间阻塞，stop 抛 ``RunStopped``；
    - waited_ms 使用运行活跃时长口径（paused 不计入）。
    """
    timeout_ms = max(0, int(_field(when, "timeout_ms", 0) or 0))
    poll_interval_ms = max(50, int(_field(when, "poll_interval_ms", 350) or 350))
    condition = _field(when, "condition")
    summary = condition_summary(condition)
    started_active = ctx.active_elapsed_ms()
    met = False

    while True:
        ctx.wait_if_paused()
        if ctx.should_stop():
            raise RunStopped("stop requested while evaluating when condition")

        snapshot = ConditionSnapshot(ctx.driver)
        now = now_fn() if now_fn is not None else local_now()
        met = evaluate_condition(condition, ctx, snapshot=snapshot, now=now)
        waited_ms = max(0, int(ctx.active_elapsed_ms()) - started_active)
        if met or waited_ms >= timeout_ms:
            break
        ctx.sleep_interruptible(min(poll_interval_ms, timeout_ms - waited_ms))

    waited_ms = max(0, int(ctx.active_elapsed_ms()) - started_active)
    return ConditionWaitResult(met=met, waited_ms=waited_ms, condition_summary=summary)


__all__ = [
    "ConditionSnapshot",
    "ConditionWaitResult",
    "condition_summary",
    "evaluate_condition",
    "local_now",
    "wait_condition_tree",
]
