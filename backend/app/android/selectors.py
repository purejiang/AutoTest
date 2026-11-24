"""Target -> 节点/坐标（契约 §3、§14.1）。

匹配优先级（拿到一棵层级树后）：
1. ``by="point"``：直接返回坐标，不查树；
2. 先按 ``target.by`` 显式指定的方式匹配（尊重契约里 by 的本意）；
3. 显式方式无命中时，按 ``text > text_contains > resource_id > content_desc > class_name > xpath``
   顺序降级兜底（录制/手写脚本里 by 与实际属性不符时仍能定位）；
4. 取命中列表的 ``target.index`` 项（0 基），越界 -> ``TargetNotFound``。

轮询等待统一可打断：每次 dump 之间 sleep 分片（≤100ms），``should_abort()`` 为真立即退出。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ..errors import ApiError
from ..logs import logger
from ..schemas import Target
from .driver import HierarchyNode
from .hierarchy import flatten

# 降级兜底顺序（显式 by 无命中时才用到）
_FALLBACK_ORDER: tuple[str, ...] = (
    "text",
    "text_contains",
    "resource_id",
    "content_desc",
    "class_name",
    "xpath",
)

_CLASS_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*(\.[A-Za-z_$][A-Za-z0-9_$]*)*$")
_XPATH_PRED_RE = re.compile(r"^@(?P<attr>[A-Za-z_-]+)\s*=\s*(?P<quote>['\"])(?P<value>[^'\"]*)(?P=quote)$")

_XPATH_ATTRS = {
    "resource-id": "resource_id",
    "resource_id": "resource_id",
    "text": "text",
    "content-desc": "content_desc",
    "content_description": "content_desc",
    "class": "class_name",
    "package": "package",
}

AbortFn = Callable[[], bool] | None


# ---------------------------------------------------------------- 数据类/异常
@dataclass
class ResolvedTarget:
    x: int
    y: int
    node: HierarchyNode | None  # point 定位时为 None
    bounds: list[int]


class TargetNotFound(Exception):
    """目标未找到（``str(e)`` 可直接展示给用户）。"""


# ---------------------------------------------------------------- 参数归一化
def _field(target: Any, name: str, default: Any = None) -> Any:
    """兼容 pydantic ``Target`` 与普通 dict（调用方偶有传 dict）。"""
    if isinstance(target, dict):
        return target.get(name, default)
    return getattr(target, name, default)


def describe_target(target: Any) -> str:
    by = str(_field(target, "by", "") or "")
    if by == "point":
        return f"point({_field(target, 'x')},{_field(target, 'y')})"
    index = _field(target, "index", 0)
    return f"{by}={_field(target, 'value')!r}, index={index}"


# ---------------------------------------------------------------- 单类型匹配
def _class_matches(node: HierarchyNode, value: str) -> bool:
    class_name = node.class_name or ""
    if not value:
        return False
    if class_name == value:
        return True
    # 简名后缀匹配：Button -> android.widget.Button
    return class_name.endswith("." + value)


def _match_by(nodes: Sequence[HierarchyNode], by: str, value: str) -> list[HierarchyNode]:
    if by == "text":
        wanted = value.strip()
        return [node for node in nodes if (node.text or "").strip() == wanted]
    if by == "text_contains":
        wanted = value.strip().lower()
        if not wanted:
            return []
        return [node for node in nodes if wanted in (node.text or "").lower()]
    if by == "resource_id":
        wanted = value.strip()
        return [node for node in nodes if (node.resource_id or "").strip() == wanted]
    if by == "content_desc":
        wanted = value.strip()
        return [node for node in nodes if (node.content_desc or "").strip() == wanted]
    if by == "class_name":
        wanted = value.strip()
        return [node for node in nodes if _class_matches(node, wanted)]
    if by == "xpath":
        return _xpath_matches(nodes, value)
    raise ApiError("INVALID_ARGUMENT", f"unsupported target by={by!r}", {"by": by})


# ---------------------------------------------------------------- 精简 xpath
def _xpath_matches(nodes: Sequence[HierarchyNode], expression: str) -> list[HierarchyNode]:
    """精简 xpath 子集：``//Class``、``//*``、``//*[@attr='v']``、``//Class[N]``。

    仅支持单步、以 ``//`` 开头、一个可选谓词；其他形式抛 ``ApiError("UNSUPPORTED")``
    （不静默返回空，避免把脚本错误当成"元素还没出现"而白等超时）。
    """
    expr = (expression or "").strip()
    if not expr.startswith("//"):
        raise ApiError("UNSUPPORTED", f"unsupported xpath (must start with //): {expression!r}")
    body = expr[2:].strip()
    if not body:
        raise ApiError("UNSUPPORTED", f"unsupported xpath (empty expression): {expression!r}")

    tag = body
    predicate: str | None = None
    bracket = body.find("[")
    if bracket >= 0:
        if not body.endswith("]"):
            raise ApiError("UNSUPPORTED", f"unsupported xpath (unclosed predicate): {expression!r}")
        tag = body[:bracket].strip()
        predicate = body[bracket + 1 : -1].strip()
        if not predicate or "[" in predicate or "]" in predicate:
            raise ApiError("UNSUPPORTED", f"unsupported xpath predicate: {expression!r}")
    if "/" in tag:  # 多级路径不支持（谓词里的 '/' 如 resource-id 不受影响）
        raise ApiError("UNSUPPORTED", f"unsupported xpath (multi-step not supported): {expression!r}")
    if not tag or (tag != "*" and not _CLASS_RE.match(tag)):
        raise ApiError("UNSUPPORTED", f"unsupported xpath tag: {expression!r}")

    matched: list[HierarchyNode] = list(nodes) if tag == "*" else [n for n in nodes if _class_matches(n, tag)]

    if predicate is None:
        return matched
    if predicate.isdigit():
        position = int(predicate)
        if position < 1:
            raise ApiError("UNSUPPORTED", f"unsupported xpath index (1-based): {expression!r}")
        return [matched[position - 1]] if position <= len(matched) else []

    parsed = _XPATH_PRED_RE.match(predicate)
    if not parsed:
        raise ApiError("UNSUPPORTED", f"unsupported xpath predicate: {expression!r}")
    attr = parsed.group("attr")
    field_name = _XPATH_ATTRS.get(attr)
    if field_name is None:
        raise ApiError("UNSUPPORTED", f"unsupported xpath attribute @{attr}")
    wanted = parsed.group("value")
    return [node for node in matched if (getattr(node, field_name, "") or "") == wanted]


# ---------------------------------------------------------------- 匹配入口
def visible_nodes(root: HierarchyNode) -> list[HierarchyNode]:
    """展开树并过滤 ``visible=False`` 的节点（uiautomator ``visible-to-user``）。"""
    return [node for node in flatten(root) if node.visible]


def match_nodes(root: HierarchyNode, target: Any) -> list[HierarchyNode]:
    """返回与 target 匹配的节点列表（文档顺序，未应用 ``index``）。

    ``point`` 不查树，返回 ``[]``；``xpath`` 语法不支持时抛 ``ApiError("UNSUPPORTED")``。
    """
    by = str(_field(target, "by", "") or "")
    if by == "point":
        return []
    value = str(_field(target, "value") or "")
    nodes = visible_nodes(root)

    explicit = _match_by(nodes, by, value)
    if explicit:
        return explicit
    for fallback in _FALLBACK_ORDER:
        # xpath 只有显式指定时才解释 value（否则任意文本都会被当成非法 xpath 抛 UNSUPPORTED）
        if fallback == by or fallback == "xpath":
            continue
        matches = _match_by(nodes, fallback, value)
        if matches:
            return matches
    return []


def select_node(root: HierarchyNode, target: Any) -> HierarchyNode | None:
    """应用 ``index`` 选出唯一节点；无任何命中返回 None；命中但 index 越界抛 TargetNotFound。"""
    candidates = match_nodes(root, target)
    if not candidates:
        return None
    try:
        index = int(_field(target, "index", 0) or 0)
    except (TypeError, ValueError):
        index = 0
    if index < 0:
        index = 0
    if index >= len(candidates):
        raise TargetNotFound(
            f"目标 index 越界: {describe_target(target)} (命中 {len(candidates)} 个)"
        )
    return candidates[index]


# ---------------------------------------------------------------- 树抓取（容错）
def _dump_tree(driver: Any) -> HierarchyNode | None:
    """dump 一次层级；瞬时设备/解析错误返回 None（轮询期间继续等）。"""
    try:
        return driver.dump_hierarchy()
    except ApiError as exc:
        logger.warning("dump_hierarchy failed while resolving target: %s", exc)
        return None
    except Exception as exc:  # 驱动实现异常也不该打断轮询
        logger.warning("dump_hierarchy raised %s: %s", type(exc).__name__, exc)
        return None


def _should_abort(should_abort: AbortFn) -> bool:
    return bool(should_abort and should_abort())


def _interruptible_sleep(seconds: float, should_abort: AbortFn) -> bool:
    """分片 sleep（每片 ≤100ms），可被 should_abort 打断；被中断返回 False。"""
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        if _should_abort(should_abort):
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(0.1, remaining))


# ---------------------------------------------------------------- 对外 API（§14.1）
def resolve_target(
    driver: Any,
    target: Target,
    timeout_ms: int = 0,
    poll_interval_ms: int = 350,
    should_abort: AbortFn = None,
) -> ResolvedTarget:
    """定位目标到坐标。

    - ``by=point``：立即返回 ``ResolvedTarget(x, y, None, [x,y,x,y])``；
    - 元素定位：轮询 dump 至 ``timeout_ms``（0 = 只查一次），坐标取 bounds 中心；
    - ``should_abort()`` 为真 -> ``TargetNotFound("aborted")``；
    - 超时 / 一直无命中 -> ``TargetNotFound``（消息含目标描述，可直接给用户看）。
    """
    by = str(_field(target, "by", "") or "")
    if by == "point":
        x = _field(target, "x")
        y = _field(target, "y")
        if x is None or y is None:
            raise ApiError("INVALID_ARGUMENT", "point target requires x and y", {"target": describe_target(target)})
        px, py = int(x), int(y)
        return ResolvedTarget(x=px, y=py, node=None, bounds=[px, py, px, py])

    timeout_s = max(0, int(timeout_ms)) / 1000.0
    interval_s = max(0, int(poll_interval_ms)) / 1000.0
    deadline = time.monotonic() + timeout_s
    while True:
        if _should_abort(should_abort):
            raise TargetNotFound("aborted")
        root = _dump_tree(driver)
        if root is not None:
            node = select_node(root, target)  # index 越界会直接抛 TargetNotFound
            if node is not None:
                x, y = node.center
                return ResolvedTarget(x=x, y=y, node=node, bounds=list(node.bounds))
        if timeout_ms <= 0 or time.monotonic() >= deadline:
            raise TargetNotFound(f"未找到目标: {describe_target(target)}")
        if not _interruptible_sleep(interval_s, should_abort):
            raise TargetNotFound("aborted")


def check_condition(driver: Any, target: Target, state: str = "visible") -> bool:
    """单次检查目标是否满足 ``state``（visible/gone）；不抛 TargetNotFound。

    - 瞬时 dump 失败 = **观测未知**：visible 与 gone 都返回 False（交给 wait_for 下一轮再试），
      绝不能把「拿不到层级」当成「元素已消失」，否则 wait(gone)/not_exists 会在设备抖动时误通过；
    - ``point`` 无法判断可见性：visible 恒 True / gone 恒 False。
    """
    if state not in ("visible", "gone"):
        raise ApiError("INVALID_ARGUMENT", f"unsupported condition state={state!r}", {"state": state})
    if str(_field(target, "by", "") or "") == "point":
        visible = True
    else:
        root = _dump_tree(driver)
        if root is None:
            return False
        try:
            visible = select_node(root, target) is not None
        except TargetNotFound:
            visible = False
    return visible if state == "visible" else not visible


def wait_for(
    driver: Any,
    target: Target,
    state: str,
    timeout_ms: int,
    poll_interval_ms: int = 350,
    should_abort: AbortFn = None,
) -> bool:
    """轮询等待目标达到 ``state``；满足返回 True，超时/被打断返回 False（不抛）。"""
    if state not in ("visible", "gone"):
        raise ApiError("INVALID_ARGUMENT", f"unsupported condition state={state!r}", {"state": state})
    timeout_s = max(0, int(timeout_ms)) / 1000.0
    interval_s = max(0, int(poll_interval_ms)) / 1000.0
    deadline = time.monotonic() + timeout_s
    while True:
        if _should_abort(should_abort):
            return False
        if check_condition(driver, target, state):
            return True
        if timeout_ms <= 0 or time.monotonic() >= deadline:
            return False
        if not _interruptible_sleep(interval_s, should_abort):
            return False
