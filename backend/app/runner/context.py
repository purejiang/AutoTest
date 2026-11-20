"""运行上下文（契约 §3/§5/§9）。

RunContext 由引擎为一次运行创建；`sub_script` 通过 `child()` 派生共享同一 driver / 事件总线 /
三环度量的子上下文。所有方法在引擎线程或节点执行线程调用，需线程安全。
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .. import config
from ..db import session_scope
from ..events import RunEventBus
from ..logs import logger
from ..models import Artifact
from ..schemas import ScriptData

if TYPE_CHECKING:
    from ..android.driver import Driver
    from .controls import Control

MAX_SUB_DEPTH = 5

_VAR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.\-]+")


# ---------------------------------------------------------------- 异常
class RunStopped(Exception):
    """stop 请求触发：引擎结束运行，最终状态 stopped。"""


class StepTimeout(Exception):
    """节点超出 timeout_ms。"""


class NonRetryableError(Exception):
    """不参与 retry 的错误（断言失败、脚本/配置错误）。"""


class RunFailed(Exception):
    """运行级失败（on_fail=abort 或保护环触发）。"""

    def __init__(
        self,
        code: str,
        message: str,
        step_id: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.step_id = step_id
        self.error: dict[str, Any] = {"code": code, "message": message}
        if details is not None:
            self.error["details"] = details


class BudgetExceeded(RunFailed):
    def __init__(self, max_steps: int) -> None:
        super().__init__("MAX_STEPS_EXCEEDED", f"step budget exceeded (max_steps={max_steps})")


class TotalTimeout(RunFailed):
    def __init__(self, max_total_ms: int) -> None:
        super().__init__("TOTAL_TIMEOUT", f"run exceeded max total active time ({max_total_ms} ms)")


# ---------------------------------------------------------------- 脚本快照
@dataclass
class RunSnapshot:
    """运行期脚本快照：原始 data + 解析后的 ScriptData。"""

    raw: dict[str, Any]
    parsed: ScriptData
    script_id: str | None = None

    @classmethod
    def from_data(cls, data: dict[str, Any], script_id: str | None = None) -> RunSnapshot:
        parsed = ScriptData.model_validate(data)
        return cls(raw=dict(data), parsed=parsed, script_id=script_id)

    @property
    def name(self) -> str:
        return self.parsed.name

    @property
    def variables(self) -> dict[str, str]:
        return dict(self.parsed.variables)

    @property
    def settings(self) -> Any:
        return self.parsed.settings

    @property
    def steps(self) -> list[Any]:
        return list(self.parsed.steps)


def default_limits() -> dict[str, Any]:
    """从三级配置取三环参数；引擎启动时冻结一份传给 RunContext。"""
    grace = config.get("engine.pause_abandon_grace_s", 5)
    return {
        "max_total_ms": int(config.get("engine.max_total_ms", 1800_000) or 0),
        "max_steps": int(config.get("engine.max_steps", 2000) or 0),
        "poll_interval_ms": int(config.get("engine.poll_interval_ms", 350) or 350),
        "default_timeout_ms": int(config.get("engine.default_timeout_ms", 8000) or 8000),
        "step_min_timeout_ms": int(config.get("engine.step_min_timeout_ms", 100) or 100),
        "step_max_timeout_ms": min(int(config.get("engine.step_max_timeout_ms", 600_000) or 600_000), 600_000),
        "stop_grace_s": 5.0 if grace is None else float(grace),
    }


class _SharedState:
    """root 上下文持有；子脚本上下文共享活跃时长/步骤预算/截图序号。"""

    def __init__(self, limits: dict[str, Any]) -> None:
        self.limits: dict[str, Any] = dict(limits)
        self.lock = threading.Lock()
        self.budget_used = 0
        self.started = time.monotonic()
        self.paused_total = 0.0
        self.paused_since: float | None = None
        self.screenshot_seq = 0


class RunContext:
    """一次运行的执行上下文。"""

    def __init__(
        self,
        run_id: int,
        driver: Driver,
        script: RunSnapshot | dict[str, Any],
        variables: dict[str, str] | None = None,
        depth: int = 0,
        controls: Control | None = None,
        limits: dict[str, Any] | None = None,
        parent: RunContext | None = None,
    ) -> None:
        self.run_id = int(run_id)
        self.driver = driver
        if isinstance(script, RunSnapshot):
            self.script = script
        else:
            self.script = RunSnapshot.from_data(script)
        self.depth = int(depth)
        self.controls = controls
        self.parent = parent
        if parent is not None:
            self._shared = parent._shared
            self.debug = parent.debug
            self.on_pause = parent.on_pause
        else:
            self._shared = _SharedState(limits or default_limits())
            self.debug = {}
            self.on_pause: Callable[[bool], None] | None = None
        self.step_index = 0
        self.current_step_id: str | None = None
        self.step_timeout_ms: int | None = None
        self._step_deadline: float | None = None

        merged: dict[str, str] = {}
        if parent is not None:
            merged.update(parent.variables)
        merged.update({str(k): v for k, v in (self.script.parsed.variables or {}).items()})
        merged.update({str(k): v for k, v in (variables or {}).items()})
        self.variables: dict[str, str] = merged

    # ------------------------------------------------------------ 基本信息
    @property
    def limits(self) -> dict[str, Any]:
        return self._shared.limits

    @property
    def poll_interval_ms(self) -> int:
        return int(self.limits.get("poll_interval_ms", 350))

    @property
    def stop_grace_s(self) -> float:
        return float(self.limits.get("stop_grace_s", 5.0))

    def child(self, script: RunSnapshot, variables: dict[str, str] | None = None) -> RunContext:
        """派生 sub_script 子上下文（共享 driver/控制表/三环度量）。"""
        child = RunContext(
            run_id=self.run_id,
            driver=self.driver,
            script=script,
            variables=variables,
            depth=self.depth + 1,
            controls=self.controls,
            limits=self.limits,
            parent=self,
        )
        child.on_pause = self.on_pause
        child.debug = self.debug
        return child

    # ------------------------------------------------------------ 事件
    def emit(self, type: str, payload: dict[str, Any] | None = None) -> int:
        return RunEventBus.emit(self.run_id, type, payload or {})

    # ------------------------------------------------------------ 停止 / 暂停
    def should_stop(self) -> bool:
        return self.controls is not None and self.controls.stop_event.is_set()

    def step_expired(self) -> bool:
        return self._step_deadline is not None and time.monotonic() >= self._step_deadline

    def abort_requested(self) -> bool:
        """传给 selectors 的 should_abort：stop 或节点超时都尽快打断轮询。"""
        return self.should_stop() or self.step_expired()

    def begin_step(self, timeout_ms: int) -> None:
        self.step_timeout_ms = int(timeout_ms)
        self._step_deadline = time.monotonic() + max(0, int(timeout_ms)) / 1000.0

    def end_step(self) -> None:
        self.step_timeout_ms = None
        self._step_deadline = None

    def wait_if_paused(self) -> None:
        """暂停在引擎循环内阻塞（250ms 轮询以响应 stop）；由第一个观察者宣布进入/离开。"""
        control = self.controls
        if control is None or control.pause_event.is_set():
            return
        from .controls import begin_pause_wait, end_pause_wait

        if begin_pause_wait(control):
            self.mark_pause_started()
            self._notify_pause(True)
        try:
            while not control.pause_event.is_set():
                if control.stop_event.is_set():
                    raise RunStopped("stop requested while paused")
                control.pause_event.wait(0.25)
            if control.stop_event.is_set():
                raise RunStopped("stop requested while paused")
        finally:
            if end_pause_wait(control):
                self.mark_pause_ended()
                self._notify_pause(False)

    def _notify_pause(self, entered: bool) -> None:
        callback = self.on_pause
        if callback is None:
            return
        try:
            callback(entered)
        except Exception:  # 暂停回调不能打断运行
            logger.exception("pause listener failed run=%s", self.run_id)

    def sleep_interruptible(self, ms: int) -> None:
        """分片 ≤200ms 的可打断 sleep：stop 立即抛 RunStopped，pause 期间阻塞。"""
        remaining = max(0, int(ms or 0))
        if remaining <= 0:
            if self.should_stop():
                raise RunStopped("stop requested")
            return
        while remaining > 0:
            if self.should_stop():
                raise RunStopped("stop requested")
            self.wait_if_paused()
            chunk = min(remaining, 200)
            if self.controls is not None:
                self.controls.stop_event.wait(chunk / 1000.0)
            else:
                time.sleep(chunk / 1000.0)
            remaining -= chunk
            if self.should_stop():
                raise RunStopped("stop requested")

    # ------------------------------------------------------------ 三环度量
    def mark_pause_started(self) -> None:
        shared = self._shared
        with shared.lock:
            if shared.paused_since is None:
                shared.paused_since = time.monotonic()

    def mark_pause_ended(self) -> None:
        shared = self._shared
        with shared.lock:
            if shared.paused_since is not None:
                shared.paused_total += time.monotonic() - shared.paused_since
                shared.paused_since = None

    def active_elapsed_ms(self) -> int:
        """活跃时长（paused 不计入）。"""
        shared = self._shared
        with shared.lock:
            now = time.monotonic()
            elapsed = now - shared.started - shared.paused_total
            if shared.paused_since is not None:
                elapsed -= now - shared.paused_since
        return max(0, int(elapsed * 1000))

    def check_step_budget(self) -> int:
        """记录一次节点尝试（含 retry/goto 重跳）；超预算抛 BudgetExceeded。"""
        shared = self._shared
        with shared.lock:
            shared.budget_used += 1
            used = shared.budget_used
        max_steps = int(shared.limits.get("max_steps", 0) or 0)
        if max_steps > 0 and used > max_steps:
            raise BudgetExceeded(max_steps)
        return used

    def check_total_timeout(self) -> None:
        max_total_ms = int(self.limits.get("max_total_ms", 0) or 0)
        if max_total_ms > 0 and self.active_elapsed_ms() > max_total_ms:
            raise TotalTimeout(max_total_ms)

    # ------------------------------------------------------------ 变量插值
    def interpolate(self, text: Any) -> Any:
        """`{{var}}` 替换；未知变量保留原文并 emit log warn。"""
        if not isinstance(text, str):
            return text

        def _replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name in self.variables:
                return str(self.variables[name])
            self.emit(
                "log",
                {"level": "warn", "message": f"unknown variable: {name} (kept {match.group(0)})"},
            )
            return match.group(0)

        return _VAR_RE.sub(_replace, text)

    def interpolate_target(self, target: Any) -> Any:
        """插值 Target 的字符串字段（point 不动；pydantic / dict 都支持）。"""
        if target is None:
            return None
        if hasattr(target, "model_dump"):
            data = target.model_dump()
            if data.get("by") == "point":
                return target
            if data.get("value") is not None:
                return target.model_copy(update={"value": self.interpolate(data["value"])})
            return target
        if isinstance(target, dict):
            data = dict(target)
            if data.get("by") != "point" and data.get("value") is not None:
                data["value"] = self.interpolate(data["value"])
            return data
        return target

    # ------------------------------------------------------------ 截图产物
    def save_screenshot(self, name: str, step_result_id: int | None = None) -> dict[str, Any]:
        """driver.screenshot() → artifacts/run_<id>/xxx.png + artifacts 行 + screenshot 事件。"""
        safe = _SAFE_NAME_RE.sub("_", str(name or "screenshot")).strip("_")[:80] or "screenshot"
        shared = self._shared
        with shared.lock:
            shared.screenshot_seq += 1
            shot_seq = shared.screenshot_seq
        run_dir = config.artifacts_dir() / f"run_{self.run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{int(time.time() * 1000)}_{shot_seq}_{safe}.png"
        path = run_dir / filename

        raw = self.driver.screenshot()
        if not isinstance(raw, (bytes, bytearray)) or not bytes(raw).startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("invalid screenshot data returned by driver")
        data = bytes(raw)
        path.write_bytes(data)

        artifact_id: int | None = None
        try:
            with session_scope() as session:
                artifact = Artifact(
                    run_id=self.run_id,
                    step_result_id=step_result_id,
                    kind="screenshot",
                    name=safe,
                    path=str(path),
                    size=len(data),
                    meta={"step_id": self.current_step_id},
                )
                session.add(artifact)
                session.flush()
                artifact_id = artifact.id
        except Exception:  # 产物落库失败不能打断运行
            logger.exception("failed to record artifact for run=%s path=%s", self.run_id, path)

        self.emit(
            "screenshot",
            {"artifact_id": artifact_id, "step_id": self.current_step_id, "name": safe},
        )
        return {"artifact_id": artifact_id, "name": safe, "path": str(path), "size": len(data)}
