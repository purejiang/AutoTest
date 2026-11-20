"""录制挂钩（契约 §8）。

职责：给"手动控制端点"提供 zero-cost 的录制留痕点，避免 api 层直接依赖录制实现。
- 无活跃录制时所有函数立即返回（`on_before_pointer` 返回 None），不产生任何 adb 开销；
- `on_before_pointer` 在注入之前同步抓层级（点击归因准确性来自这里）；
- 滑动/拖拽等手势由 session 内部做环缓冲补偿，api 层只调用 `on_action`。
"""

from __future__ import annotations

import threading
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RecorderSessionLike(Protocol):
    recording_id: str

    def before_pointer(self) -> dict[str, Any] | None:
        """注入前同步抓一次层级，返回精简树 dict（失败返回 None）。"""

    def handle(self, kind: str, payload: dict[str, Any], pre: dict[str, Any] | None) -> None:
        """记录一次已执行的注入动作。kind ∈ tap|long_press|swipe|drag|input|key|app。"""

    def note_manual(self, kind: str, payload: dict[str, Any]) -> None:
        """非注入类留痕（运行暂停期间的手动操作）。"""


_LOCK = threading.RLock()
_ACTIVE: dict[str, RecorderSessionLike] = {}


def register(serial: str, session: RecorderSessionLike) -> None:
    with _LOCK:
        _ACTIVE[serial] = session


def unregister(serial: str, recording_id: str | None = None) -> None:
    with _LOCK:
        current = _ACTIVE.get(serial)
        if current is None:
            return
        if recording_id and getattr(current, "recording_id", None) != recording_id:
            return
        _ACTIVE.pop(serial, None)


def active(serial: str) -> RecorderSessionLike | None:
    with _LOCK:
        return _ACTIVE.get(serial)


def is_recording(serial: str) -> bool:
    return active(serial) is not None


def active_serials() -> list[str]:
    with _LOCK:
        return list(_ACTIVE)


def on_before_pointer(serial: str) -> dict[str, Any] | None:
    session = active(serial)
    if session is None:
        return None
    try:
        return session.before_pointer()
    except Exception:  # 录制不能影响控制
        return None


def on_action(
    serial: str,
    kind: str,
    payload: dict[str, Any],
    pre: dict[str, Any] | None = None,
) -> None:
    session = active(serial)
    if session is None:
        return
    try:
        session.handle(kind, payload, pre)
    except Exception:
        pass


def note_manual(serial: str, kind: str, payload: dict[str, Any]) -> None:
    session = active(serial)
    if session is None:
        return
    try:
        session.note_manual(kind, payload)
    except Exception:
        pass
