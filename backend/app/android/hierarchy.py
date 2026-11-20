"""uiautomator XML 层级解析（契约 §4 精简树）。

职责边界：
- 只做 XML <-> ``HierarchyNode`` 的转换，不做目标匹配（selectors.py 负责）；
- ``parse_xml`` 产出文档顺序的树（保留 ``index`` 属性的先后关系，不重排序）；
- 空/占位 XML 返回空根；**畸形 XML 抛** ``ApiError("INTERNAL")``，策略见各函数 docstring。

uiautomator 的 node 属性到 ``HierarchyNode`` 的映射：
    text                -> text
    resource-id         -> resource_id
    content-desc        -> content_desc
    class               -> class_name
    package             -> package
    bounds              -> bounds ``[x1,y1,x2,y2]``（解析自 ``[x1,y1][x2,y2]``）
    clickable           -> clickable
    enabled             -> enabled
    visible-to-user     -> visible（缺省 True；``visible="false"`` 也兼容）
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ..errors import ApiError
from .driver import HierarchyNode

_BOUNDS_RE = re.compile(r"^\s*\[(-?\d+),(-?\d+)\]\s*\[(-?\d+),(-?\d+)\]\s*$")

_TRUE_VALUES = frozenset({"true", "1", "yes", "y", "on"})


# ---------------------------------------------------------------- 属性解析
def _tag_name(element: ET.Element) -> str:
    """去掉 XML 命名空间后的标签名（兼容带 ns 的 dump）。"""
    tag = element.tag
    if isinstance(tag, str) and "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return str(tag)


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in _TRUE_VALUES


def parse_bounds(value: str | None) -> list[int]:
    """``[x1,y1][x2,y2]`` -> ``[x1,y1,x2,y2]``；缺失/畸形返回 ``[0,0,0,0]``。"""
    if not value:
        return [0, 0, 0, 0]
    match = _BOUNDS_RE.match(str(value))
    if not match:
        return [0, 0, 0, 0]
    return [int(part) for part in match.groups()]


def _node_from_element(element: ET.Element) -> HierarchyNode:
    attrs = element.attrib
    node = HierarchyNode(
        class_name=attrs.get("class", "") or "",
        text=attrs.get("text", "") or "",
        resource_id=attrs.get("resource-id", "") or attrs.get("resource_id", "") or "",
        content_desc=attrs.get("content-desc", "")
        or attrs.get("content_description", "")
        or attrs.get("contentDescription", "")
        or "",
        package=attrs.get("package", "") or "",
        bounds=parse_bounds(attrs.get("bounds")),
        clickable=_as_bool(attrs.get("clickable"), False),
        enabled=_as_bool(attrs.get("enabled"), True),
        visible=_as_bool(attrs.get("visible-to-user", attrs.get("visible")), True),
    )
    for child in element:
        if _tag_name(child) == "node":
            node.children.append(_node_from_element(child))
    return node


def _empty_root() -> HierarchyNode:
    return HierarchyNode(class_name="hierarchy")


def _union_bounds(nodes: list[HierarchyNode]) -> list[int]:
    boxes = [node.bounds for node in nodes if node.bounds and node.bounds[2] > node.bounds[0]]
    if not boxes:
        return [0, 0, 0, 0]
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


# ---------------------------------------------------------------- 解析 / 序列化
def parse_xml(xml_str: str | bytes) -> HierarchyNode:
    """把 uiautomator dump 的 XML 解析成精简树。

    策略：
    - 空字符串 / 纯空白 / ``<hierarchy/>`` -> 空根（``class_name="hierarchy"``，无子节点）；
    - 根为 ``<hierarchy>``：恰好一个 ``<node>`` 子节点时直接返回该节点；
      多个 ``<node>``（多窗口/分屏）时返回合成根，children 为各节点（bounds 取并集）；
    - 根直接是 ``<node>`` -> 返回该节点；
    - 其他根标签 / 畸形 XML -> ``ApiError("INTERNAL")``（调用方可当瞬时错误重试）。
    """
    if xml_str is None:  # pragma: no cover - 防御性
        raise ApiError("INVALID_ARGUMENT", "hierarchy xml is required")
    if isinstance(xml_str, (bytes, bytearray)):
        text = bytes(xml_str).decode("utf-8", errors="replace")
    else:
        text = str(xml_str)
    text = text.lstrip("\ufeff").strip()
    if not text:
        return _empty_root()
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ApiError(
            "INTERNAL",
            f"invalid hierarchy xml: {exc}",
            {"reason": "xml_parse_error"},
        ) from exc

    tag = _tag_name(root)
    if tag == "node":
        return _node_from_element(root)
    if tag == "hierarchy":
        children = [_node_from_element(child) for child in root if _tag_name(child) == "node"]
        if not children:
            return _empty_root()
        if len(children) == 1:
            return children[0]
        synthetic = HierarchyNode(
            class_name="hierarchy",
            package=children[0].package,
            bounds=_union_bounds(children),
            children=children,
        )
        return synthetic
    raise ApiError("INTERNAL", f"unexpected hierarchy root tag: {tag}", {"tag": tag})


def flatten(tree: HierarchyNode | None) -> list[HierarchyNode]:
    """先序遍历展开（含根节点本身），顺序与 XML 文档一致。"""
    if tree is None:
        return []
    result: list[HierarchyNode] = []
    stack: list[HierarchyNode] = [tree]
    while stack:
        node = stack.pop()
        result.append(node)
        stack.extend(reversed(node.children))
    return result


def to_xml_string(node: HierarchyNode | None) -> str:
    """把精简树序列化回 uiautomator 风格 XML（供 FakeDriver / 录制 raw 展示兜底）。

    ``class_name == "hierarchy"`` 的合成根会被展开为其 children 的容器（不作为一个 node 输出）。
    注意：只保留 ``HierarchyNode`` 能表达的属性，``index`` 按 children 顺序重新编号。
    """
    wrapper = ET.Element("hierarchy", {"rotation": "0"})
    if node is not None:
        if node.class_name == "hierarchy":
            for index, child in enumerate(node.children):
                wrapper.append(_node_to_element(child, index))
        else:
            wrapper.append(_node_to_element(node, 0))
    header = "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n"
    return header + ET.tostring(wrapper, encoding="unicode")


def _node_to_element(node: HierarchyNode, index: int) -> ET.Element:
    x1, y1, x2, y2 = (list(node.bounds) + [0, 0, 0, 0])[:4]
    element = ET.Element(
        "node",
        {
            "index": str(index),
            "text": node.text,
            "resource-id": node.resource_id,
            "class": node.class_name,
            "package": node.package,
            "content-desc": node.content_desc,
            "clickable": "true" if node.clickable else "false",
            "enabled": "true" if node.enabled else "false",
            "visible-to-user": "true" if node.visible else "false",
            "bounds": f"[{x1},{y1}][{x2},{y2}]",
        },
    )
    for child_index, child in enumerate(node.children):
        element.append(_node_to_element(child, child_index))
    return element
