"""hierarchy.py 单测：uiautomator XML 解析（不依赖真机）。"""

from __future__ import annotations

import pytest

from app.android.hierarchy import flatten, parse_bounds, parse_xml, to_xml_string
from app.errors import ApiError

# 真实 uiautomator dump 风格样例 1：中文 text、空 text、content-desc、不可见节点、嵌套
SAMPLE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.example.app"
        content-desc="" checkable="false" checked="false" clickable="false" enabled="true"
        focusable="false" focused="false" scrollable="false" long-clickable="false" password="false"
        selected="false" bounds="[0,0][1080,2400]" visible-to-user="true">
    <node index="0" text="" resource-id="com.example.app:id/toolbar" class="android.widget.LinearLayout"
          package="com.example.app" content-desc="" clickable="false" enabled="true" focused="false"
          bounds="[0,80][1080,200]" visible-to-user="true">
      <node index="0" text="设置" resource-id="com.example.app:id/title"
            class="android.widget.TextView" package="com.example.app" content-desc=""
            clickable="false" enabled="true" bounds="[40,100][200,180]" visible-to-user="true" />
    </node>
    <node index="1" text="" resource-id="com.example.app:id/content" class="android.widget.FrameLayout"
          package="com.example.app" bounds="[0,200][1080,2400]" visible-to-user="true">
      <node index="0" text="" resource-id="com.example.app:id/et_search" class="android.widget.EditText"
            package="com.example.app" content-desc="搜索框" clickable="true" enabled="true"
            bounds="[40,240][1040,360]" visible-to-user="true" />
      <node index="1" text="确定" resource-id="com.example.app:id/btn_ok"
            class="android.widget.Button" package="com.example.app" content-desc="确认按钮"
            clickable="true" enabled="false" bounds="[800,1000][1040,1120]" visible-to-user="true" />
      <node index="2" text="隐藏项" resource-id="com.example.app:id/hidden"
            class="android.widget.TextView" package="com.example.app"
            bounds="[0,0][0,0]" visible-to-user="false" />
    </node>
  </node>
</hierarchy>
"""

# 样例 2：转义字符 + 换行 + 缺省属性（无 clickable/enabled/visible-to-user）
SAMPLE_XML_ESCAPED = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="1">
  <node index="0" text="Tom &amp; Jerry &lt;3 &quot;quoted&quot;" resource-id="com.example:id/a"
        class="android.widget.TextView" package="com.example" content-desc=""
        bounds="[-10,20][100,200]">
    <node index="0" text="第一行&#10;第二行" class="android.widget.TextView"
          bounds="[0,200][100,300]" />
  </node>
</hierarchy>
"""

MULTI_ROOT_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="左" class="android.widget.TextView" package="p1" bounds="[0,0][100,100]" />
  <node index="1" text="右" class="android.widget.TextView" package="p1" bounds="[200,100][400,300]" />
</hierarchy>
"""


def test_parse_basic_attributes() -> None:
    root = parse_xml(SAMPLE_XML)
    assert root.class_name == "android.widget.FrameLayout"
    assert root.package == "com.example.app"
    assert root.bounds == [0, 0, 1080, 2400]
    assert root.clickable is False
    assert root.enabled is True
    assert root.visible is True
    assert root.text == ""

    toolbar = root.children[0]
    assert toolbar.resource_id == "com.example.app:id/toolbar"
    assert toolbar.children[0].text == "设置"
    assert toolbar.children[0].class_name == "android.widget.TextView"
    assert toolbar.children[0].bounds == [40, 100, 200, 180]


def test_parse_children_content_desc_and_empty_text() -> None:
    root = parse_xml(SAMPLE_XML)
    content = root.children[1]
    search, button, hidden = content.children
    assert search.text == ""
    assert search.content_desc == "搜索框"
    assert search.clickable is True
    assert button.text == "确定"
    assert button.enabled is False
    assert button.content_desc == "确认按钮"
    assert hidden.visible is False
    assert hidden.bounds == [0, 0, 0, 0]


def test_flatten_order_and_count() -> None:
    root = parse_xml(SAMPLE_XML)
    nodes = flatten(root)
    assert len(nodes) == 7  # 根 + toolbar + title + content + et + btn + hidden
    # 先序（文档顺序）
    assert nodes[0] is root
    assert [n.text for n in nodes if n.text] == ["设置", "确定", "隐藏项"]
    assert nodes[1].resource_id == "com.example.app:id/toolbar"
    assert nodes[2].text == "设置"
    assert nodes[-1].resource_id == "com.example.app:id/hidden"
    assert flatten(None) == []


def test_parse_bounds_variants() -> None:
    assert parse_bounds("[100,640][980,780]") == [100, 640, 980, 780]
    assert parse_bounds("[0,0][0,0]") == [0, 0, 0, 0]
    assert parse_bounds(" [-5,6][7,8] ") == [-5, 6, 7, 8]
    assert parse_bounds("garbage") == [0, 0, 0, 0]
    assert parse_bounds("") == [0, 0, 0, 0]
    assert parse_bounds(None) == [0, 0, 0, 0]


def test_parse_escaped_text_and_missing_attrs() -> None:
    root = parse_xml(SAMPLE_XML_ESCAPED)
    assert root.text == 'Tom & Jerry <3 "quoted"'
    assert root.bounds == [-10, 20, 100, 200]
    # 缺省 clickable=False / enabled=True / visible=True
    assert root.clickable is False
    assert root.enabled is True
    assert root.visible is True
    assert root.children[0].text == "第一行\n第二行"
    assert root.children[0].resource_id == ""


def test_bom_and_whitespace() -> None:
    root = parse_xml("\ufeff  " + SAMPLE_XML)
    assert root.children[0].children[0].text == "设置"


def test_empty_hierarchy_returns_empty_root() -> None:
    for xml in ("", "   ", "<hierarchy rotation='0' />", "<?xml version='1.0' ?><hierarchy />"):
        root = parse_xml(xml)
        assert root.class_name == "hierarchy"
        assert root.children == []
        assert flatten(root) == [root]


def test_multi_root_synthetic_node() -> None:
    root = parse_xml(MULTI_ROOT_XML)
    assert root.class_name == "hierarchy"
    assert [child.text for child in root.children] == ["左", "右"]
    assert root.bounds == [0, 0, 400, 300]  # children bounds 并集
    assert len(flatten(root)) == 3


def test_malformed_xml_raises_internal() -> None:
    with pytest.raises(ApiError) as excinfo:
        parse_xml("<hierarchy><node text='x'></hierarchy>")
    assert excinfo.value.code == "INTERNAL"
    assert "invalid hierarchy xml" in excinfo.value.message


def test_unexpected_root_tag_raises_internal() -> None:
    with pytest.raises(ApiError) as excinfo:
        parse_xml("<foo><node /></foo>")
    assert excinfo.value.code == "INTERNAL"


def test_to_xml_string_roundtrip() -> None:
    root = parse_xml(SAMPLE_XML)
    xml = to_xml_string(root)
    again = parse_xml(xml)
    assert len(flatten(again)) == len(flatten(root)) == 7
    assert again.children[0].children[0].text == "设置"
    assert again.children[1].children[2].visible is False
    assert again.bounds == root.bounds


def test_to_xml_string_escapes_and_synthetic_root() -> None:
    root = parse_xml(SAMPLE_XML_ESCAPED)
    xml = to_xml_string(root)
    assert "&amp;" in xml and "&lt;3" in xml
    again = parse_xml(xml)
    assert again.text == 'Tom & Jerry <3 "quoted"'

    multi = parse_xml(MULTI_ROOT_XML)
    multi_xml = to_xml_string(multi)
    assert multi_xml.count("<node ") == 2  # 合成根不输出为 node
    assert parse_xml(multi_xml).children[1].text == "右"
