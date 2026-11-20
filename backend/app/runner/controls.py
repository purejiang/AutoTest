"""运行控制状态机（契约 §9/§14.4）：pause / resume / stop / debug(step|continue|run_from)。

线程模型：
- 引擎线程、节点执行线程、API 线程共享 `Control`；
- `pause_event`：set=running，clear=paused；`stop_event`：set=已请求停止；
- `pause_wait_active` 保证「进入暂停」只被第一个观察者宣布一次（避免重复 run_paused 事件）；
- resume 严格校验：只有 paused 可 resume，只支持 hot，返回 warnings 列表。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from ..errors import ApiError

_DEBUG_ACTIONS = ("step", "continue", "run_from")


class Control:
    """单个 run 的控制块。"""

    def __init__(self, run_id: int, debug: dict[str, Any] | None = None) -> None:
        self.run_id = int(run_id)
        self.debug: dict[str, Any] = dict(debug or {})
        self.lock = threading.RLock()
        self.pause_event = threading.Event()
        self.pause_event.set()  # set = 未被暂停
        self.stop_event = threading.Event()
        self.pause_wait_active = False
        self.pause_reason = "user"
        self.paused_at_step_id: str | None = None
        self.current_step_id: str | None = None
        self.pending_run_from: str | None = None
        self.resume_warnings: list[str] = []
        self.created_at = time.time()


_TABLE: dict[int, Control] = {}
_TABLE_LOCK = threading.RLock()


# ---------------------------------------------------------------- 生命周期
def create_control(run_id: int, debug: dict[str, Any] | None = None) -> Control:
    with _TABLE_LOCK:
        control = _TABLE.get(int(run_id))
        if control is not None:
            if debug is not None:
                control.debug = dict(debug)
            return control
        control = Control(run_id, debug)
        _TABLE[int(run_id)] = control
        return control


def get_control(run_id: int) -> Control | None:
    with _TABLE_LOCK:
        return _TABLE.get(int(run_id))


def has_control(run_id: int) -> bool:
    return get_control(run_id) is not None


def cleanup(run_id: int) -> None:
    with _TABLE_LOCK:
        _TABLE.pop(int(run_id), None)


def cleanup_all() -> None:
    with _TABLE_LOCK:
        _TABLE.clear()


def active_run_ids() -> list[int]:
    with _TABLE_LOCK:
        return list(_TABLE)


# ---------------------------------------------------------------- 状态查询
def is_paused(run_id: int) -> bool:
    control = get_control(run_id)
    if control is None:
        return False
    return not control.pause_event.is_set()


def is_stopping(run_id: int) -> bool:
    control = get_control(run_id)
    return control is not None and control.stop_event.is_set()


def run_control_state(run_id: int) -> dict[str, Any] | None:
    control = get_control(run_id)
    if control is None:
        return None
    with control.lock:
        paused = not control.pause_event.is_set()
        stopping = control.stop_event.is_set()
        status = "stopping" if stopping else ("paused" if paused else "running")
        return {
            "run_id": control.run_id,
            "status": status,
            "paused": paused,
            "pause_reason": control.pause_reason if paused else None,
            "paused_at_step_id": control.paused_at_step_id if paused else None,
            "current_step_id": control.current_step_id,
            "stop_requested": stopping,
            "step_mode": bool(control.debug.get("step_mode")),
            "breakpoints": list(control.debug.get("breakpoints") or []),
            "pending_run_from": control.pending_run_from,
            "resume_warnings": list(control.resume_warnings),
        }


# ---------------------------------------------------------------- 请求动作
def request_pause(run_id: int, reason: str = "user", at_step_id: str | None = None) -> dict[str, Any] | None:
    """请求暂停；引擎在下一个安全点（节点边界 / 等待分片内）阻塞。"""
    control = get_control(run_id)
    if control is None:
        return None
    with control.lock:
        if control.stop_event.is_set():
            raise ApiError("CONFLICT", "run is stopping", {"run_id": int(run_id)})
        if control.pause_event.is_set():
            control.pause_event.clear()
            control.pause_reason = reason
            control.paused_at_step_id = at_step_id or control.current_step_id
            control.pause_wait_active = False
    return run_control_state(run_id)


def request_resume(run_id: int) -> dict[str, Any] | None:
    """恢复运行：仅 paused 可 resume，只支持 hot；warnings 随状态返回。"""
    control = get_control(run_id)
    if control is None:
        return None
    with control.lock:
        if control.stop_event.is_set():
            raise ApiError("CONFLICT", "run is stopping", {"run_id": int(run_id)})
        if control.pause_event.is_set():
            raise ApiError("CONFLICT", "run is not paused", {"run_id": int(run_id)})
        warnings: list[str] = []
        control.resume_warnings = warnings
        control.pause_reason = "user"
        control.pause_event.set()
    return run_control_state(run_id)


def request_stop(run_id: int) -> dict[str, Any] | None:
    """请求停止：置 stop_event 并唤醒暂停等待；引擎 ≤5s 内强切当前节点。"""
    control = get_control(run_id)
    if control is None:
        return None
    with control.lock:
        control.stop_event.set()
        control.pause_event.set()
    return run_control_state(run_id)


def request_debug(run_id: int, action: str, step_id: str | None = None) -> dict[str, Any] | None:
    """调试动作：step（放行一个节点）/ continue（退出单步模式）/ run_from（跳转到指定节点）。"""
    if action not in _DEBUG_ACTIONS:
        raise ApiError("INVALID_ARGUMENT", f"unknown debug action: {action}", {"action": action})
    control = get_control(run_id)
    if control is None:
        return None
    with control.lock:
        if control.stop_event.is_set():
            raise ApiError("CONFLICT", "run is stopping", {"run_id": int(run_id)})
        if action == "step":
            control.debug["step_mode"] = True
            control.pause_event.set()
        elif action == "continue":
            control.debug["step_mode"] = False
            control.pause_event.set()
        else:
            if not step_id:
                raise ApiError("INVALID_ARGUMENT", "run_from requires step_id", {})
            control.pending_run_from = str(step_id)
            control.pause_event.set()
    return run_control_state(run_id)


# ---------------------------------------------------------------- 引擎内部使用
def consume_run_from(run_id: int) -> str | None:
    control = get_control(run_id)
    if control is None:
        return None
    with control.lock:
        step_id = control.pending_run_from
        control.pending_run_from = None
        return step_id


def begin_pause_wait(control: Control) -> bool:
    """第一个观察到 paused 的线程返回 True（由它负责发 run_paused / 写 DB）。"""
    with control.lock:
        if control.pause_wait_active:
            return False
        control.pause_wait_active = True
        control.paused_at_step_id = control.paused_at_step_id or control.current_step_id
        return True


def end_pause_wait(control: Control) -> bool:
    with control.lock:
        if not control.pause_wait_active:
            return False
        control.pause_wait_active = False
        control.paused_at_step_id = None
        return True
