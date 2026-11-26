"""录制事件转换：手势分类、点击归因、事件列表 -> 契约 §3 脚本。

职责边界（契约 §8 / §14.5）：
- `classify()` / `gesture_from_samples()`：把 hook kind 与物理触摸采样归并成脚本节点类型；
- `attribute()` / `attribute_with_confidence()`：用注入前的层级快照，把点击点归属到
  「包含该点的最小可点击节点」；找不到/层级缺失时退化为 point 定位并降低置信度；
- `events_to_script()`：把 `recording_events` 转成 `schemas.validate_script()` 可校验的脚本，
  保持 **一事件一节点**（不合并同点连续 tap），并返回面向用户的中文 warnings。

坐标来源说明：web 录制来自 API 注入参数；physical 录制来自 getevent 采样（已由
touchmap 映射为设备像素）。两者都直接使用设备像素坐标。
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from ..android.driver import KEY_CODES, HierarchyNode
from ..schemas import Target, validate_script

# ---------------------------------------------------------------- 常量（契约 §8 阈值）
TAP_MAX_MS = 500  # < 500ms 且位移 < 10px => tap；>= 500ms 且位移 < 10px => long_click
POINT_MAX_PX = 10  # 位移 < 10px 视为点按；>= 10px 视为滑动/拖拽
SWIPE_MAX_MS = 300  # 位移 >= 10px 且 < 300ms => swipe；>= 300ms => drag

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

# hook kind -> 脚本节点 type（web: long_press；physical 归并后可能直接产出 long_click）
_KIND_TO_STEP: dict[str, str] = {
    "tap": "click",
    "click": "click",
    "long_press": "long_click",
    "long_click": "long_click",
    "swipe": "swipe",
    "drag": "drag",
    "input": "input",
    "key": "key",
    "app": "app",
}

# 录制保真：app 启动/切换也生成 app 节点（否则回放不从同一界面开始）；
# 只跳过确实无法转换的 kind（见 _convert_event 返回 None 的分支）。
_SKIP_KINDS: set[str] = set()

# 回放时序：把录制事件之间的真实间隔还原成节点的 delay_before_ms，
# 否则回放会「瞬间连点」，而页面动画/网络都没跟上（用户反馈「运行和操作不一样」）。
# 步骤间隔还原上限：人类等加载/看界面可能是十几秒，不能截断成 3s（否则回放"太快"）
_STEP_DELAY_MAX_MS = 15_000
# 轨迹采样上限（与录制端一致）
_MAX_GESTURE_PATH = 240
_FIRST_STEP_DELAY_MS = 800

# rerun_safety：录制出来的手势/输入/按键都按 safe 处理（契约 §3）
_SAFE_TYPES = {"click", "long_click", "swipe", "drag", "input", "key"}

_KEYCODE_TO_NAME: dict[int, str] = {code: name for name, code in KEY_CODES.items()}

# 录制转脚本节点短名称：类型标签 + 同类型序号（点击1、点击2…）。
# 方向/画圈/包名/文本/坐标等信息继续放在 description 与 params 中。
_NODE_NAME_LABELS: dict[str, str] = {
    "click": "点击",
    "long_click": "长按",
    "swipe": "滑动",
    "drag": "拖拽",
    "input": "输入",
    "key": "按键",
    "app": "应用",
    "wait": "等待",
    "shell": "Shell",
    "assert": "断言",
    "sub_script": "子脚本",
    "screenshot": "截图",
}

_BOUNDS_RE = re.compile(r"-?\d+")


# ---------------------------------------------------------------- 手势分类
def classify(kind: str, payload: dict[str, Any] | None = None) -> str:
    """把录制事件 kind 映射为脚本节点 type；未知 kind 原样返回（由调用方决定跳过）。"""
    return _KIND_TO_STEP.get(str(kind or "").strip().lower(), str(kind or ""))


def gesture_from_samples(
    duration_ms: float,
    distance_px: float,
    path_extent_px: float | None = None,
) -> str:
    """按契约 §8 阈值把一次触摸手势归并为 tap / long_press / swipe / drag。

    ``distance_px`` 只是起点→终点的直线位移；画圆/画圈会回到起点附近，
    仅看它会把整段轨迹误判成点击。``path_extent_px`` 是轨迹上离起点最远的
    距离，二者取最大值后再判断“有没有产生位移”。
    """
    duration_ms = float(duration_ms or 0)
    extent = max(float(distance_px or 0), float(path_extent_px or 0))
    if extent < POINT_MAX_PX:
        return "tap" if duration_ms < TAP_MAX_MS else "long_press"
    if duration_ms < SWIPE_MAX_MS:
        return "swipe"
    return "drag"


# ---------------------------------------------------------------- 节点归属
def _normalize_bounds(raw: Any) -> list[int]:
    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
        try:
            return [int(float(raw[0])), int(float(raw[1])), int(float(raw[2])), int(float(raw[3]))]
        except (TypeError, ValueError):
            return [0, 0, 0, 0]
    if isinstance(raw, str):
        nums = [int(match) for match in _BOUNDS_RE.findall(raw)]
        if len(nums) >= 4:
            return nums[:4]
    return [0, 0, 0, 0]


def _node_from_dict(data: dict[str, Any]) -> HierarchyNode:
    children = data.get("children") or []
    return HierarchyNode(
        class_name=str(data.get("class_name", "") or ""),
        text=str(data.get("text", "") or ""),
        resource_id=str(data.get("resource_id", "") or ""),
        content_desc=str(data.get("content_desc", "") or ""),
        package=str(data.get("package", "") or ""),
        bounds=_normalize_bounds(data.get("bounds")),
        clickable=bool(data.get("clickable", False)),
        enabled=bool(data.get("enabled", True)),
        visible=bool(data.get("visible", True)),
        children=[_node_from_dict(child) for child in children if isinstance(child, dict)],
    )


def _tree_from_pre(pre: Any) -> HierarchyNode | None:
    """把 before_pointer() 返回的精简树还原成 HierarchyNode；无法解析返回 None。"""
    if pre is None:
        return None
    if isinstance(pre, HierarchyNode):
        return pre
    if isinstance(pre, dict):
        try:
            return _node_from_dict(pre)
        except Exception:  # pragma: no cover - 防御异常层级数据
            return None
    if isinstance(pre, (list, tuple)):
        # 兼容原始 dump：根节点可能是单元素列表
        for item in pre:
            if isinstance(item, dict):
                try:
                    return _node_from_dict(item)
                except Exception:  # pragma: no cover
                    return None
    return None


def _contains(bounds: Any, x: int, y: int) -> bool:
    x1, y1, x2, y2 = _normalize_bounds(bounds)
    return x1 <= x <= x2 and y1 <= y <= y2


def _smallest_clickable(root: HierarchyNode, x: int, y: int) -> HierarchyNode | None:
    """返回包含点击点的最小可点击节点（面积最小；同面积取层级更深的）。"""
    best: HierarchyNode | None = None
    best_area = float("inf")
    best_depth = -1

    def walk(node: HierarchyNode, depth: int) -> None:
        nonlocal best, best_area, best_depth
        if node.visible and node.enabled and node.clickable and _contains(node.bounds, x, y):
            x1, y1, x2, y2 = _normalize_bounds(node.bounds)
            area = max(0, x2 - x1) * max(0, y2 - y1)
            if area < best_area or (area == best_area and depth > best_depth):
                best, best_area, best_depth = node, area, depth
        for child in node.children:
            walk(child, depth + 1)

    walk(root, 0)
    return best


def _area(bounds: Any) -> int:
    x1, y1, x2, y2 = _normalize_bounds(bounds)
    return max(0, x2 - x1) * max(0, y2 - y1)


def _descendant_with_selector(root: HierarchyNode, x: int, y: int) -> HierarchyNode | None:
    """在 root 子树里找包含点击点、且带可用选择器（text/content_desc/resource_id）的最小节点。

    典型场景：MIUI/系统设置的列表行是「可点击的父容器 + 无点击属性的子 TextView」，
    父容器没有 text，直接取父容器只能退回坐标；向下找到子 TextView 才能得到 by=text 的稳定选择器。
    """
    best: HierarchyNode | None = None
    best_area = float("inf")

    def walk(node: HierarchyNode) -> None:
        nonlocal best, best_area
        if node is not root and node.visible and _contains(node.bounds, x, y):
            has_selector = bool(node.text or node.content_desc or node.resource_id)
            if has_selector:
                x1, y1, x2, y2 = _normalize_bounds(node.bounds)
                area = max(0, x2 - x1) * max(0, y2 - y1)
                if area < best_area:
                    best, best_area = node, area
        for child in node.children:
            walk(child)

    walk(root)
    return best


def _smallest_selector_node(root: HierarchyNode, x: int, y: int) -> HierarchyNode | None:
    """整棵树里包含点击点、带选择器的最小节点（不限 clickable，作为最后的文本兜底）。"""
    best: HierarchyNode | None = None
    best_area = float("inf")

    def walk(node: HierarchyNode) -> None:
        nonlocal best, best_area
        if node.visible and _contains(node.bounds, x, y):
            has_selector = bool(node.text or node.content_desc or node.resource_id)
            if has_selector:
                x1, y1, x2, y2 = _normalize_bounds(node.bounds)
                area = max(0, x2 - x1) * max(0, y2 - y1)
                if area < best_area:
                    best, best_area = node, area
        for child in node.children:
            walk(child)

    walk(root)
    return best


def node_target(node: HierarchyNode | None) -> dict[str, Any] | None:
    """节点 -> 契约 §3 Target；优先 text（可读性好），其次 content_desc / resource_id。"""
    if node is None:
        return None
    candidates = (
        ("text", node.text),
        ("content_desc", node.content_desc),
        ("resource_id", node.resource_id),
    )
    for by, value in candidates:
        if value:
            return {"by": by, "value": str(value), "index": 0}
    return None


def attribute_with_confidence(
    driver: Any,
    pre_tree: Any,
    x: Any,
    y: Any,
) -> tuple[dict[str, Any], str, str]:
    """点击点归因：返回 (Target, confidence, reason)。

    - 命中最小可点击节点且有可用属性 -> high；
    - 有层级但没找到可点击节点 / 节点缺选择器属性 -> point + medium；
    - 层级缺失、无法解析或点击点不在层级根范围（快照过期/旋转）-> point + low。
    """
    try:
        px, py = int(x), int(y)
    except (TypeError, ValueError):
        return {"by": "point", "x": 0, "y": 0}, CONFIDENCE_LOW, "坐标非法，使用坐标定位"

    tree = _tree_from_pre(pre_tree)
    if tree is None:
        return (
            {"by": "point", "x": px, "y": py},
            CONFIDENCE_LOW,
            "缺少层级快照，使用坐标定位",
        )
    if not _contains(tree.bounds, px, py):
        return (
            {"by": "point", "x": px, "y": py},
            CONFIDENCE_LOW,
            "点击点不在层级根范围内（快照可能过期），使用坐标定位",
        )
    node = _smallest_clickable(tree, px, py)
    if node is not None:
        # 优先「可读文本」：容器往往只有 resource_id（如 header_view），回放易受布局改名影响；
        # 子节点的 text/content_desc 更语义化、更稳定。
        readable_child = _descendant_with_selector(node, px, py)
        readable_target = node_target(readable_child) if readable_child is not None else None
        node_target_value = node_target(node)
        if readable_target is not None and readable_target["by"] in ("text", "content_desc"):
            return (
                readable_target,
                CONFIDENCE_HIGH,
                f"命中可点击容器内的文本子节点 {readable_target['by']}={readable_target['value']}",
            )
        if node_target_value is not None:
            if readable_target is not None and node_target_value["by"] == "resource_id":
                return (
                    readable_target,
                    CONFIDENCE_HIGH,
                    f"命中可点击容器内的子节点 {readable_target['by']}={readable_target['value']}",
                )
            return (
                node_target_value,
                CONFIDENCE_HIGH,
                f"命中可点击节点 {node_target_value['by']}={node_target_value['value']}",
            )
        if readable_target is not None:
            return (
                readable_target,
                CONFIDENCE_HIGH,
                f"命中可点击容器内的子节点 {readable_target['by']}={readable_target['value']}",
            )
    # 没有任何可点击节点包含该点：退一步用「带选择器的文本节点」，比裸坐标稳定得多。
    # 但只接受「有可读文本」或「面积足够小」的容器，避免把整页容器（如 springbacklayout）当目标。
    fallback = _smallest_selector_node(tree, px, py)
    fallback_target = node_target(fallback)
    if fallback is not None and fallback_target is not None:
        root_area = _area(tree.bounds) or 1
        fallback_area = _area(fallback.bounds)
        readable = bool(fallback.text or fallback.content_desc)
        if not readable and fallback_area > root_area * 0.3:
            fallback_target = None
    if fallback_target is not None:
        return (
            fallback_target,
            CONFIDENCE_MEDIUM,
            f"未命中可点击节点，使用文本节点 {fallback_target['by']}={fallback_target['value']}",
        )
    return (
        {"by": "point", "x": px, "y": py},
        CONFIDENCE_MEDIUM,
        "未找到包含点击点的可定位节点，使用坐标定位",
    )


def attribute(driver: Any, pre_tree: Any, x: int, y: int) -> dict[str, Any] | None:
    """点击点归因（对外签名，契约 §14.5 配套）；坐标非法返回 None。"""
    try:
        int(x), int(y)
    except (TypeError, ValueError):
        return None
    target, _confidence, _reason = attribute_with_confidence(driver, pre_tree, x, y)
    return target


# ---------------------------------------------------------------- 事件访问
def _event_get(event: Any, field: str, default: Any = None) -> Any:
    if isinstance(event, dict):
        return event.get(field, default)
    return getattr(event, field, default)


def _event_payload(event: Any) -> dict[str, Any]:
    payload = _event_get(event, "payload")
    return payload if isinstance(payload, dict) else {}


def _valid_target(data: Any) -> dict[str, Any] | None:
    """校验/规整 Target；容忍不带 by 的 {x,y}（录制内部 from/to 常用该结构）。"""
    if not isinstance(data, dict):
        return None
    payload = dict(data)
    if "by" not in payload and "x" in payload and "y" in payload:
        payload["by"] = "point"
    try:
        result = Target.model_validate(payload).model_dump(exclude_none=True)
    except Exception:
        return None
    if result.get("by") == "point":
        result.pop("index", None)  # 契约示例中 point target 不带 index
    return result


def _point_target(x: Any, y: Any) -> dict[str, Any] | None:
    try:
        return {"by": "point", "x": int(x), "y": int(y)}
    except (TypeError, ValueError):
        return None


def _point_pair(payload: dict[str, Any], prefix: str) -> tuple[Any, Any]:
    return payload.get(f"{prefix}x"), payload.get(f"{prefix}y")


def _payload_point(payload: dict[str, Any], *prefixes: str) -> dict[str, Any] | None:
    for prefix in prefixes:
        target = _point_target(*_point_pair(payload, prefix))
        if target is not None:
            return target
    return None


def _looks_like_scroll(payload: dict[str, Any]) -> bool:
    start = payload.get("from")
    end = payload.get("to")
    if not isinstance(start, dict) or not isinstance(end, dict):
        return False
    try:
        dy = abs(int(end.get("y", 0)) - int(start.get("y", 0)))
        dx = abs(int(end.get("x", 0)) - int(start.get("x", 0)))
    except (TypeError, ValueError):
        return False
    return dy > dx


# ---------------------------------------------------------------- 单个事件 -> 节点
def _split_key(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw_key = payload.get("key")
    raw_code = payload.get("keycode")
    name: str | None = None
    if isinstance(raw_key, str) and raw_key.strip():
        text = raw_key.strip()
        lowered = text.lower()
        if lowered in KEY_CODES:
            name = lowered
        elif lowered.startswith("keycode_"):
            candidate = lowered[len("keycode_") :]
            if candidate in KEY_CODES:
                name = candidate
    if name is not None:
        return {"key": name}
    code: int | None = None
    if isinstance(raw_code, int) and not isinstance(raw_code, bool):
        code = raw_code
    elif isinstance(raw_code, str) and raw_code.strip().isdigit():
        code = int(raw_code.strip())
    if code is None and isinstance(raw_key, str) and raw_key.strip().isdigit():
        code = int(raw_key.strip())
    if code is None:
        return None
    if code in _KEYCODE_TO_NAME:
        return {"key": _KEYCODE_TO_NAME[code]}
    return {"keycode": code}


def _target_label(target: dict[str, Any] | None) -> str:
    if target and target.get("by") != "point" and target.get("value"):
        return f"「{target['value']}」"
    return ""


def _swipe_direction(start: dict[str, Any], end: dict[str, Any]) -> str:
    try:
        dx = int(end.get("x", 0)) - int(start.get("x", 0))
        dy = int(end.get("y", 0)) - int(start.get("y", 0))
    except (TypeError, ValueError):
        return "滑动"
    if abs(dy) >= abs(dx):
        return "向上滑动" if dy < 0 else "向下滑动"
    return "向左滑动" if dx < 0 else "向右滑动"


def _build_description(
    kind: str,
    payload: dict[str, Any],
    confidence: str,
) -> str:
    parts = [f"来源={payload.get('source') or 'web'}", f"原始事件={kind}"]
    x, y = payload.get("x"), payload.get("y")
    if x is not None and y is not None:
        parts.append(f"坐标=({x}, {y})")
    start, end = payload.get("from"), payload.get("to")
    if isinstance(start, dict) and isinstance(end, dict):
        parts.append(
            f"起点=({start.get('x')}, {start.get('y')}) 终点=({end.get('x')}, {end.get('y')})"
            f" 时长={payload.get('duration_ms')}ms"
        )
    if payload.get("text"):
        parts.append(f"文本={payload['text']}")
    if payload.get("key") or payload.get("keycode") is not None:
        parts.append(f"按键={payload.get('key') or payload.get('keycode')}")
    if confidence != CONFIDENCE_HIGH:
        parts.append(f"置信度={confidence}")
    reason = payload.get("confidence_reason")
    if reason:
        parts.append(f"归因={reason}")
    return "；".join(str(part) for part in parts)[:500]


def _event_timestamp(event: Any) -> float | None:
    """事件时间戳（秒）：优先 created_at(ISO)，兼容 ts 数字。"""
    raw = _event_get(event, "created_at", None)
    if raw is None:
        raw = _event_get(event, "ts", None)
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _gap_delay_ms(previous_ts: float | None, current_ts: float | None) -> int:
    """两次操作之间的真实间隔 → 节点 delay_before_ms（上限 3s，避免录到长离开）。"""
    if previous_ts is None or current_ts is None:
        return 0
    gap = int(round((current_ts - previous_ts) * 1000))
    return max(0, min(_STEP_DELAY_MAX_MS, gap))


TARGET_MODE_POINT = "point"
TARGET_MODE_ELEMENT = "element"
TARGET_MODES = (TARGET_MODE_POINT, TARGET_MODE_ELEMENT)


def _convert_event(
    kind: str,
    payload: dict[str, Any],
    confidence: str,
    target_mode: str = TARGET_MODE_POINT,
) -> tuple[str, str, str, dict[str, Any], str, dict[str, Any] | None] | None:
    """返回 (type, name, description, params, rerun_safety, point_target_if_used)。

    target_mode：
    - ``point``（默认）：点击/长按一律记**绝对坐标**。游戏/自绘 UI、无障碍树不可靠时这是唯一可靠写法；
      元素信息只写进 description 供人参考。
    - ``element``：优先元素定位（text/resource_id…），失败退回坐标。
    """
    step_type = classify(kind, payload)
    extra_notes: list[str] = []
    if step_type == "app":
        package = str(payload.get("package") or "").strip()
        if not package:
            return None
        action = str(payload.get("action") or "launch").strip().lower()
        if action not in ("launch", "stop", "clear", "restart"):
            action = "launch"
        activity = payload.get("activity")
        params = {
            "action": action,
            "package": package,
            "activity": str(activity) if activity else None,
        }
        name = f"{ {'launch': '启动', 'stop': '停止', 'clear': '清除数据', 'restart': '重启'}[action] } {package}"
        description = f"录制：{action} {package}" + (f" / {activity}" if activity else "")
        safety = "destructive" if action in ("stop", "clear", "restart") else "safe"
        return step_type, name, description, params, safety, None
    if step_type == "click":
        point = _point_target(*_point_pair(payload, ""))
        element = _valid_target(payload.get("node") or payload.get("target"))
        if target_mode == TARGET_MODE_POINT:
            target = point or element
        else:
            target = element or point
        if target is None:
            return None
        if target.get("by") == "point":
            label = f"{target.get('x')}, {target.get('y')}"
            name = f"点击坐标 ({label})"
        else:
            label = _target_label(target) or f"坐标 ({target.get('x')}, {target.get('y')})"
            name = f"点击{label}"
        params = {"target": target}
        safety = "safe"
    elif step_type == "long_click":
        point = _point_target(*_point_pair(payload, ""))
        element = _valid_target(payload.get("node") or payload.get("target"))
        if target_mode == TARGET_MODE_POINT:
            target = point or element
        else:
            target = element or point
        if target is None:
            return None
        duration = _clamp(payload.get("duration_ms"), 100, 10_000, 800)
        label = _target_label(target) or f"坐标 ({target.get('x')}, {target.get('y')})"
        name = f"长按{label}"
        params = {"target": target, "duration_ms": duration}
        safety = "safe"
    elif step_type in ("swipe", "drag"):
        start = _valid_target(payload.get("from")) or _point_target(payload.get("x1"), payload.get("y1"))
        end = _valid_target(payload.get("to")) or _point_target(payload.get("x2"), payload.get("y2"))
        if start is None or end is None:
            start = start or _point_target(payload.get("x"), payload.get("y"))
            if start is None or end is None:
                return None
        if step_type == "swipe":
            duration = _clamp(payload.get("duration_ms"), 50, 10_000, 300)
            name = _swipe_direction(start, end)
            extra_notes.append(f"方向={name}")
            safety = "safe"
        else:
            duration = _clamp(payload.get("duration_ms"), 100, 10_000, 1000)
            name = f"拖拽 ({start.get('x')}, {start.get('y')}) → ({end.get('x')}, {end.get('y')})"
            safety = "safe"
        params = {"from": start, "to": end, "duration_ms": duration}
        # 录制轨迹：回放时按原轨迹重演（弧形/变速滑动与录制一致）
        raw_path = payload.get("path")
        if isinstance(raw_path, list) and len(raw_path) >= 2:
            samples: list[dict[str, int]] = []
            for item in raw_path[:_MAX_GESTURE_PATH]:
                if not isinstance(item, dict):
                    continue
                try:
                    samples.append(
                        {
                            "x": int(item["x"]),
                            "y": int(item["y"]),
                            "dt_ms": max(0, int(item.get("dt_ms") or 0)),
                        }
                    )
                except (KeyError, TypeError, ValueError):
                    continue
            if len(samples) >= 2:
                params["path"] = samples
                try:
                    sx, sy = int(start.get("x", 0)), int(start.get("y", 0))
                    ex, ey = int(end.get("x", 0)), int(end.get("y", 0))
                    extent = max(
                        math.hypot(point["x"] - sx, point["y"] - sy) for point in samples
                    )
                    if math.hypot(ex - sx, ey - sy) < POINT_MAX_PX and extent >= POINT_MAX_PX:
                        name = "画圈"
                        extra_notes.append("轨迹=画圈")
                except (TypeError, ValueError):
                    pass
    elif step_type == "input":
        text = payload.get("text")
        if text is None:
            return None
        text = str(text)
        target = _valid_target(payload.get("node") or payload.get("target"))
        preview = text if len(text) <= 20 else text[:20] + "…"
        name = f"输入「{preview}」" if text else "输入空文本"
        params = {"text": text, "target": target, "clear_first": bool(payload.get("clear_first", False))}
        safety = "safe"
    elif step_type == "key":
        key_params = _split_key(payload)
        if key_params is None:
            return None
        name = f"按键「{key_params.get('key')}」" if key_params.get("key") else f"按键 keycode={key_params['keycode']}"
        params = key_params
        safety = "safe"
    else:
        return None

    description = _build_description(kind, payload, confidence)
    if extra_notes:
        # 保留方向/画圈等关键后缀：必要时截断前面的原始描述，避免后缀被 500 字上限吃掉。
        suffix = "；".join(extra_notes)
        description = description[: max(0, 500 - len(suffix) - 1)] + "；" + suffix
    point = params.get("target") if isinstance(params.get("target"), dict) else None
    return step_type, name[:120], description, params, safety, point


def _next_step_name(step_type: str, fallback: str, counters: dict[str, int]) -> str:
    """短名称：类型标签 + 整份脚本内同类型序号；未知类型回退原名称。"""
    label = _NODE_NAME_LABELS.get(step_type)
    if label is None:
        return fallback
    count = counters.get(step_type, 0) + 1
    counters[step_type] = count
    return f"{label}{count}"


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


# ---------------------------------------------------------------- 事件列表 -> 脚本
def events_to_script(
    events: Iterable[Any],
    name: str = "录制脚本",
    include_kinds: list[str] | None = None,
    exclude_seqs: Iterable[int] | None = None,
    recording_id: str | None = None,
    target_mode: str = TARGET_MODE_POINT,
) -> tuple[dict[str, Any], list[str]]:
    """录制事件 -> 契约 §3 脚本。

    - 一事件一节点（**不**合并同点连续 tap，避免自创语义）；
    - include_kinds 白名单过滤（按 event.kind 原始值），exclude_seqs 排除指定 seq；
    - app 启动/切换事件会生成 app 节点（保证回放从同一界面开始）；
    - target_mode=point（默认）：点击/长按记绝对坐标（游戏/自绘 UI 唯一可靠方式）；
      target_mode=element：优先元素定位，失败退回坐标；
    - warnings 为面向用户的中文提示（坐标定位、低置信度、疑似滚动等）。

    返回 (script, warnings)，script 结构可通过 `schemas.validate_script()`。
    """
    allowed: set[str] | None = None
    if include_kinds:
        allowed = {str(item).strip().lower() for item in include_kinds if str(item).strip()}
    excluded = set()
    for seq in exclude_seqs or []:
        try:
            excluded.add(int(seq))
        except (TypeError, ValueError):
            continue

    steps: list[dict[str, Any]] = []
    warnings: list[str] = []
    coordinate_nodes = 0
    low_confidence_nodes = 0
    swipe_count = 0
    skipped = 0
    previous_ts: float | None = None
    name_counters: dict[str, int] = {}

    for event in events:
        kind = str(_event_get(event, "kind", "") or "").strip()
        if not kind:
            skipped += 1
            continue
        try:
            seq = int(_event_get(event, "seq", 0) or 0)
        except (TypeError, ValueError):
            seq = 0
        if seq in excluded:
            continue
        if allowed is not None and kind.lower() not in allowed:
            continue
        if kind.lower() in _SKIP_KINDS:
            skipped += 1
            continue

        payload = _event_payload(event)
        confidence = str(_event_get(event, "confidence", CONFIDENCE_HIGH) or CONFIDENCE_HIGH)
        converted = _convert_event(kind, payload, confidence, target_mode)
        if converted is None:
            skipped += 1
            continue
        step_type, step_name, description, params, safety, point = converted
        if step_type in ("swipe", "drag") and _looks_like_scroll(payload):
            swipe_count += 1
        if point is not None and point.get("by") == "point":
            coordinate_nodes += 1
        if confidence == CONFIDENCE_LOW:
            low_confidence_nodes += 1

        event_ts = _event_timestamp(event)

        # point 模式下点击都是坐标定位：输入事件的目标（元素）在回放时未必可靠，
        # 统一改为「先点击上一个触点坐标聚焦，再输入」。
        if step_type == "input" and target_mode == TARGET_MODE_POINT:
            if params.get("target") is not None:
                params["target"] = None
            if not steps or steps[-1]["type"] != "click":
                focus_point = _point_target(*_point_pair(payload, ""))
                if focus_point is not None:
                    steps.append(
                        {
                            "id": f"s{len(steps) + 1}",
                            "name": _next_step_name("click", "点击聚焦", name_counters),
                            "type": "click",
                            "description": "输入前点击聚焦（录制未带独立点击时自动补）",
                            "enabled": True,
                            "when": None,
                            "timeout_ms": None,
                            "delay_before_ms": _gap_delay_ms(previous_ts, event_ts) if steps else _FIRST_STEP_DELAY_MS,
                            "delay_after_ms": 300,
                            "retry": None,
                            "on_fail": None,
                            "screenshot_on_fail": True,
                            "rerun_safety": "safe",
                            "tags": ["auto-focus"],
                            "params": {"target": focus_point},
                        }
                    )

        # 紧跟在「同目标点击」之后的输入：点击已经完成聚焦，输入节点不应再带 target，
        # 否则回放会二次点击（页面可能已跳转，目标消失 → 输入失败）。
        if step_type == "input" and steps and steps[-1]["type"] == "click":
            previous_target = (steps[-1].get("params") or {}).get("target")
            current_target = params.get("target")
            if previous_target is not None and current_target == previous_target:
                params["target"] = None
                description = (description + "；目标由上一节点点击聚焦").strip("；")

        delay = (
            _FIRST_STEP_DELAY_MS
            if not steps
            else _gap_delay_ms(previous_ts, event_ts)
        )
        if event_ts is not None:
            previous_ts = event_ts

        steps.append(
            {
                "id": f"s{len(steps) + 1}",
                "name": _next_step_name(step_type, step_name, name_counters),
                "type": step_type,
                "description": description,
                "enabled": True,
                "when": None,
                "timeout_ms": None,
                "delay_before_ms": delay,
                "delay_after_ms": 0,
                "retry": None,
                "on_fail": None,
                "screenshot_on_fail": True,
                "rerun_safety": safety,
                "tags": [],
                "params": params,
            }
        )

    script: dict[str, Any] = {
        "schema_version": 1,
        "name": (str(name or "录制脚本").strip() or "录制脚本")[:200],
        "notes": f"由录制 {recording_id} 转换" if recording_id else "根据录制事件生成",
        "variables": {},
        "settings": {
            "default_timeout_ms": 8000,
            # 录制脚本不额外加默认停顿：节奏由事件时间间隔还原到节点的 delay_before_ms。
            "default_delay_before_ms": 0,
            "default_delay_after_ms": 0,
            "on_fail": "abort",
            "logcat": False,
            "retry": None,
            "screenshot_on_fail": True,
        },
        "defaults": {"retry": None},
        "steps": steps,
    }

    if target_mode == TARGET_MODE_POINT and steps:
        point_steps = sum(
            1
            for step in steps
            if (step.get("params") or {}).get("target", {}) and (step["params"]["target"].get("by") == "point")
        )
        if point_steps:
            warnings.append(
                f"{point_steps} 个节点使用绝对坐标（按你的选择，坐标最可靠；换分辨率/布局变化时需重录）"
            )
    delayed_steps = sum(1 for step in steps if int(step.get("delay_before_ms") or 0) > 0)
    if delayed_steps:
        warnings.append(f"已按录制时的操作间隔还原 {delayed_steps} 个节点的延迟（回放更接近人工节奏）")
    if skipped:
        warnings.append(f"{skipped} 个事件无法转换，已跳过")
    if coordinate_nodes and target_mode == TARGET_MODE_ELEMENT:
        # 仅在「元素定位」模式下提示：point 模式本来就是用户要求的纯坐标写法
        warnings.append(f"{coordinate_nodes} 个节点未能识别元素、退回坐标定位")
    if low_confidence_nodes:
        warnings.append(f"{low_confidence_nodes} 个节点置信度低（层级缺失/过期），建议运行前补充元素选择器")
    if swipe_count:
        warnings.append(f"检测到疑似滚动（{swipe_count} 次滑动），已生成 swipe 节点")
    if not steps:
        warnings.append("没有可转换的录制事件")

    validation = validate_script(script)
    if not validation.get("valid"):
        for error in validation.get("errors", []):
            warnings.append(f"脚本校验失败: {error.get('path')} {error.get('message')}")
    return script, warnings
