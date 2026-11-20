"""录制转换：手势分类边界、点击归因、事件 -> 脚本（FakeDriver + 纯数据，无真机）。"""

from __future__ import annotations

import pytest

from app.android.driver import FakeDriver
from app.recorder import convert
from app.schemas import validate_script


def _event(seq: int, kind: str, payload: dict, confidence: str = "high") -> dict:
    return {"seq": seq, "kind": kind, "payload": payload, "confidence": confidence}


def _node_target(*, by: str = "text", value: str = "登录") -> dict:
    return {"by": by, "value": value, "index": 0}


# ---------------------------------------------------------------- classify / 手势阈值
def test_classify_maps_hook_kinds_to_step_types() -> None:
    assert convert.classify("tap") == "click"
    assert convert.classify("long_press") == "long_click"
    assert convert.classify("long_click") == "long_click"
    assert convert.classify("swipe") == "swipe"
    assert convert.classify("drag") == "drag"
    assert convert.classify("input") == "input"
    assert convert.classify("key") == "key"
    assert convert.classify("app") == "app"
    assert convert.classify("unknown-kind") == "unknown-kind"


@pytest.mark.parametrize(
    ("duration_ms", "distance", "expected"),
    [
        (0, 0, "tap"),
        (499, 9, "tap"),  # 时间边界：< 500ms + 位移 < 10px
        (500, 9, "long_press"),  # >= 500ms + 位移 < 10px
        (1500, 9, "long_press"),
        (0, 10, "swipe"),  # 位移边界：>= 10px + < 300ms
        (299, 10, "swipe"),
        (299, 400, "swipe"),
        (300, 10, "drag"),  # >= 300ms + >= 10px
        (800, 200, "drag"),
    ],
)
def test_gesture_from_samples_boundaries(duration_ms: int, distance: int, expected: str) -> None:
    assert convert.gesture_from_samples(duration_ms, distance) == expected


# ---------------------------------------------------------------- 节点归属
def test_attribute_button_center_hits_text_node() -> None:
    driver = FakeDriver("fake-convert")
    pre = driver.dump_hierarchy().to_dict()
    target, confidence, reason = convert.attribute_with_confidence(driver, pre, 540, 710)
    assert target == {"by": "text", "value": "登录", "index": 0}
    assert confidence == "high"
    assert "登录" in reason
    assert convert.attribute(driver, pre, 540, 710) == target


def test_attribute_input_field_falls_back_to_resource_id() -> None:
    driver = FakeDriver("fake-convert")
    pre = driver.dump_hierarchy().to_dict()
    target, confidence, _reason = convert.attribute_with_confidence(driver, pre, 540, 360)
    assert target == {"by": "resource_id", "value": "com.example.fake:id/et_account", "index": 0}
    assert confidence == "high"


def test_attribute_blank_point_is_point_medium() -> None:
    driver = FakeDriver("fake-convert")
    pre = driver.dump_hierarchy().to_dict()
    target, confidence, _reason = convert.attribute_with_confidence(driver, pre, 5, 5)
    assert target == {"by": "point", "x": 5, "y": 5}
    assert confidence == "medium"


def test_attribute_out_of_bounds_is_point_low() -> None:
    driver = FakeDriver("fake-convert")
    pre = driver.dump_hierarchy().to_dict()
    target, confidence, _reason = convert.attribute_with_confidence(driver, pre, 5000, 5000)
    assert target == {"by": "point", "x": 5000, "y": 5000}
    assert confidence == "low"


def test_attribute_missing_or_broken_tree_is_point_low() -> None:
    driver = FakeDriver("fake-convert")
    for pre in (None, {}, {"children": "not-a-list"}):
        target, confidence, _reason = convert.attribute_with_confidence(driver, pre, 540, 710)
        assert target == {"by": "point", "x": 540, "y": 710}
        assert confidence == "low"
    assert convert.attribute(driver, None, "bad", 1) is None


# ---------------------------------------------------------------- 事件 -> 脚本
def test_events_to_script_is_valid_and_one_node_per_event() -> None:
    events = [
        _event(
            1,
            "tap",
            {"x": 540, "y": 710, "duration_ms": 0, "node": _node_target(), "source": "web"},
        ),
        _event(
            2,
            "long_press",
            {"x": 540, "y": 360, "duration_ms": 900, "node": _node_target(by="resource_id", value="id/a")},
        ),
        _event(
            3,
            "swipe",
            {"from": {"x": 540, "y": 1800}, "to": {"x": 540, "y": 600}, "duration_ms": 250},
        ),
        _event(4, "tap", {"x": 5, "y": 5, "duration_ms": 0, "node": {"by": "point", "x": 5, "y": 5}}),
        _event(5, "input", {"text": "hello", "node": _node_target(by="resource_id", value="id/account")}),
        _event(6, "key", {"key": "back"}),
        _event(7, "key", {"keycode": 3}),
        _event(8, "app", {"package": "com.example.fake", "action": "launch"}),
    ]
    script, warnings = convert.events_to_script(events, "录制脚本", recording_id="rec_x")

    result = validate_script(script)
    assert result["valid"] is True, result["errors"]
    assert [step["id"] for step in script["steps"]] == ["s1", "s2", "s3", "s4", "s5", "s6", "s7"]
    assert [step["type"] for step in script["steps"]] == [
        "click",
        "long_click",
        "swipe",
        "click",
        "input",
        "key",
        "key",
    ]
    # 一事件一节点：两个 tap 不合并
    assert script["steps"][0]["params"]["target"] == _node_target()
    assert script["steps"][1]["params"]["duration_ms"] == 900
    assert script["steps"][2]["params"]["from"] == {"by": "point", "x": 540, "y": 1800}
    assert script["steps"][2]["params"]["to"] == {"by": "point", "x": 540, "y": 600}
    assert script["steps"][5]["params"] == {"key": "back"}
    assert script["steps"][6]["params"] == {"key": "home"}
    # 公共字段齐全
    for step in script["steps"]:
        assert step["name"]
        assert step["description"]
        assert step["when"] is None
        assert step["timeout_ms"] is None
        assert step["retry"] is None
        assert step["on_fail"] is None
        assert step["enabled"] is True
        assert step["screenshot_on_fail"] is True
        assert step["rerun_safety"] == "safe"
        assert step["tags"] == []
    assert script["schema_version"] == 1
    assert script["settings"]["on_fail"] == "abort"
    # 中文命名 + warnings
    assert script["steps"][0]["name"] == "点击「登录」"
    assert any("坐标定位" in warning for warning in warnings)
    assert any("应用切换事件" in warning for warning in warnings)


def test_events_to_script_filter_include_kinds_and_exclude_seqs() -> None:
    events = [
        _event(1, "tap", {"x": 540, "y": 710, "node": _node_target()}),
        _event(2, "swipe", {"from": {"x": 540, "y": 1800}, "to": {"x": 540, "y": 600}, "duration_ms": 200}),
        _event(3, "key", {"key": "back"}),
    ]
    script, _warnings = convert.events_to_script(events, include_kinds=["tap", "swipe"])
    assert [step["type"] for step in script["steps"]] == ["click", "swipe"]

    script, _warnings = convert.events_to_script(events, exclude_seqs=[2])
    assert [step["type"] for step in script["steps"]] == ["click", "key"]
    assert validate_script(script)["valid"] is True


def test_events_to_script_empty_yields_invalid_script_and_warning() -> None:
    script, warnings = convert.events_to_script([], "空录制")
    assert script["steps"] == []
    assert validate_script(script)["valid"] is False
    assert any("没有可转换" in warning for warning in warnings)


def test_events_to_script_flags_low_confidence() -> None:
    events = [
        _event(
            1,
            "tap",
            {"x": 540, "y": 710, "node": {"by": "point", "x": 540, "y": 710}},
            confidence="low",
        )
    ]
    script, warnings = convert.events_to_script(events)
    assert script["steps"][0]["params"]["target"] == {"by": "point", "x": 540, "y": 710}
    assert any("置信度低" in warning for warning in warnings)
