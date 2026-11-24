"""selectors.py 单测：Target 匹配 / index / xpath / 轮询等待（FakeDriver，不依赖真机）。"""

from __future__ import annotations

import time

import pytest

from app.android import hierarchy
from app.android.driver import FakeDriver, HierarchyNode
from app.android.hierarchy import parse_xml, to_xml_string
from app.android.selectors import (
    ResolvedTarget,
    TargetNotFound,
    check_condition,
    match_nodes,
    resolve_target,
    select_node,
    wait_for,
)
from app.errors import ApiError
from app.schemas import Target

# 对 FakeDriver 的假树做一次 XML 往返，确保走真实解析路径
_FAKE_TREE = parse_xml(to_xml_string(FakeDriver().dump_hierarchy()))


@pytest.fixture()
def driver() -> FakeDriver:
    return FakeDriver()


def _by(**kwargs) -> Target:
    return Target(**kwargs)


def test_text_match_center() -> None:
    nodes = match_nodes(_FAKE_TREE, _by(by="text", value="登录"))
    assert len(nodes) == 1
    assert nodes[0].resource_id == "com.example.fake:id/btn_login"
    assert nodes[0].center == (540, 710)


def test_text_strip_equality() -> None:
    root = HierarchyNode(
        class_name="root",
        bounds=[0, 0, 100, 100],
        children=[HierarchyNode(text="  登录  ", bounds=[10, 10, 20, 20])],
    )
    assert len(match_nodes(root, _by(by="text", value="登录"))) == 1
    # 精确匹配本身不含"登"；降级 text_contains 兜底会命中，这是刻意设计（见模块 docstring）
    assert len(match_nodes(root, _by(by="text", value="登"))) == 1


def test_resource_id_content_desc_class_name() -> None:
    rid = match_nodes(_FAKE_TREE, _by(by="resource_id", value="com.example.fake:id/et_account"))
    assert len(rid) == 1 and rid[0].class_name == "android.widget.EditText"

    desc = match_nodes(_FAKE_TREE, _by(by="content_desc", value="item1"))
    assert len(desc) == 1 and desc[0].text == "列表项1"

    # 简名后缀匹配 + 全名匹配
    short = match_nodes(_FAKE_TREE, _by(by="class_name", value="EditText"))
    assert len(short) == 2
    full = match_nodes(
        _FAKE_TREE, _by(by="class_name", value="android.widget.Button")
    )
    assert len(full) == 1 and full[0].text == "登录"


def test_index_selection_and_out_of_range() -> None:
    nodes = match_nodes(_FAKE_TREE, _by(by="class_name", value="EditText"))
    assert select_node(_FAKE_TREE, _by(by="class_name", value="EditText", index=1)) is nodes[1]

    with pytest.raises(TargetNotFound) as excinfo:
        select_node(_FAKE_TREE, _by(by="class_name", value="EditText", index=5))
    assert "越界" in str(excinfo.value)


def test_not_found_returns_empty_and_select_none() -> None:
    target = _by(by="text", value="不存在的按钮")
    assert match_nodes(_FAKE_TREE, target) == []
    assert select_node(_FAKE_TREE, target) is None
    with pytest.raises(TargetNotFound):
        resolve_target(FakeDriver(), target, timeout_ms=0)


def test_text_contains_case_insensitive() -> None:
    root = HierarchyNode(
        class_name="root",
        bounds=[0, 0, 200, 200],
        children=[
            HierarchyNode(text="Hello World", bounds=[0, 0, 100, 100]),
            HierarchyNode(text="第二行", bounds=[100, 100, 200, 200]),
        ],
    )
    assert len(match_nodes(root, _by(by="text_contains", value="  hello  "))) == 1
    assert len(match_nodes(root, _by(by="text_contains", value="WORLD"))) == 1
    assert match_nodes(root, _by(by="text_contains", value="hello world 第二行")) == []


def test_point_returns_immediately() -> None:
    target = _by(by="point", x=12, y=34)
    assert match_nodes(_FAKE_TREE, target) == []
    resolved = resolve_target(FakeDriver(), target, timeout_ms=5000)
    assert resolved == ResolvedTarget(x=12, y=34, node=None, bounds=[12, 34, 12, 34])


def test_resolve_target_element_center(driver: FakeDriver) -> None:
    resolved = resolve_target(driver, _by(by="text", value="列表项2"))
    assert resolved.node is not None
    assert resolved.bounds == [40, 1260, 1040, 1380]
    assert (resolved.x, resolved.y) == (540, 1320)


def test_priority_fallback_when_explicit_by_misses() -> None:
    # by=text 给的是 resource-id，显式方式无命中 -> 按 text>resource_id>... 兜底
    nodes = match_nodes(_FAKE_TREE, _by(by="text", value="com.example.fake:id/btn_login"))
    assert len(nodes) == 1 and nodes[0].text == "登录"


def test_explicit_by_wins_over_priority() -> None:
    root = HierarchyNode(
        class_name="root",
        bounds=[0, 0, 200, 200],
        children=[
            HierarchyNode(text="双胞胎", bounds=[0, 0, 100, 100]),
            HierarchyNode(content_desc="双胞胎", bounds=[100, 100, 200, 200]),
        ],
    )
    # 显式 by=content_desc 应命中 content_desc 节点，而不是优先拿 text
    nodes = match_nodes(root, _by(by="content_desc", value="双胞胎"))
    assert nodes[0].content_desc == "双胞胎"
    # 无显式 by 时按 text 优先
    fallback = match_nodes(root, _by(by="class_name", value="不存在"))
    assert fallback == []


def test_invisible_nodes_are_skipped() -> None:
    root = HierarchyNode(
        class_name="root",
        bounds=[0, 0, 100, 100],
        children=[HierarchyNode(text="幽灵", visible=False, bounds=[0, 0, 10, 10])],
    )
    assert match_nodes(root, _by(by="text", value="幽灵")) == []
    assert check_condition(_make_driver(root), _by(by="text", value="幽灵")) is False


def _make_driver(root: HierarchyNode) -> FakeDriver:
    fake = FakeDriver()
    fake.set_hierarchy(root)
    return fake


def test_xpath_forms() -> None:
    assert len(match_nodes(_FAKE_TREE, _by(by="xpath", value="//android.widget.Button"))) == 1
    by_id = match_nodes(
        _FAKE_TREE, _by(by="xpath", value="//*[@resource-id='com.example.fake:id/btn_login']")
    )
    assert len(by_id) == 1 and by_id[0].text == "登录"
    by_text = match_nodes(_FAKE_TREE, _by(by="xpath", value='//*[@text="登录"]'))
    assert len(by_text) == 1
    # 位置谓词（1 基）+ 简名匹配
    second_text = match_nodes(_FAKE_TREE, _by(by="xpath", value="//TextView[2]"))
    assert len(second_text) == 1 and second_text[0].text == "列表项0"
    indexed = match_nodes(_FAKE_TREE, _by(by="xpath", value="//android.widget.TextView[3]"))
    assert len(indexed) == 1 and indexed[0].text == "列表项1"
    assert match_nodes(_FAKE_TREE, _by(by="xpath", value="//android.widget.TextView[99]")) == []


def test_xpath_unsupported_forms_raise() -> None:
    for bad in (
        "android.widget.Button",
        "//android.widget.FrameLayout/node",
        "//node[contains(@text,'x')]",
        "//node[1][2]",
        "//node[@class='x' and @text='y']",
    ):
        with pytest.raises(ApiError) as excinfo:
            match_nodes(_FAKE_TREE, _by(by="xpath", value=bad))
        assert excinfo.value.code == "UNSUPPORTED", bad


def test_resolve_target_polls_until_timeout() -> None:
    target = _by(by="text", value="不存在的按钮")
    start = time.monotonic()
    with pytest.raises(TargetNotFound):
        resolve_target(FakeDriver(), target, timeout_ms=180, poll_interval_ms=30)
    elapsed = time.monotonic() - start
    assert elapsed >= 0.15
    assert elapsed < 2.0


def test_resolve_target_timeout_message() -> None:
    target = _by(by="text", value="不存在的按钮")
    with pytest.raises(TargetNotFound) as excinfo:
        resolve_target(FakeDriver(), target, timeout_ms=60, poll_interval_ms=20)
    assert "不存在的按钮" in str(excinfo.value)


def test_resolve_target_abort() -> None:
    target = _by(by="text", value="不存在的按钮")
    start = time.monotonic()
    with pytest.raises(TargetNotFound) as excinfo:
        resolve_target(
            FakeDriver(), target, timeout_ms=5000, poll_interval_ms=350, should_abort=lambda: True
        )
    assert str(excinfo.value) == "aborted"
    assert time.monotonic() - start < 1.0


def test_check_condition_visible_and_gone() -> None:
    driver = FakeDriver()
    assert check_condition(driver, _by(by="text", value="登录"), "visible") is True
    assert check_condition(driver, _by(by="text", value="登录"), "gone") is False
    assert check_condition(driver, _by(by="text", value="不存在"), "visible") is False
    assert check_condition(driver, _by(by="text", value="不存在"), "gone") is True
    point = _by(by="point", x=1, y=2)
    assert check_condition(driver, point, "visible") is True
    assert check_condition(driver, point, "gone") is False
    with pytest.raises(ApiError):
        check_condition(driver, point, "bogus")


def test_dump_failure_never_satisfies_gone(monkeypatch) -> None:
    """层级抓取失败 = 观测未知：gone 不能被误判为满足（否则 not_exists/wait(gone) 会假通过）。"""
    driver = FakeDriver()

    def boom() -> None:
        raise RuntimeError("u2 dump failed")

    monkeypatch.setattr(driver, "dump_hierarchy", boom)
    target = _by(by="text", value="不存在")
    assert check_condition(driver, target, "visible") is False
    assert check_condition(driver, target, "gone") is False
    assert wait_for(driver, target, "gone", 80, poll_interval_ms=20) is False


def test_wait_for_timeout_returns_false_fast() -> None:
    start = time.monotonic()
    result = wait_for(FakeDriver(), _by(by="text", value="不存在"), "visible", 150, poll_interval_ms=30)
    assert result is False
    assert time.monotonic() - start < 2.0


def test_wait_for_success_and_gone() -> None:
    driver = FakeDriver()
    assert wait_for(driver, _by(by="text", value="登录"), "visible", 1000) is True
    assert wait_for(driver, _by(by="text", value="不存在"), "gone", 1000) is True


def test_wait_for_abort_returns_false_fast() -> None:
    start = time.monotonic()
    assert (
        wait_for(
            FakeDriver(),
            _by(by="text", value="不存在"),
            "visible",
            5000,
            poll_interval_ms=350,
            should_abort=lambda: True,
        )
        is False
    )
    assert time.monotonic() - start < 1.0


def test_wait_for_state_visible_uses_parsed_xml() -> None:
    driver = FakeDriver()
    assert wait_for(driver, _by(by="resource_id", value="com.example.fake:id/btn_login"), "visible", 0)
    assert not wait_for(driver, _by(by="resource_id", value="com.example.fake:id/nope"), "visible", 0)


def test_dump_failure_is_tolerated_while_polling() -> None:
    class BrokenDriver:
        serial = "broken"

        def __init__(self) -> None:
            self.calls = 0
            self.good = FakeDriver()

        def dump_hierarchy(self):
            self.calls += 1
            if self.calls == 1:
                raise ApiError("DEVICE_UNAVAILABLE", "transient")
            return hierarchy.parse_xml(to_xml_string(self.good.dump_hierarchy()))

    broken = BrokenDriver()
    resolved = resolve_target(broken, _by(by="text", value="登录"), timeout_ms=1000, poll_interval_ms=10)
    assert resolved.node is not None and resolved.node.text == "登录"
    assert broken.calls >= 2
