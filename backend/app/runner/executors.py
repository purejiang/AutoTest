"""12 类节点执行器（契约 §3，§14.1 选择器接口）。

统一签名：`execute_<type>(ctx, step) -> dict`，返回 JSON 可序列化的 output。
- 目标解析失败抛 `TargetNotFound`（可重试）；
- 断言失败抛 `AssertionFailed`（NonRetryable：不重试动作，直接走 on_fail）；
- 子脚本深度 > 5 抛 `SubScriptDepthExceeded`（NonRetryable）。

跨模块缝合线：`app.android.selectors`（resolve_target / check_condition / wait_for / TargetNotFound）
按契约 §14.1 导入；be-driver 尚未落地时用本文件的**最小可用 fallback**（直接基于 Driver 协议
的 dump_hierarchy），测试通过 monkeypatch `app.runner.executors.*` 固化行为。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..db import session_scope
from ..logs import logger
from ..models import Script
from .context import (
    MAX_SUB_DEPTH,
    NonRetryableError,
    RunContext,
    RunFailed,
    RunSnapshot,
    RunStopped,
    StepTimeout,
)

# ---------------------------------------------------------------- 选择器缝合线
try:  # pragma: no cover - 取决于 be-driver 是否已落地
    from ..android.selectors import (  # type: ignore[assignment]
        TargetNotFound,
        check_condition,
        resolve_target,
        wait_for,
    )
except Exception as _selector_import_error:  # noqa: BLE001 - be-driver 未落地/依赖缺失时退化到 fallback
    logger.warning("app.android.selectors unavailable (%s); using fallback", _selector_import_error)
    TargetNotFound = None  # type: ignore[assignment]
    check_condition = None  # type: ignore[assignment]
    resolve_target = None  # type: ignore[assignment]
    wait_for = None  # type: ignore[assignment]


@dataclass
class ResolvedTarget:
    x: int
    y: int
    node: Any = None
    bounds: list[int] = field(default_factory=lambda: [0, 0, 0, 0])


if resolve_target is None:

    class TargetNotFound(Exception):  # type: ignore[no-redef]
        """目标解析失败（可重试）。"""

    def _target_field(target: Any, key: str, default: Any = None) -> Any:
        if target is None:
            return default
        if isinstance(target, dict):
            return target.get(key, default)
        return getattr(target, key, default)

    def _node_field(node: Any, key: str, default: Any = "") -> Any:
        if node is None:
            return default
        if isinstance(node, dict):
            return node.get(key, default)
        return getattr(node, key, default)

    def _find_node(driver: Any, target: Any) -> Any:
        by = _target_field(target, "by")
        value = str(_target_field(target, "value") or "")
        index = int(_target_field(target, "index", 0) or 0)
        if by == "point" or by == "xpath":
            return None
        try:
            tree = driver.dump_hierarchy()
        except Exception:  # 层级抓取失败按"没找到"处理，交给上层重试
            return None
        matches = []
        for node in tree.iter_nodes():
            if not _node_field(node, "visible", True):
                continue
            text = _node_field(node, "text") or ""
            rid = _node_field(node, "resource_id") or ""
            desc = _node_field(node, "content_desc") or ""
            cls = _node_field(node, "class_name") or ""
            matched = False
            if by == "text":
                matched = text == value
            elif by == "text_contains":
                matched = bool(value) and value in text
            elif by == "resource_id":
                matched = rid == value or rid.endswith(":id/" + value)
            elif by == "content_desc":
                matched = desc == value
            elif by == "class_name":
                matched = cls == value or cls.endswith(value)
            if matched:
                matches.append(node)
        if not matches:
            return None
        return matches[index] if 0 <= index < len(matches) else None

    def resolve_target(  # type: ignore[no-redef]
        driver: Any,
        target: Any,
        timeout_ms: int = 0,
        poll_interval_ms: int = 350,
        should_abort: Callable[[], bool] | None = None,
    ) -> ResolvedTarget:
        by = _target_field(target, "by")
        if by == "point":
            x = int(_target_field(target, "x", 0) or 0)
            y = int(_target_field(target, "y", 0) or 0)
            return ResolvedTarget(x=x, y=y, node=None, bounds=[x, y, x, y])
        deadline = time.monotonic() + max(0, int(timeout_ms or 0)) / 1000.0
        interval = max(0.05, int(poll_interval_ms or 350) / 1000.0)
        while True:
            if should_abort is not None and should_abort():
                raise TargetNotFound("target resolution aborted")
            node = _find_node(driver, target)
            if node is not None:
                x, y = node.center
                return ResolvedTarget(x=int(x), y=int(y), node=node, bounds=list(node.bounds))
            if time.monotonic() >= deadline:
                break
            time.sleep(min(interval, max(0.01, deadline - time.monotonic())))
        raise TargetNotFound(
            f"target not found: by={by} value={_target_field(target, 'value')!r}"
        )

    def check_condition(driver: Any, target: Any, state: str = "visible") -> bool:  # type: ignore[no-redef]
        found = _find_node(driver, target) is not None
        return found if state == "visible" else not found

    def wait_for(  # type: ignore[no-redef]
        driver: Any,
        target: Any,
        state: str,
        timeout_ms: int,
        poll_interval_ms: int = 350,
        should_abort: Callable[[], bool] | None = None,
    ) -> bool:
        deadline = time.monotonic() + max(0, int(timeout_ms or 0)) / 1000.0
        interval = max(0.05, int(poll_interval_ms or 350) / 1000.0)
        while True:
            if should_abort is not None and should_abort():
                return False
            if check_condition(driver, target, state):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(interval, max(0.01, deadline - time.monotonic())))


def wait_condition(
    ctx: RunContext,
    target: Any,
    state: str,
    timeout_ms: int,
    interval_ms: int | None = None,
) -> bool:
    """可被 pause/stop/节点超时打断的轮询等待（单次探测仍走契约里的 wait_for）。

    §9：所有等待可被 pause/stop 打断（≥每 350ms 检查），因此不能把大 timeout 一次性
    交给阻塞轮询，而是按 poll_interval 切片，切片之间走 ctx.sleep_interruptible。
    """
    target = ctx.interpolate_target(target)
    timeout = max(0, int(timeout_ms or 0))
    interval = max(50, int(interval_ms or ctx.poll_interval_ms))
    deadline = time.monotonic() + timeout / 1000.0
    while True:
        ctx.wait_if_paused()
        if ctx.should_stop():
            raise RunStopped("stop requested while waiting")
        if wait_for(
            ctx.driver,
            target,
            state,
            0,
            poll_interval_ms=interval,
            should_abort=ctx.abort_requested,
        ):
            return True
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            return False
        ctx.sleep_interruptible(min(interval, remaining_ms))


# ---------------------------------------------------------------- 执行器异常
class AssertionFailed(NonRetryableError):
    """断言失败：不重试动作，直接走 on_fail。"""

    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.detail = detail or {}


class ShellCommandFailed(Exception):
    """shell 退出码不满足 check_exit_code（可重试）。"""

    def __init__(self, message: str, output: dict[str, Any]) -> None:
        super().__init__(message)
        self.output = output


class SubScriptFailed(Exception):
    """子脚本执行失败（可重试）。"""

    def __init__(self, message: str, output: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.output = output or {}


class SubScriptDepthExceeded(NonRetryableError):
    pass


def _resolve(ctx: RunContext, target: Any, timeout_ms: int | None = None) -> Any:
    """插值 + 解析目标；超时取节点 timeout（引擎已设置 ctx.step_timeout_ms）。"""
    target = ctx.interpolate_target(target)
    timeout = ctx.step_timeout_ms if timeout_ms is None else int(timeout_ms)
    return resolve_target(
        ctx.driver,
        target,
        timeout_ms=timeout,
        poll_interval_ms=ctx.poll_interval_ms,
        should_abort=ctx.abort_requested,
    )


def _resolved_output(resolved: Any) -> dict[str, Any]:
    return {
        "x": int(getattr(resolved, "x", 0)),
        "y": int(getattr(resolved, "y", 0)),
        "bounds": list(getattr(resolved, "bounds", []) or []),
    }


def _node_text(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, dict):
        return str(node.get("text") or node.get("content_desc") or "")
    return str(getattr(node, "text", "") or getattr(node, "content_desc", "") or "")


# ---------------------------------------------------------------- 12 类执行器
def execute_click(ctx: RunContext, step: Any) -> dict[str, Any]:
    resolved = _resolve(ctx, step.params.target)
    ctx.ensure_actionable()  # 超时/stop 强切后不再产生迟到的点击
    ctx.driver.click(resolved.x, resolved.y)
    return _resolved_output(resolved)


def execute_long_click(ctx: RunContext, step: Any) -> dict[str, Any]:
    resolved = _resolve(ctx, step.params.target)
    duration = int(getattr(step.params, "duration_ms", 800) or 800)
    ctx.ensure_actionable()
    ctx.driver.long_press(resolved.x, resolved.y, duration_ms=duration)
    return {**_resolved_output(resolved), "duration_ms": duration}


def _recorded_path_samples(path: Any, max_points: int = 240) -> list[tuple[int, int, int]]:
    """读取录制轨迹 [(dt_ms, x, y)]，保留原始时间戳（不重采样）。

    scrcpy 控制通道可以按每条 MOVE 的真实间隔注入，因此这里不做等时间重采样，
    直接把录制时的手指轨迹和速度/停顿交给注入器。
    """
    samples: list[tuple[int, int, int]] = []
    for item in path or []:
        try:
            # 运行时是 Pydantic GestureSample；单测/直接调用也可能传原始 dict。
            t = int((item.get("dt_ms", 0) if isinstance(item, dict) else getattr(item, "dt_ms", 0)) or 0)
            x = int((item.get("x", 0) if isinstance(item, dict) else getattr(item, "x", 0)) or 0)
            y = int((item.get("y", 0) if isinstance(item, dict) else getattr(item, "y", 0)) or 0)
        except (TypeError, ValueError):
            continue
        samples.append((max(0, t), x, y))
    if len(samples) < 2:
        return []
    samples.sort(key=lambda item: item[0])
    merged: list[tuple[int, int, int]] = []
    for sample in samples:
        if merged and sample[0] <= merged[-1][0]:
            merged[-1] = sample
        else:
            merged.append(sample)
    if len(merged) > max_points:
        step = max(1, (len(merged) + max_points - 1) // max_points)
        reduced = merged[::step]
        if reduced[-1] != merged[-1]:
            reduced.append(merged[-1])
        merged = reduced
    return merged


def _try_scrcpy_path(
    ctx: RunContext,
    samples: list[tuple[int, int, int]],
    duration_ms: int,
    output: dict[str, Any],
) -> bool:
    """有活跃镜像时用 scrcpy control 通道回放轨迹（含变速/停顿），最贴近录制。

    回放可被 stop / 节点超时打断：中止时注入器会补 UP 并抛 ``InjectionAborted``，
    这里必须把中断原样抛回引擎，**不能**再回落到 u2 两点 swipe（否则 stop 后还会产生新手势）。
    """
    serial = getattr(ctx.driver, "serial", None)
    if not serial or len(samples) < 2:
        return False
    from ..android.scrcpy import InjectionAborted  # 局部导入：避免 runner 模块启动即依赖媒体层

    try:
        from ..mirror import get_session

        session = get_session(str(serial))
        if session is None or not getattr(session, "active", False):
            return False
        if not session.inject_path(samples, duration_ms, should_abort=ctx.abort_requested):
            return False
    except InjectionAborted:
        if ctx.should_stop():
            raise RunStopped("stop requested during gesture replay") from None
        raise StepTimeout("gesture replay aborted by step timeout") from None
    except Exception as exc:  # noqa: BLE001 - 镜像/控制通道不可用时回退 u2
        logger.warning("scrcpy path replay failed, fallback to u2: %s", exc)
        return False
    output["duration_ms"] = max(int(duration_ms or 0), samples[-1][0])
    output["path_points"] = len(samples)
    output["injector"] = "scrcpy"
    return True


def _execute_gesture(ctx: RunContext, step: Any, default_duration: int) -> dict[str, Any]:
    start = _resolve(ctx, step.params.from_)
    end = _resolve(ctx, step.params.to)
    duration = int(getattr(step.params, "duration_ms", default_duration) or default_duration)
    output: dict[str, Any] = {
        "from": _resolved_output(start),
        "to": _resolved_output(end),
        "duration_ms": duration,
    }
    samples = _recorded_path_samples(getattr(step.params, "path", None))
    ctx.ensure_actionable()
    if samples and _try_scrcpy_path(ctx, samples, duration, output):
        return output
    # 没有镜像控制通道时退化为两点头滑动；u2 的 swipePoints 在部分游戏/设备上会卡死。
    ctx.ensure_actionable()
    ctx.driver.swipe(start.x, start.y, end.x, end.y, duration_ms=duration)
    return output


def execute_swipe(ctx: RunContext, step: Any) -> dict[str, Any]:
    return _execute_gesture(ctx, step, 300)


def execute_drag(ctx: RunContext, step: Any) -> dict[str, Any]:
    """拖拽回放：录制带轨迹时优先用当前镜像的 scrcpy 控制通道按原时序重演。

    v0.2.5 之前 drag 一律走原生 drag，完全丢掉录制轨迹；Unity/游戏界面上
    native drag 还会长时间不返回，把节点卡到超时。没有镜像通道时退化为两点
    swipe，只有“无轨迹的手动 drag 节点”才继续使用驱动原生 drag。
    """
    start = _resolve(ctx, step.params.from_)
    end = _resolve(ctx, step.params.to)
    duration = int(getattr(step.params, "duration_ms", 1000) or 1000)
    output: dict[str, Any] = {
        "from": _resolved_output(start),
        "to": _resolved_output(end),
        "duration_ms": duration,
    }
    samples = _recorded_path_samples(getattr(step.params, "path", None))
    ctx.ensure_actionable()
    if samples and _try_scrcpy_path(ctx, samples, duration, output):
        return output

    # 录制出来的“慢滑动”如果拿不到镜像通道，走两点 swipe 比 native drag 更稳：
    # 真机上 native drag 在 Unity 等界面会长时间不返回，直接把节点卡到超时。
    if samples:
        ctx.ensure_actionable()
        ctx.driver.swipe(start.x, start.y, end.x, end.y, duration_ms=duration)
        return output

    ctx.ensure_actionable()
    drag_fn = getattr(ctx.driver, "drag", None)
    if callable(drag_fn):
        drag_fn(start.x, start.y, end.x, end.y, duration_ms=duration)
    else:
        ctx.driver.swipe(start.x, start.y, end.x, end.y, duration_ms=duration)
    return output


def execute_input(ctx: RunContext, step: Any) -> dict[str, Any]:
    params = step.params
    text = ctx.interpolate(params.text)
    output: dict[str, Any] = {"text": text, "length": len(str(text))}
    target = getattr(params, "target", None)
    if target is not None:
        resolved = _resolve(ctx, target)
        ctx.ensure_actionable()
        ctx.driver.click(resolved.x, resolved.y)
        output["point"] = _resolved_output(resolved)
    if getattr(params, "clear_first", False):
        ctx.ensure_actionable()
        ctx.driver.clear_text()
        output["cleared"] = True
    ctx.ensure_actionable()
    ctx.driver.input_text(str(text))
    return output


def execute_key(ctx: RunContext, step: Any) -> dict[str, Any]:
    params = step.params
    key = params.key if getattr(params, "key", None) is not None else params.keycode
    ctx.ensure_actionable()
    ctx.driver.key(key)
    return {"key": key}


def execute_wait(ctx: RunContext, step: Any) -> dict[str, Any]:
    params = step.params
    if params.mode == "sleep":
        ms = int(params.ms or 0)
        ctx.sleep_interruptible(ms)
        return {"mode": "sleep", "waited_ms": ms}
    target = ctx.interpolate_target(params.target)
    state = params.state
    started = time.monotonic()
    ok = wait_condition(
        ctx,
        target,
        state,
        int(params.timeout_ms),
        interval_ms=int(getattr(params, "interval_ms", ctx.poll_interval_ms) or ctx.poll_interval_ms),
    )
    waited = int((time.monotonic() - started) * 1000)
    if not ok:
        raise TargetNotFound(f"wait until {state} timed out after {params.timeout_ms} ms")
    return {"mode": "until", "state": state, "waited_ms": waited}


def execute_app(ctx: RunContext, step: Any) -> dict[str, Any]:
    params = step.params
    action = params.action
    package = ctx.interpolate(params.package) if params.package else None
    activity = ctx.interpolate(params.activity) if params.activity else None
    if not package:
        foreground = ctx.driver.current_app() or {}
        package = foreground.get("package")
    if not package:
        raise NonRetryableError("app step requires package (no foreground package available)")
    ctx.ensure_actionable()
    if action == "launch":
        ctx.driver.app_start(package, activity)
    elif action == "stop":
        ctx.driver.app_stop(package)
    elif action == "clear":
        ctx.driver.app_clear(package)
    else:  # restart
        ctx.driver.app_stop(package)
        ctx.sleep_interruptible(300)
        ctx.ensure_actionable()
        ctx.driver.app_start(package, activity)
    return {"action": action, "package": package, "activity": activity}


def execute_shell(ctx: RunContext, step: Any) -> dict[str, Any]:
    params = step.params
    command = str(ctx.interpolate(params.command))
    timeout_s = max(0.1, int(params.timeout_ms or 10_000) / 1000.0)
    ctx.ensure_actionable()
    result = ctx.driver.shell(command, timeout_s=timeout_s)
    stdout = str(getattr(result, "stdout", "") or "")
    stderr = str(getattr(result, "stderr", "") or "")
    code = int(getattr(result, "code", 0) or 0)
    output = {
        "command": command,
        "code": code,
        "stdout": stdout[:8000],
        "stderr": stderr[:8000],
    }
    if bool(params.check_exit_code) and code != 0:
        raise ShellCommandFailed(f"shell command exited with code {code}", output)
    return output


def execute_assert(ctx: RunContext, step: Any) -> dict[str, Any]:
    params = step.params
    kind = params.kind
    passed = False
    detail: dict[str, Any] = {"kind": kind}
    if kind in ("exists", "not_exists"):
        state = "visible" if kind == "exists" else "gone"
        target = ctx.interpolate_target(params.target)
        passed = bool(check_condition(ctx.driver, target, state))
        detail["state"] = state
    elif kind == "text_contains":
        value = ctx.interpolate(params.value or "")
        target = ctx.interpolate_target(params.target)
        resolved = resolve_target(
            ctx.driver,
            target,
            timeout_ms=0,
            poll_interval_ms=ctx.poll_interval_ms,
            should_abort=ctx.abort_requested,
        )
        actual = _node_text(getattr(resolved, "node", None))
        passed = str(value) in actual
        detail.update({"value": value, "actual": actual[:400]})
    else:  # foreground
        value = str(ctx.interpolate(params.value or ""))
        foreground = ctx.driver.current_app() or {}
        package = str(foreground.get("package") or "")
        activity = str(foreground.get("activity") or "")
        passed = package == value or value in package or (bool(activity) and value in activity)
        detail.update({"value": value, "foreground": foreground})
    ctx.emit(
        "assert_result",
        {"step_id": step.id, "kind": kind, "passed": passed, "detail": detail},
    )
    if not passed:
        raise AssertionFailed(f"assert {kind} failed: {detail}", detail)
    return {"kind": kind, "passed": True, **detail}


def execute_sub_script(ctx: RunContext, step: Any) -> dict[str, Any]:
    params = step.params
    script_id = str(ctx.interpolate(params.script_id))
    ctx.ensure_actionable()
    child_depth = ctx.depth + 1
    if child_depth > MAX_SUB_DEPTH:
        raise SubScriptDepthExceeded(
            f"sub_script nesting exceeds {MAX_SUB_DEPTH} levels (script={script_id})"
        )
    with session_scope() as session:
        row = session.get(Script, script_id)
        data = dict(row.data) if row is not None and row.data else None
    if data is None:
        raise NonRetryableError(f"sub script not found: {script_id}")
    try:
        snapshot = RunSnapshot.from_data(data, script_id=script_id)
    except Exception as exc:  # 子脚本快照非法：确定性错误，不重试
        raise NonRetryableError(f"invalid sub script {script_id}: {exc}") from exc

    variables = {
        str(key): ctx.interpolate(value)
        for key, value in (getattr(params, "variables", None) or {}).items()
    }
    child = ctx.child(snapshot, variables=variables)

    from .engine import run_script_with_context  # 延迟导入，避免 engine <-> executors 循环

    try:
        summary = run_script_with_context(child)
    except RunFailed as exc:
        raise SubScriptFailed(
            f"sub script {script_id} failed: {exc.message}",
            {"script_id": script_id, "error": exc.error},
        ) from exc
    output = {"script_id": script_id, "summary": summary}
    if int(summary.get("failed_steps", 0)) > 0 or summary.get("status") == "failed":
        raise SubScriptFailed(f"sub script {script_id} has failed steps", output)
    return output


def execute_screenshot(ctx: RunContext, step: Any) -> dict[str, Any]:
    name = ctx.interpolate(step.params.name) if getattr(step.params, "name", None) else None
    name = name or step.name or step.id
    info = ctx.save_screenshot(str(name))
    return {
        "artifact_id": info.get("artifact_id"),
        "name": info.get("name"),
        "size": info.get("size"),
    }


EXECUTORS: dict[str, Callable[[RunContext, Any], dict[str, Any]]] = {
    "click": execute_click,
    "long_click": execute_long_click,
    "swipe": execute_swipe,
    "drag": execute_drag,
    "input": execute_input,
    "key": execute_key,
    "wait": execute_wait,
    "app": execute_app,
    "shell": execute_shell,
    "assert": execute_assert,
    "sub_script": execute_sub_script,
    "screenshot": execute_screenshot,
}


def execute_step(ctx: RunContext, step: Any) -> dict[str, Any]:
    """按节点 type 分发执行器。"""
    step_type = str(getattr(step, "type", ""))
    executor = EXECUTORS.get(step_type)
    if executor is None:
        raise NonRetryableError(f"unsupported step type: {step_type}")
    result = executor(ctx, step)
    return result if isinstance(result, dict) else {}


def selector_backend() -> str:
    """诊断用：当前选择器来自 be-driver 还是 fallback。"""
    return "be-driver" if check_condition is not None else "fallback"
