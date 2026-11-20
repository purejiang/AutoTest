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

import re
from collections.abc import Iterable
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

# 契约 §13：录制不产生 app 节点
_SKIP_KINDS = {"app"}

# rerun_safety：录制出来的手势/输入/按键都按 safe 处理（契约 §3）
_SAFE_TYPES = {"click", "long_click", "swipe", "drag", "input", "key"}

_KEYCODE_TO_NAME: dict[int, str] = {code: name for name, code in KEY_CODES.items()}

_BOUNDS_RE = re.compile(r"-?\d+")


# ---------------------------------------------------------------- 手势分类
def classify(kind: str, payload: dict[str, Any] | None = None) -> str:
    """把录制事件 kind 映射为脚本节点 type；未知 kind 原样返回（由调用方决定跳过）。"""
    return _KIND_TO_STEP.get(str(kind or "").strip().lower(), str(kind or ""))


def gesture_from_samples(duration_ms: float, distance_px: float) -> str:
    """按契约 §8 阈值把一次触摸手势归并为 tap / long_press / swipe / drag。"""
    duration_ms = float(duration_ms or 0)
    distance_px = float(distance_px or 0)
    if distance_px < POINT_MAX_PX:
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
    if node is None:
        return (
            {"by": "point", "x": px, "y": py},
            CONFIDENCE_MEDIUM,
            "未找到包含点击点的可点击节点，使用坐标定位",
        )
    target = node_target(node)
    if target is None:
        return (
            {"by": "point", "x": px, "y": py},
            CONFIDENCE_MEDIUM,
            "可点击节点缺少可用选择器属性，使用坐标定位",
        )
    return target, CONFIDENCE_HIGH, f"命中节点 {target['by']}={target['value']}"


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


def _convert_event(
    kind: str,
    payload: dict[str, Any],
    confidence: str,
) -> tuple[str, str, str, dict[str, Any], str, dict[str, Any] | None] | None:
    """返回 (type, name, description, params, rerun_safety, point_target_if_used)。"""
    step_type = classify(kind, payload)
    if step_type == "click":
        start = _point_target(*_point_pair(payload, ""))
        target = _valid_target(payload.get("node") or payload.get("target"))
        if target is None:
            target = start
        if target is None:
            return None
        label = _target_label(target) or f"坐标 ({target.get('x')}, {target.get('y')})"
        name = f"点击{label}"
        params = {"target": target}
        safety = "safe"
    elif step_type == "long_click":
        target = _valid_target(payload.get("node") or payload.get("target"))
        if target is None:
            target = _point_target(*_point_pair(payload, ""))
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
            safety = "safe"
        else:
            duration = _clamp(payload.get("duration_ms"), 100, 10_000, 1000)
            name = f"拖拽 ({start.get('x')}, {start.get('y')}) → ({end.get('x')}, {end.get('y')})"
            safety = "safe"
        params = {"from": start, "to": end, "duration_ms": duration}
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
    point = params.get("target") if isinstance(params.get("target"), dict) else None
    return step_type, name[:120], description, params, safety, point


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
) -> tuple[dict[str, Any], list[str]]:
    """录制事件 -> 契约 §3 脚本。

    - 一事件一节点（**不**合并同点连续 tap，避免自创语义）；
    - include_kinds 白名单过滤（按 event.kind 原始值），exclude_seqs 排除指定 seq；
    - app 事件按契约 §13 不产生节点；
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
    app_skipped = 0

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
            app_skipped += 1
            continue

        payload = _event_payload(event)
        confidence = str(_event_get(event, "confidence", CONFIDENCE_HIGH) or CONFIDENCE_HIGH)
        converted = _convert_event(kind, payload, confidence)
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

        steps.append(
            {
                "id": f"s{len(steps) + 1}",
                "name": step_name,
                "type": step_type,
                "description": description,
                "enabled": True,
                "when": None,
                "timeout_ms": None,
                "delay_before_ms": 0,
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
        "notes": (
            f"由录制 {recording_id} 转换，共 {len(steps)} 个节点" if recording_id else f"根据录制事件生成，共 {len(steps)} 个节点"
        ),
        "variables": {},
        "settings": {
            "default_timeout_ms": 8000,
            "on_fail": "abort",
            "logcat": False,
            "retry": None,
            "screenshot_on_fail": True,
        },
        "defaults": {"retry": None},
        "steps": steps,
    }

    if app_skipped:
        warnings.append(f"{app_skipped} 个应用切换事件未生成节点（录制不产生 app 节点）")
    if skipped:
        warnings.append(f"{skipped} 个事件无法转换，已跳过")
    if coordinate_nodes:
        warnings.append(f"{coordinate_nodes} 个节点用坐标定位，建议补充元素选择器")
    if low_confidence_nodes:
        warnings.append(f"{low_confidence_nodes} 个节点置信度低（层级缺失/过期），建议运行前补充元素选择器")
    if swipe_count:
        warnings.append(f"检测到疑似滚动（{swipe_count} 次滑动），已合并为 swipe 节点")
    if not steps:
        warnings.append("没有可转换的录制事件")

    validation = validate_script(script)
    if not validation.get("valid"):
        for error in validation.get("errors", []):
            warnings.append(f"脚本校验失败: {error.get('path')} {error.get('message')}")
    return script, warnings
