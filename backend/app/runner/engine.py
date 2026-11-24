"""执行引擎（契约 §3/§5/§9/§14.4）。

线程模型：
- `start_run()` 把 run 提交到模块级 ThreadPoolExecutor(max_workers=2)（幂等）；
- 主循环在池线程里跑；每个节点再放进一个 **daemon 线程**执行，主循环按 20ms 轮询
  节点超时 / stop 强切（stop 后最多等 stop_grace_s，默认 5s）；
- 引擎线程自身绝不无限阻塞：节点线程可被放弃（daemon），最终状态一定落库。

状态机：
    queued → running → (paused ⇄ running) → succeeded | failed | stopped
    stop 请求期间 DB 显示 stopping，结束后 stopped。
    aborted 留给"运行被外部撤销"场景（当前实现只在 run 行被删除时静默结束）。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from ..capture import logcat
from ..db import session_scope
from ..errors import ApiError
from ..events import RunEventBus
from ..logs import logger
from ..models import Run, StepResult, now_iso
from . import conditions, controls, executors
from .context import (
    BudgetExceeded,
    NonRetryableError,
    RunContext,
    RunFailed,
    RunSnapshot,
    RunStopped,
    StepAbandoned,
    StepAttempt,
    StepTimeout,
    TotalTimeout,
    default_limits,
)

# ---------------------------------------------------------------- 跨模块缝合线
try:  # pragma: no cover - 取决于 be-core device_registry 是否已落地
    from ..services.device_registry import acquire, get_driver, release

    HAS_DEVICE_REGISTRY = True
except Exception as _registry_import_error:  # noqa: BLE001 - be-core 未落地时退化
    logger.warning(
        "app.services.device_registry unavailable (%s); using FakeDriver fallback",
        _registry_import_error,
    )
    HAS_DEVICE_REGISTRY = False

    def acquire(serial: str, purpose: str, ref_id: Any) -> None:  # type: ignore[misc]
        return None

    def release(serial: str, purpose: str, ref_id: Any) -> None:  # type: ignore[misc]
        return None

    def get_driver(serial: str | None = None):  # type: ignore[misc]
        from ..android.driver import FakeDriver

        if serial and str(serial).startswith("fake"):
            return FakeDriver(str(serial))
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            f"device registry unavailable (serial={serial})",
            {"serial": serial},
        )


# ---------------------------------------------------------------- 线程池
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="autotest-run")
_ACTIVE_LOCK = threading.RLock()
_ACTIVE: dict[int, Future] = {}

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "aborted", "stopped", "interrupted"})
_ACTIVE_STATUSES = frozenset({"queued", "running", "paused", "stopping"})


def start_run(run_id: int) -> None:
    """提交运行到线程池；幂等（同一 run 已排队/运行中则忽略）。"""
    run_id = int(run_id)
    with _ACTIVE_LOCK:
        future = _ACTIVE.get(run_id)
        if future is not None and not future.done():
            return
        _ACTIVE[run_id] = _EXECUTOR.submit(_run_guarded, run_id)


def is_active(run_id: int) -> bool:
    with _ACTIVE_LOCK:
        future = _ACTIVE.get(int(run_id))
        return future is not None and not future.done()


def active_run_ids() -> list[int]:
    with _ACTIVE_LOCK:
        return [rid for rid, future in _ACTIVE.items() if not future.done()]


def wait_for_idle(timeout_s: float = 10.0) -> bool:
    """等待所有已提交运行结束（测试 / 优雅退出用）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not active_run_ids():
            return True
        time.sleep(0.02)
    return not active_run_ids()


def _run_guarded(run_id: int) -> None:
    try:
        _run(run_id)
    except BaseException:  # _run 内部已兜底，这里是最后一道保险
        logger.exception("run %s crashed", run_id)
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.pop(int(run_id), None)


# ---------------------------------------------------------------- 运行入口
def _run(run_id: int) -> None:
    data = _load_run_data(run_id)
    if data is None:
        logger.warning("run %s not found; skip", run_id)
        return
    if data["status"] != "queued":
        logger.info("run %s already %s; skip", run_id, data["status"])
        return
    serial = str(data["device_serial"] or "auto")
    try:
        snapshot = RunSnapshot.from_data(data["script_snapshot"] or {}, script_id=data["script_id"])
    except Exception as exc:  # 脚本快照损坏
        _finish_early_failure(run_id, serial, "VALIDATION_ERROR", f"invalid script snapshot: {exc}")
        return

    _update_run(run_id, status="running", started_at=now_iso(), error=None)
    control = controls.create_control(run_id, debug=data["debug"] or {})
    try:
        driver = get_driver(serial)
    except ApiError as exc:
        _finish_early_failure(run_id, serial, exc.code, exc.message)
        controls.cleanup(run_id)
        return
    except Exception as exc:
        logger.exception("run %s failed to get driver for %s", run_id, serial)
        _finish_early_failure(run_id, serial, "DEVICE_UNAVAILABLE", str(exc))
        controls.cleanup(run_id)
        return

    limits = default_limits()
    ctx = RunContext(
        run_id=run_id,
        driver=driver,
        script=snapshot,
        variables=dict(data["variables"] or {}),
        controls=control,
        limits=limits,
    )
    ctx.debug = dict(data["debug"] or {})
    ctx.on_pause = lambda entered: _on_pause_change(ctx, entered)

    _safe_emit(
        run_id,
        "run_started",
        {
            "script_id": data["script_id"],
            "script_name": snapshot.name,
            "device_serial": serial,
            "total_steps": len(snapshot.steps),
        },
    )
    _safe_emit(run_id, "run_state", {"status": "running", "step_index": 0})

    # 运行生命周期挂接：按 capture.logcat_auto_attach + 脚本 settings.logcat 自动采集。
    # 只 attach 本 run 拥有的会话；停止时也只停本 run 启动的采集（手动采集不误停）。
    _attach_run_logcat(run_id, snapshot)
    summary: dict[str, Any] = {}
    status = "failed"
    error: dict[str, Any] | None = None
    try:
        summary = run_script_with_context(ctx)
        status = str(summary.get("status") or "succeeded")
        error = summary.get("error")
    except RunStopped as exc:
        status = "stopped"
        summary = getattr(exc, "summary", None) or {}
        error = {"code": "STOPPED", "message": str(exc) or "stopped"}
    except RunFailed as exc:
        status = "failed"
        summary = getattr(exc, "summary", None) or {}
        error = dict(exc.error)
    except BaseException as exc:  # noqa: BLE001 - 绝不能让 run 卡在 running
        logger.exception("run %s failed unexpectedly", run_id)
        status = "failed"
        summary = getattr(exc, "summary", None) or summary
        error = {"code": "INTERNAL", "message": f"{type(exc).__name__}: {exc}"}
    finally:
        # 先收尾 logcat（写 artifact + artifacts 行），再落运行终态；异常/stop 也走这里。
        _stop_run_logcat(run_id, serial)
        ended_at = now_iso()
        duration_ms = ctx.active_elapsed_ms()
        final_summary = {
            "passed_steps": int(summary.get("passed_steps", 0)),
            "failed_steps": int(summary.get("failed_steps", 0)),
            "skipped_steps": int(summary.get("skipped_steps", 0)),
            "duration_ms": duration_ms,
            "total_steps": len(snapshot.steps),
            "status": status,
        }
        _update_run(
            run_id,
            status=status,
            ended_at=ended_at,
            error=error,
            summary=final_summary,
        )
        if error is not None:
            _safe_emit(run_id, "error", error)
        _safe_emit(
            run_id,
            "run_finished",
            {
                "status": status,
                "duration_ms": duration_ms,
                "passed_steps": final_summary["passed_steps"],
                "failed_steps": final_summary["failed_steps"],
                "skipped_steps": final_summary["skipped_steps"],
            },
        )
        _safe_emit(run_id, "run_state", {"status": status})
        try:
            release(serial, "run", str(run_id))
        except Exception:  # 释放占用失败不能影响最终状态
            logger.exception("run %s failed to release device %s", run_id, serial)
        controls.cleanup(run_id)


def _attach_run_logcat(run_id: int, snapshot: RunSnapshot) -> None:
    """自动 attach logcat；采集失败绝不能拖垮运行。"""
    try:
        capture = logcat.attach_if_needed(run_id, snapshot.settings)
        if capture is not None:
            logger.info("run %s attached logcat capture (serial=%s)", run_id, capture.serial)
    except Exception:
        logger.exception("run %s failed to attach logcat capture", run_id)


def _stop_run_logcat(run_id: int, serial: str) -> None:
    """停止本 run 启动的 logcat 并保存 artifact；幂等且不误停手动采集。"""
    try:
        path = logcat.stop_run_capture(run_id, serial)
        if path is not None:
            logger.info("run %s saved logcat artifact: %s", run_id, path)
    except Exception:
        logger.exception("run %s failed to stop logcat capture", run_id)


def _finish_early_failure(
    run_id: int,
    serial: str,
    code: str,
    message: str,
) -> None:
    """运行还没进入主循环就失败（脚本损坏 / 取 driver 失败）：落库最终态 + 事件。"""
    error = {"code": code, "message": message}
    summary = {"passed_steps": 0, "failed_steps": 0, "skipped_steps": 0, "duration_ms": 0}
    _update_run(run_id, status="failed", ended_at=now_iso(), error=error, summary=summary)
    _safe_emit(run_id, "error", error)
    _safe_emit(
        run_id,
        "run_finished",
        {
            "status": "failed",
            "duration_ms": 0,
            "passed_steps": 0,
            "failed_steps": 0,
            "skipped_steps": 0,
        },
    )
    _safe_emit(run_id, "run_state", {"status": "failed"})
    try:
        release(serial, "run", str(run_id))
    except Exception:  # pragma: no cover
        logger.exception("run %s failed to release device %s", run_id, serial)


def _on_pause_change(ctx: RunContext, entered: bool) -> None:
    control = ctx.controls
    if control is None:
        return
    if entered:
        step_id = control.paused_at_step_id or ctx.current_step_id
        reason = control.pause_reason or "user"
        _update_run(ctx.run_id, status="paused", current_step_id=step_id)
        _safe_emit(ctx.run_id, "run_paused", {"reason": reason, "at_step_id": step_id})
        _safe_emit(ctx.run_id, "run_state", {"status": "paused", "step_index": ctx.step_index})
        return
    if control.stop_event.is_set():
        return
    warnings = list(control.resume_warnings or [])
    control.resume_warnings = []
    control.pause_reason = "user"
    _update_run(ctx.run_id, status="running")
    _safe_emit(ctx.run_id, "run_resumed", {"resume_kind": "hot", "warnings": warnings})
    _safe_emit(ctx.run_id, "run_state", {"status": "running", "step_index": ctx.step_index})


# ---------------------------------------------------------------- 主循环
def run_script_with_context(ctx: RunContext) -> dict[str, Any]:
    """顺序执行 ctx.script 的节点；返回 summary。root 与 sub_script 子上下文共用本函数。"""
    steps = list(ctx.script.steps)
    index_by_id = {step.id: pos for pos, step in enumerate(steps)}
    control = ctx.controls
    summary: dict[str, Any] = {"status": "succeeded", "error": None}
    # 每个节点"最近一次"结果：goto 重入后以最新一次为准（重试/跳转恢复成功不算最终失败）
    outcomes: dict[str, str] = {}
    retry_used: dict[str, int] = {}
    index = 0
    last_error: dict[str, Any] | None = None

    try:
        while index < len(steps):
            if ctx.should_stop():
                raise RunStopped("stop requested")

            step = steps[index]
            ctx.step_index = index
            ctx.current_step_id = step.id
            if control is not None:
                control.current_step_id = step.id
            _update_run_progress(ctx.run_id, step.id, index)

            try:
                ctx.check_total_timeout()
            except RunFailed as exc:
                # 总时长保护环触发：把当前节点记为 failed（TOTAL_TIMEOUT），便于 UI 定位
                last_error = dict(exc.error)
                outcomes[step.id] = "failed"
                _record_attempt(ctx, step, index, "failed", 0, now_iso(), 0, exc.error, None)
                raise

            ctx.wait_if_paused()
            if ctx.should_stop():
                raise RunStopped("stop requested")
            # 调试 run_from：暂停中被请求则立刻跳转，运行中则在节点边界生效
            jump_index = _consume_run_from(ctx, index_by_id, retry_used, steps)
            if jump_index is not None:
                index = jump_index
                continue

            if not bool(getattr(step, "enabled", True)):
                _record_skipped(ctx, step, index, "disabled")
                outcomes[step.id] = "skipped"
                index += 1
                continue

            # breakpoint：命中则进入干预模式，等待 resume/step
            breakpoints = list(control.debug.get("breakpoints") or []) if control is not None else []
            if step.id in breakpoints:
                controls.request_pause(ctx.run_id, reason="breakpoint", at_step_id=step.id)
                ctx.wait_if_paused()
                if ctx.should_stop():
                    raise RunStopped("stop requested")
                jump_index = _consume_run_from(ctx, index_by_id, retry_used, steps)
                if jump_index is not None:
                    index = jump_index
                    continue

            # when 条件（v0.2 条件树）：when_started -> when_result -> 后续 step 事件
            when = getattr(step, "when", None)
            when_info: dict[str, Any] | None = None
            if when is not None:
                summary_text = conditions.condition_summary(when.condition)
                _safe_emit(
                    ctx.run_id,
                    "when_started",
                    {
                        "step_id": step.id,
                        "mode": when.mode,
                        "timeout_ms": int(when.timeout_ms),
                        "poll_interval_ms": int(when.poll_interval_ms),
                        "condition_summary": summary_text,
                    },
                )
                wait_result = conditions.wait_condition_tree(ctx, when)
                when_info = {
                    "met": bool(wait_result.met),
                    "waited_ms": int(wait_result.waited_ms),
                    "condition_summary": wait_result.condition_summary,
                }
                _safe_emit(
                    ctx.run_id,
                    "when_result",
                    {
                        "step_id": step.id,
                        "mode": when.mode,
                        "met": when_info["met"],
                        "waited_ms": when_info["waited_ms"],
                        "condition_summary": when_info["condition_summary"],
                    },
                )
                if not wait_result.met:
                    if when.mode == "optional":
                        _record_skipped(
                            ctx, step, index, "when_not_met", when_info=when_info
                        )
                        outcomes[step.id] = "skipped"
                        index += 1
                        continue
                    when_error = {
                        "code": "WHEN_NOT_MET",
                        "message": f"when require not met ({summary_text}) "
                        f"within {when.timeout_ms} ms",
                        "details": {"when": when_info},
                    }
                    last_error = when_error
                    _record_attempt(
                        ctx,
                        step,
                        index,
                        "failed",
                        0,
                        now_iso(),
                        0,
                        when_error,
                        None,
                        when_info=when_info,
                    )
                    outcomes[step.id] = "failed"
                    # when 不满足也会走 on_fail（含 goto）：必须计入步骤预算，
                    # 否则 goto 指回同一个 when 失败的节点会空转到 max_total_ms（30min），
                    # 期间只写 StepResult 不执行任何动作。
                    ctx.check_step_budget()
                    failed_index = index
                    index = _handle_on_fail(ctx, step, index, index_by_id, steps, retry_used, summary)
                    if index is not None:
                        continue
                    _skip_remaining(ctx, steps, failed_index + 1, outcomes)
                    raise RunFailed(
                        when_error["code"],
                        when_error["message"],
                        step_id=step.id,
                        details=when_error.get("details"),
                    )

            ctx.sleep_interruptible(int(getattr(step, "delay_before_ms", 0) or 0))

            retry_count, retry_interval = _retry_policy(ctx, step)
            attempts = 0
            fail_error: dict[str, Any] | None = None
            fail_output: dict[str, Any] | None = None
            step_duration = 0

            while True:
                attempts += 1
                try:
                    ctx.check_step_budget()
                    ctx.check_total_timeout()
                except RunFailed as exc:
                    last_error = dict(exc.error)
                    _record_attempt(
                        ctx,
                        step,
                        index,
                        "failed",
                        attempts,
                        now_iso(),
                        0,
                        exc.error,
                        None,
                        when_info=when_info,
                    )
                    outcomes[step.id] = "failed"
                    raise
                ctx.wait_if_paused()
                if ctx.should_stop():
                    raise RunStopped("stop requested")
                _safe_emit(
                    ctx.run_id,
                    "step_started",
                    {
                        "step_id": step.id,
                        "name": step.name,
                        "type": step.type,
                        "index": index,
                        "attempt": attempts,
                    },
                )
                started_iso = now_iso()
                started_active = ctx.active_elapsed_ms()
                timeout_ms = _step_timeout(ctx, step)
                attempt = ctx.begin_step(timeout_ms)
                try:
                    output = _execute_step_with_timeout(ctx, step, timeout_ms, attempt)
                except RunStopped:
                    step_duration = max(0, ctx.active_elapsed_ms() - started_active)
                    stop_error = {"code": "STOPPED", "message": "run stopped"}
                    _record_attempt(
                        ctx,
                        step,
                        index,
                        "failed",
                        attempts,
                        started_iso,
                        step_duration,
                        stop_error,
                        None,
                        when_info=when_info,
                    )
                    raise
                except BaseException as exc:  # noqa: BLE001 - 统一转 step error
                    step_duration = max(0, ctx.active_elapsed_ms() - started_active)
                    step_error = _error_from_exception(exc)
                    fail_output = _output_from_exception(exc)
                    retryable = not isinstance(
                        exc, (NonRetryableError, RunFailed, RunStopped, StepAbandoned)
                    )
                    if retryable and retry_used.get(step.id, 0) < retry_count:
                        retry_used[step.id] = retry_used.get(step.id, 0) + 1
                        _safe_emit(
                            ctx.run_id,
                            "step_retry",
                            {"step_id": step.id, "attempt": attempts, "error": step_error},
                        )
                        ctx.sleep_interruptible(retry_interval)
                        continue
                    fail_error = step_error
                    last_error = step_error
                    _record_attempt(
                        ctx,
                        step,
                        index,
                        "failed",
                        attempts,
                        started_iso,
                        step_duration,
                        step_error,
                        fail_output,
                        when_info=when_info,
                    )
                    outcomes[step.id] = "failed"
                    break
                else:
                    step_duration = max(0, ctx.active_elapsed_ms() - started_active)
                    retry_used[step.id] = 0
                    ctx.sleep_interruptible(int(getattr(step, "delay_after_ms", 0) or 0))
                    _record_attempt(
                        ctx,
                        step,
                        index,
                        "passed",
                        attempts,
                        started_iso,
                        step_duration,
                        None,
                        output,
                        when_info=when_info,
                    )
                    outcomes[step.id] = "passed"
                    break
                finally:
                    ctx.end_step()

            if fail_error is None:
                # 成功：进入下一节点；step_mode 在每个节点后暂停等待 debug step/continue
                index += 1
                if control is not None and bool(control.debug.get("step_mode")):
                    controls.request_pause(ctx.run_id, reason="step_mode", at_step_id=step.id)
                    ctx.wait_if_paused()
                    if ctx.should_stop():
                        raise RunStopped("stop requested")
                continue

            # 失败：on_fail 策略
            next_index = _handle_on_fail(
                ctx,
                step,
                index,
                index_by_id,
                steps,
                retry_used,
                summary,
                fail_error,
            )
            if next_index is not None:
                index = next_index
                continue
            error_code = (fail_error or {}).get("code", "STEP_FAILED")
            error_message = (fail_error or {}).get("message", "step failed")
            _skip_remaining(ctx, steps, index + 1, outcomes)
            raise RunFailed(str(error_code), str(error_message), step_id=step.id)
    except RunStopped as exc:
        exc.summary = _apply_outcomes(summary, outcomes)  # type: ignore[attr-defined]
        _restore_parent_progress(ctx)
        raise
    except RunFailed as exc:
        exc.summary = _apply_outcomes(summary, outcomes)  # type: ignore[attr-defined]
        _restore_parent_progress(ctx)
        raise

    _apply_outcomes(summary, outcomes)
    _restore_parent_progress(ctx)
    status = "failed" if summary["failed_steps"] > 0 else "succeeded"
    summary["status"] = status
    summary["error"] = last_error if status == "failed" else None
    return summary


def _handle_on_fail(
    ctx: RunContext,
    step: Any,
    index: int,
    index_by_id: dict[str, int],
    steps: list[Any],
    retry_used: dict[str, int],
    summary: dict[str, Any],
    fail_error: dict[str, Any] | None = None,
) -> int | None:
    """返回下一个要执行的 index（continue/goto）；返回 None 表示 abort。"""
    on_fail = getattr(step, "on_fail", None)
    action = on_fail.action if on_fail is not None else ctx.script.parsed.settings.on_fail
    if action == "continue":
        _safe_emit(
            ctx.run_id,
            "log",
            {"level": "info", "message": f"on_fail=continue after step {step.id}"},
        )
        return index + 1
    if action == "goto":
        goto_id = getattr(on_fail, "target_id", None)
        target_index = index_by_id.get(str(goto_id)) if goto_id else None
        if target_index is None:
            return None
        reset_retry_used(retry_used, steps, target_index)
        _safe_emit(
            ctx.run_id,
            "log",
            {"level": "info", "message": f"on_fail=goto {goto_id} after step {step.id}"},
        )
        return target_index
    return None


def reset_retry_used(retry_used: dict[str, int], steps: list[Any], from_index: int) -> None:
    """goto 重入：重置目标节点及其后的 retry 计数。"""
    for step in steps[max(0, int(from_index)) :]:
        retry_used.pop(step.id, None)


def _restore_parent_progress(ctx: RunContext) -> None:
    """sub_script 子上下文跑完后，把 run 进度恢复成父节点（避免 UI 高亮到子步骤）。"""
    parent = ctx.parent
    if parent is None or parent.current_step_id is None:
        return
    _update_run_progress(ctx.run_id, parent.current_step_id, parent.step_index)


def _consume_run_from(
    ctx: RunContext,
    index_by_id: dict[str, int],
    retry_used: dict[str, int],
    steps: list[Any],
) -> int | None:
    """消费 pending run_from；返回目标 index（同时重置目标及其后的 retry 计数）。"""
    if ctx.controls is None:
        return None
    step_id = controls.consume_run_from(ctx.run_id)
    if step_id is None:
        return None
    target_index = index_by_id.get(str(step_id))
    if target_index is None:
        _safe_emit(
            ctx.run_id,
            "log",
            {"level": "warn", "message": f"run_from target not found: {step_id}"},
        )
        return None
    reset_retry_used(retry_used, steps, target_index)
    return target_index


def _retry_policy(ctx: RunContext, step: Any) -> tuple[int, int]:
    retry = getattr(step, "retry", None)
    if retry is None:
        retry = ctx.script.parsed.defaults.retry or ctx.script.parsed.settings.retry
    if retry is None:
        return 0, 1000
    return max(0, int(retry.count)), max(0, int(retry.interval_ms))


def _step_timeout(ctx: RunContext, step: Any) -> int:
    raw = getattr(step, "timeout_ms", None)
    if raw is None:
        raw = ctx.script.parsed.settings.default_timeout_ms
    if raw is None:
        raw = ctx.limits.get("default_timeout_ms", 8000)
    low = int(ctx.limits.get("step_min_timeout_ms", 100) or 100)
    high = min(int(ctx.limits.get("step_max_timeout_ms", 600_000) or 600_000), 600_000)
    # 手势本身的时长是动作耗时（长按/滑动/拖拽），节点超时必须覆盖它；
    # 否则录制的 8s+ 长滑会被默认 8s 节点超时硬切成失败。
    duration = getattr(getattr(step, "params", None), "duration_ms", None)
    if isinstance(duration, int) and duration > 0:
        raw = max(int(raw), duration + 2000)
    return max(low, min(int(raw), high))


def _execute_step_with_timeout(
    ctx: RunContext,
    step: Any,
    timeout_ms: int,
    attempt: StepAttempt | None = None,
) -> dict[str, Any]:
    """在 daemon 线程里执行节点；主循环轮询节点超时与 stop 强切。

    超时/强切会先置位本次 attempt 的取消令牌，再给执行线程一个很短的收尾窗口：
    定位轮询/注入前检查会立刻放弃，避免"节点已判超时，迟到的点击才落到设备上"。
    in-flight 的系统调用（如 u2 click）无法从 Python 侧 kill，只能等它自然返回。
    """
    box: dict[str, Any] = {}
    done = threading.Event()
    token = attempt or StepAttempt()

    def _target() -> None:
        ctx.bind_attempt(token)
        try:
            box["result"] = executors.execute_step(ctx, step)
        except BaseException as exc:  # noqa: BLE001 - 原样交给主循环判定
            box["error"] = exc
        finally:
            ctx.unbind_attempt()
            done.set()

    worker = threading.Thread(
        target=_target,
        name=f"autotest-step-{ctx.run_id}-{getattr(step, 'id', '?')}",
        daemon=True,
    )
    worker.start()
    started = time.monotonic()
    paused_total = 0.0
    paused_since: float | None = None
    stop_since: float | None = None
    grace = ctx.stop_grace_s
    while True:
        if done.wait(0.02):
            break
        now = time.monotonic()
        control = ctx.controls
        paused = control is not None and not control.pause_event.is_set()
        if paused:
            if paused_since is None:
                paused_since = now
        elif paused_since is not None:
            paused_total += now - paused_since
            paused_since = None
        if ctx.should_stop():
            if stop_since is None:
                stop_since = now
            elif now - stop_since >= grace:
                ctx.cancel_step()
                done.wait(0.2)  # 给执行线程一个放弃轮询/注入的窗口
                raise RunStopped("stop requested; step force-cut")
        active = now - started - paused_total
        if paused_since is not None:
            active -= now - paused_since
        if active * 1000.0 >= timeout_ms:
            ctx.cancel_step()
            if not done.wait(0.2):
                # 执行线程还卡在设备调用里：拒绝重试，避免同一节点两个并发执行
                raise StepAbandoned(
                    f"step {getattr(step, 'id', '?')} timed out after {timeout_ms} ms "
                    "(worker still running; retry suppressed)"
                )
            raise StepTimeout(f"step {getattr(step, 'id', '?')} timed out after {timeout_ms} ms")
    error = box.get("error")
    if error is not None:
        raise error
    result = box.get("result")
    return result if isinstance(result, dict) else {}


def _error_from_exception(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, StepTimeout):
        return {"code": "STEP_TIMEOUT", "message": str(exc)}
    if isinstance(exc, executors.AssertionFailed):
        return {"code": "ASSERTION_FAILED", "message": str(exc)}
    if isinstance(exc, executors.SubScriptDepthExceeded):
        return {"code": "SUB_SCRIPT_DEPTH_EXCEEDED", "message": str(exc)}
    if isinstance(exc, executors.SubScriptFailed):
        return {"code": "SUB_SCRIPT_FAILED", "message": str(exc)}
    if isinstance(exc, executors.ShellCommandFailed):
        return {"code": "SHELL_EXIT_CODE", "message": str(exc)}
    if isinstance(exc, executors.TargetNotFound):
        return {"code": "TARGET_NOT_FOUND", "message": str(exc)}
    if isinstance(exc, NonRetryableError):
        return {"code": "STEP_ERROR", "message": str(exc)}
    if isinstance(exc, RunFailed):
        return dict(exc.error)
    return {"code": "STEP_ERROR", "message": f"{type(exc).__name__}: {exc}"}


def _output_from_exception(exc: BaseException) -> dict[str, Any] | None:
    output = getattr(exc, "output", None)
    return output if isinstance(output, dict) else None


# ---------------------------------------------------------------- DB / 事件
def _load_run_data(run_id: int) -> dict[str, Any] | None:
    with session_scope() as session:
        run = session.get(Run, int(run_id))
        if run is None:
            return None
        return {
            "id": int(run.id),
            "script_id": run.script_id,
            "script_name": run.script_name,
            "script_snapshot": run.script_snapshot,
            "device_serial": run.device_serial,
            "variables": dict(run.variables or {}),
            "debug": dict(run.debug or {}),
            "status": run.status,
        }


def _update_run(run_id: int, **fields: Any) -> None:
    try:
        with session_scope() as session:
            run = session.get(Run, int(run_id))
            if run is None:
                return
            for key, value in fields.items():
                setattr(run, key, value)
    except Exception:  # 状态落库失败不能打断运行
        logger.exception("failed to update run %s", run_id)


def _update_run_progress(run_id: int, step_id: str, step_index: int) -> None:
    _update_run(run_id, current_step_id=step_id, step_index=int(step_index))


def _safe_emit(run_id: int, event_type: str, payload: dict[str, Any]) -> None:
    try:
        RunEventBus.emit(run_id, event_type, payload)
    except Exception:
        logger.exception("failed to emit %s for run %s", event_type, run_id)


def _record_attempt(
    ctx: RunContext,
    step: Any,
    index: int,
    status: str,
    attempts: int,
    started_iso: str,
    duration_ms: int,
    error: dict[str, Any] | None,
    output: dict[str, Any] | None,
    *,
    when_info: dict[str, Any] | None = None,
) -> int:
    if when_info is not None:
        merged = dict(output or {})
        merged["when"] = when_info
        output = merged
    step_result_id = 0
    try:
        with session_scope() as session:
            row = StepResult(
                run_id=ctx.run_id,
                step_id=step.id,
                step_name=step.name,
                step_type=step.type,
                step_index=int(index),
                status=status,
                attempts=int(attempts),
                started_at=started_iso,
                ended_at=now_iso(),
                duration_ms=int(duration_ms),
                error=error,
                output=output,
            )
            session.add(row)
            session.flush()
            step_result_id = int(row.id)
    except Exception:
        logger.exception("failed to record step result run=%s step=%s", ctx.run_id, step.id)

    if status == "failed" and _should_screenshot(ctx, step):
        try:
            ctx.save_screenshot(f"{step.id}-fail", step_result_id or None)
        except Exception:
            logger.exception("failed to save failure screenshot run=%s step=%s", ctx.run_id, step.id)

    payload: dict[str, Any] = {
        "step_id": step.id,
        "status": status,
        "duration_ms": int(duration_ms),
        "attempts": int(attempts),
    }
    if error is not None:
        payload["error"] = error
    if output is not None:
        payload["output"] = output
    if when_info is not None:
        payload["when"] = when_info
    _safe_emit(ctx.run_id, "step_finished", payload)
    return step_result_id


def _record_skipped(
    ctx: RunContext,
    step: Any,
    index: int,
    reason: str,
    *,
    when_info: dict[str, Any] | None = None,
) -> None:
    error = {"code": reason.upper(), "message": reason}
    output: dict[str, Any] = {"reason": reason}
    if when_info is not None:
        output["when"] = when_info
    try:
        with session_scope() as session:
            session.add(
                StepResult(
                    run_id=ctx.run_id,
                    step_id=step.id,
                    step_name=step.name,
                    step_type=step.type,
                    step_index=int(index),
                    status="skipped",
                    attempts=0,
                    started_at=None,
                    ended_at=now_iso(),
                    duration_ms=0,
                    error=error,
                    output=output,
                )
            )
    except Exception:
        logger.exception("failed to record skipped step run=%s step=%s", ctx.run_id, step.id)
    payload: dict[str, Any] = {"step_id": step.id, "reason": reason}
    if when_info is not None:
        payload["when"] = when_info
    _safe_emit(ctx.run_id, "step_skipped", payload)


def _skip_remaining(
    ctx: RunContext,
    steps: list[Any],
    from_index: int,
    outcomes: dict[str, str],
) -> None:
    """on_fail=abort 时把剩余节点记为 skipped(after_fail)，UI/报告可完整展示。"""
    for pos in range(max(0, int(from_index)), len(steps)):
        step = steps[pos]
        reason = "disabled" if not bool(getattr(step, "enabled", True)) else "after_fail"
        _record_skipped(ctx, step, pos, reason)
        outcomes[step.id] = "skipped"


def _apply_outcomes(summary: dict[str, Any], outcomes: dict[str, str]) -> dict[str, Any]:
    summary["passed_steps"] = sum(1 for value in outcomes.values() if value == "passed")
    summary["failed_steps"] = sum(1 for value in outcomes.values() if value == "failed")
    summary["skipped_steps"] = sum(1 for value in outcomes.values() if value == "skipped")
    return summary


def _should_screenshot(ctx: RunContext, step: Any) -> bool:
    if not bool(getattr(step, "screenshot_on_fail", False)):
        return False
    return bool(ctx.script.parsed.settings.screenshot_on_fail)


# 让子脚本执行器可以拿到共享异常类型（测试/诊断用）
__all__ = [
    "BudgetExceeded",
    "TotalTimeout",
    "acquire",
    "active_run_ids",
    "get_driver",
    "is_active",
    "release",
    "run_script_with_context",
    "start_run",
    "wait_for_idle",
]
