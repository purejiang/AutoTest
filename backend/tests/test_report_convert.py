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


def test_gesture_from_samples_uses_path_extent_for_circles() -> None:
    """画圆回到起点：终点位移很小，但轨迹离起点很远，应判为 swipe/drag。"""
    assert convert.gesture_from_samples(500, 2) == "long_press"
    assert convert.gesture_from_samples(500, 2, 120) == "drag"
    assert convert.gesture_from_samples(200, 2, 120) == "swipe"
    assert convert.gesture_from_samples(500, 2, 8) == "long_press"


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
    script, warnings = convert.events_to_script(
        events, "录制脚本", recording_id="rec_x", target_mode="element"
    )

    result = validate_script(script)
    assert result["valid"] is True, result["errors"]
    assert [step["id"] for step in script["steps"]] == [
        "s1",
        "s2",
        "s3",
        "s4",
        "s5",
        "s6",
        "s7",
        "s8",
    ]
    assert [step["type"] for step in script["steps"]] == [
        "click",
        "long_click",
        "swipe",
        "click",
        "input",
        "key",
        "key",
        "app",
    ]
    # app 事件 → app 节点（回放从同一界面开始）
    assert script["steps"][7]["params"]["package"] == "com.example.fake"
    assert script["steps"][7]["params"]["action"] == "launch"
    # 回放时序：首个节点有起步延迟（给应用/页面稳定时间）
    assert script["steps"][0]["delay_before_ms"] == convert._FIRST_STEP_DELAY_MS
    # 录制脚本不继承全节点默认停顿：节奏只由录制还原的节点间隔决定
    assert script["settings"]["default_delay_before_ms"] == 0
    assert script["settings"]["default_delay_after_ms"] == 0
    assert script["settings"]["default_rerun_safety"] == "safe"
    assert script["settings"]["logcat_scope"] == "device"
    assert script["settings"]["network_capture"] is False
    # 一事件一节点：两个 tap 不合并
    assert script["steps"][0]["params"]["target"] == _node_target()
    assert script["steps"][1]["params"]["duration_ms"] == 900
    assert script["steps"][2]["params"]["from"] == {"by": "point", "x": 540, "y": 1800}
    assert script["steps"][2]["params"]["to"] == {"by": "point", "x": 540, "y": 600}
    # 方向已从短名称移到 description，参数里仍保留起终点
    assert "方向=" in script["steps"][2]["description"]
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
    # 短名称：类型标签 + 同类型序号（顺序即事件顺序）
    assert [step["name"] for step in script["steps"]] == [
        "点击1",
        "长按1",
        "滑动1",
        "点击2",
        "输入1",
        "按键1",
        "按键2",
        "应用1",
    ]
    # 备注不再重复节点数（节点列已展示）
    assert script["notes"] == "由录制 rec_x 转换"
    assert "共" not in script["notes"]
    assert "个节点" not in script["notes"]
    assert any("坐标定位" in warning for warning in warnings)
    # app 事件现在生成节点（不再有「未生成节点」警告），并提示已还原操作间隔
    assert any("间隔" in warning for warning in warnings)
    # 疑似滚动只提示，不做合并
    assert any("已生成 swipe 节点" in warning for warning in warnings)
    assert not any("已合并为 swipe 节点" in warning for warning in warnings)


def test_short_name_unknown_type_falls_back_to_original() -> None:
    counters: dict[str, int] = {}
    assert convert._next_step_name("unknown-kind", "原名称", counters) == "原名称"
    assert counters == {}
    assert convert._next_step_name("click", "fallback", counters) == "点击1"
    assert convert._next_step_name("click", "fallback", counters) == "点击2"


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
    assert script["notes"] == "根据录制事件生成"
    assert "共" not in script["notes"]
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


# ---------------------------------------------------------------- 定位方式：默认坐标
def test_events_to_script_defaults_to_point_mode() -> None:
    """默认（point）：点击/长按只记绝对坐标（游戏/自绘 UI 唯一可靠写法）。"""
    events = [
        _event(1, "tap", {"x": 540, "y": 710, "duration_ms": 0, "node": _node_target()}),
        _event(2, "long_press", {"x": 300, "y": 400, "duration_ms": 900, "node": _node_target(by="text", value="删除")}),
        _event(3, "tap", {"x": 10, "y": 20, "duration_ms": 0}),
    ]
    script, warnings = convert.events_to_script(events, "坐标脚本")
    steps = script["steps"]
    assert [step["type"] for step in steps] == ["click", "long_click", "click"]
    assert steps[0]["params"]["target"] == {"by": "point", "x": 540, "y": 710}
    assert steps[1]["params"]["target"] == {"by": "point", "x": 300, "y": 400}
    assert steps[1]["params"]["duration_ms"] == 900
    assert steps[2]["params"]["target"] == {"by": "point", "x": 10, "y": 20}
    assert all(step["when"] is None for step in steps)
    assert any("绝对坐标" in warning for warning in warnings)
    assert validate_script(script)["valid"] is True


def test_point_mode_input_inserts_focus_click() -> None:
    """point 模式下输入节点：目标改为「先点坐标聚焦，再输入」。"""
    events = [
        _event(1, "tap", {"x": 667, "y": 557, "duration_ms": 0, "node": _node_target(by="text", value="搜索")}),
        _event(2, "input", {"text": "wlan", "node": _node_target(by="text", value="搜索")}),
    ]
    script, _warnings = convert.events_to_script(events, "输入脚本")
    steps = script["steps"]
    assert [step["type"] for step in steps] == ["click", "input"]
    assert [step["name"] for step in steps] == ["点击1", "输入1"]
    assert steps[1]["params"]["target"] is None
    assert steps[1]["params"]["text"] == "wlan"
    # 自动聚焦 click 与普通节点一样不带 when
    assert all(step["when"] is None for step in steps)


def test_point_mode_input_without_tap_inserts_auto_focus_click() -> None:
    """没有前置点击时自动补聚焦节点；自动聚焦节点的 when 也必须为 None。"""
    events = [_event(1, "input", {"text": "hello", "x": 540, "y": 710})]

    script, _warnings = convert.events_to_script(events, "自动聚焦脚本")

    steps = script["steps"]
    assert [step["type"] for step in steps] == ["click", "input"]
    # 自动补的聚焦点击计入点击序号
    assert [step["name"] for step in steps] == ["点击1", "输入1"]
    assert steps[0]["tags"] == ["auto-focus"]
    assert steps[0]["when"] is None
    assert steps[1]["when"] is None
    assert validate_script(script)["valid"] is True


def test_swipe_keeps_recorded_path_and_long_waits_are_not_truncated() -> None:
    """录制保真：轨迹保留；相邻操作间隔 8s 也要还原（旧实现被截断成 3s）。"""
    def event(seq: int, kind: str, payload: dict, created_at: str) -> dict:
        return {"seq": seq, "kind": kind, "payload": payload, "confidence": "high", "created_at": created_at}

    events = [
        event(1, "tap", {"x": 100, "y": 100, "duration_ms": 50}, "2026-01-01T00:00:00.000+00:00"),
        event(
            2,
            "swipe",
            {
                "from": {"x": 540, "y": 1800},
                "to": {"x": 540, "y": 600},
                "duration_ms": 300,
                "path": [
                    {"x": 540, "y": 1800, "dt_ms": 0},
                    {"x": 520, "y": 1500, "dt_ms": 60},
                    {"x": 560, "y": 1000, "dt_ms": 130},
                    {"x": 540, "y": 600, "dt_ms": 200},
                ],
            },
            "2026-01-01T00:00:00.000+00:00",
        ),
        event(3, "tap", {"x": 200, "y": 200, "duration_ms": 50}, "2026-01-01T00:00:08.000+00:00"),
    ]
    script, _warnings = convert.events_to_script(events, "轨迹脚本")
    steps = script["steps"]
    assert steps[1]["params"]["path"] == [
        {"x": 540, "y": 1800, "dt_ms": 0},
        {"x": 520, "y": 1500, "dt_ms": 60},
        {"x": 560, "y": 1000, "dt_ms": 130},
        {"x": 540, "y": 600, "dt_ms": 200},
    ]
    assert steps[2]["delay_before_ms"] == 8000
    assert validate_script(script)["valid"] is True


def test_circle_event_becomes_named_drag_with_path() -> None:
    """画圈事件：type=drag、短名称、画圈信息留在 description、path 完整保留。"""
    event = {
        "seq": 1,
        "kind": "drag",
        "confidence": "high",
        "created_at": "2026-01-01T00:00:00.000+00:00",
        "payload": {
            "from": {"x": 500, "y": 1000},
            "to": {"x": 500, "y": 1000},
            "duration_ms": 800,
            "path": [
                {"x": 500, "y": 1000, "dt_ms": 0},
                {"x": 600, "y": 1000, "dt_ms": 100},
                {"x": 600, "y": 1100, "dt_ms": 200},
                {"x": 500, "y": 1100, "dt_ms": 300},
                {"x": 500, "y": 1000, "dt_ms": 400},
            ],
        },
    }
    script, _warnings = convert.events_to_script([event], "画圈脚本")
    step = script["steps"][0]
    assert step["type"] == "drag"
    assert step["name"] == "拖拽1"
    assert "轨迹=画圈" in step["description"]
    assert len(step["params"]["path"]) == 5
