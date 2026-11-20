"""原子动作封装：对 Driver 协议的薄封装 + 错误归一化（契约 §4/§9）。

- 所有函数同步执行、不感知录制（录制钩子由 API 层调用）；
- 非 ``ApiError`` 的底层异常统一归一化为 ``ApiError``：
  参数类（ValueError）-> ``INVALID_ARGUMENT``；其余（设备/通信失败）-> ``DEVICE_UNAVAILABLE``；
- 不做"自行发明"的降级输入策略：``input_text`` 失败直接抛错，由引擎记录并走 on_fail/retry。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..errors import ApiError
from .driver import resolve_key

Point = tuple[int, int]
_Execute = Callable[..., Any]


# ---------------------------------------------------------------- 内部工具
def _point(value: Sequence[int], what: str) -> Point:
    if len(value) != 2:
        raise ApiError("INVALID_ARGUMENT", f"{what} must be (x, y), got {value!r}", {"value": list(value)})
    x, y = int(value[0]), int(value[1])
    if x < 0 or y < 0:
        raise ApiError("INVALID_ARGUMENT", f"{what} coordinates must be >= 0", {"value": [x, y]})
    return x, y


def _execute(op: str, func: _Execute, *args: Any, **kwargs: Any) -> Any:
    """执行驱动动作并归一化异常。"""
    try:
        return func(*args, **kwargs)
    except ApiError:
        raise
    except ValueError as exc:
        raise ApiError("INVALID_ARGUMENT", f"{op}: {exc}", {"op": op}) from exc
    except Exception as exc:  # 设备/驱动通信失败
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            f"{op} failed: {exc}",
            {"op": op, "error": type(exc).__name__},
        ) from exc


# ---------------------------------------------------------------- 原子动作
def tap(driver: Any, x: int, y: int) -> None:
    """单击坐标。"""
    _execute("tap", driver.click, int(x), int(y))


def swipe(driver: Any, from_xy: Sequence[int], to_xy: Sequence[int], duration_ms: int = 300) -> None:
    """滑动。steps 由时长估算（≥5 步）——具体换算在驱动实现内（u2 1 step≈5ms）。"""
    x1, y1 = _point(from_xy, "swipe.from")
    x2, y2 = _point(to_xy, "swipe.to")
    _execute("swipe", driver.swipe, x1, y1, x2, y2, int(duration_ms))


def drag(driver: Any, from_xy: Sequence[int], to_xy: Sequence[int], duration_ms: int = 1000) -> None:
    """拖拽：驱动支持 ``drag`` 时用之，否则退化为慢速 swipe。"""
    x1, y1 = _point(from_xy, "drag.from")
    x2, y2 = _point(to_xy, "drag.to")
    drag_fn = getattr(driver, "drag", None)
    if callable(drag_fn):
        _execute("drag", drag_fn, x1, y1, x2, y2, int(duration_ms))
    else:
        _execute("drag", driver.swipe, x1, y1, x2, y2, int(duration_ms))


def long_press(driver: Any, x: int, y: int, duration_ms: int = 800) -> None:
    """长按坐标。"""
    _execute("long_press", driver.long_press, int(x), int(y), int(duration_ms))


def press_key(driver: Any, name_or_code: str | int) -> None:
    """按键名或 keycode（用 driver.KEY_CODES / resolve_key 归一化）。"""
    try:
        code = resolve_key(name_or_code)
    except ValueError as exc:
        raise ApiError("INVALID_ARGUMENT", str(exc), {"key": name_or_code}) from exc
    _execute("press_key", driver.key, code)


def input_text(driver: Any, text: str) -> None:
    """向当前焦点输入框输入文本；失败抛错（不静默降级）。"""
    _execute("input_text", driver.input_text, str(text))


def clear_and_input(driver: Any, text: str) -> None:
    """先清空当前输入框再输入。"""
    _execute("clear_text", driver.clear_text)
    _execute("input_text", driver.input_text, str(text))
