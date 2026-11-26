"""驱动接口（契约 §4）：协议 + 数据类 + FakeDriver。

- 真机实现见 u2_driver.py（be-driver）；引擎/API/录制一律依赖本文件的 Driver 协议；
- FakeDriver 是内存假设备：假层级树 + 全部动作记录 + 可编程 shell 响应，
  供引擎、API、录制、前端联调与单测使用，**不依赖真机与 adb**；
- 所有接口是同步的（uiautomator2 本身同步）；在线程池中调用。
"""

from __future__ import annotations

import base64
import struct
import time
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------- 数据类


@dataclass
class DeviceInfo:
    serial: str
    model: str = "fake"
    brand: str = "fake"
    android_version: str = "14"
    sdk: int = 34
    screen_width: int = 1080
    screen_height: int = 2400
    rotation: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "serial": self.serial,
            "model": self.model,
            "brand": self.brand,
            "android_version": self.android_version,
            "sdk": self.sdk,
            "screen_width": self.screen_width,
            "screen_height": self.screen_height,
            "rotation": self.rotation,
        }


@dataclass
class CommandResult:
    code: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class HierarchyNode:
    class_name: str = ""
    text: str = ""
    resource_id: str = ""
    content_desc: str = ""
    package: str = ""
    bounds: list[int] = field(default_factory=lambda: [0, 0, 0, 0])  # [x1,y1,x2,y2]
    clickable: bool = False
    enabled: bool = True
    visible: bool = True
    children: list[HierarchyNode] = field(default_factory=list)

    # ---- 便捷方法
    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    def iter_nodes(self) -> Iterator[HierarchyNode]:
        yield self
        for child in self.children:
            yield from child.iter_nodes()

    def to_dict(self) -> dict[str, Any]:
        return {
            "class_name": self.class_name,
            "text": self.text,
            "resource_id": self.resource_id,
            "content_desc": self.content_desc,
            "package": self.package,
            "bounds": list(self.bounds),
            "clickable": self.clickable,
            "enabled": self.enabled,
            "visible": self.visible,
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HierarchyNode:
        return cls(
            class_name=data.get("class_name", ""),
            text=data.get("text", ""),
            resource_id=data.get("resource_id", ""),
            content_desc=data.get("content_desc", ""),
            package=data.get("package", ""),
            bounds=list(data.get("bounds", [0, 0, 0, 0])),
            clickable=bool(data.get("clickable", False)),
            enabled=bool(data.get("enabled", True)),
            visible=bool(data.get("visible", True)),
            children=[cls.from_dict(c) for c in data.get("children", [])],
        )


# ---------------------------------------------------------------- 协议


@runtime_checkable
class Driver(Protocol):
    serial: str

    def alive(self) -> bool: ...
    def info(self) -> DeviceInfo: ...
    def wm_size(self) -> tuple[int, int]: ...
    def rotation(self) -> int: ...
    def dump_hierarchy(self) -> HierarchyNode: ...
    def current_app(self) -> dict[str, Any]: ...
    def screenshot(self) -> bytes: ...
    def click(self, x: int, y: int) -> None: ...
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None: ...
    def swipe_path(self, points: list[tuple[int, int]], duration_ms: int = 300) -> None: ...
    def long_press(self, x: int, y: int, duration_ms: int = 800) -> None: ...
    def key(self, name_or_code: str | int) -> None: ...
    def input_text(self, text: str) -> None: ...
    def clear_text(self) -> None: ...
    def hide_keyboard(self) -> None: ...
    def app_start(self, package: str, activity: str | None = None) -> None: ...
    def app_stop(self, package: str) -> None: ...
    def app_clear(self, package: str) -> None: ...
    def app_grant(self, package: str) -> list[str]: ...
    def shell(self, command: str, timeout_s: float = 10) -> CommandResult: ...
    def unlock(self) -> None: ...
    def close(self) -> None: ...


# key 名 -> Android keycode（与 schemas.KeyName 对齐 + 供 u2 实现复用）
KEY_CODES: dict[str, int] = {
    "back": 4,
    "home": 3,
    "recents": 187,
    "enter": 66,
    "delete": 67,
    "volume_up": 24,
    "volume_down": 25,
    "power": 26,
    "menu": 82,
    "notification": 83,
}


def resolve_key(name_or_code: str | int) -> int:
    if isinstance(name_or_code, int):
        return name_or_code
    name = str(name_or_code).strip().lower()
    if name in KEY_CODES:
        return KEY_CODES[name]
    if name.isdigit():
        return int(name)
    raise ValueError(f"unknown key: {name_or_code}")


# ---------------------------------------------------------------- FakeDriver

# 1x1 红色 PNG（合法 PNG，供截图产物测试）
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


def _fake_tree() -> HierarchyNode:
    """一张稳定的假 UI：状态栏 + 布局 + 登录按钮 + 账号输入框 + 列表项。"""
    return HierarchyNode(
        class_name="android.widget.FrameLayout",
        package="com.example.fake",
        bounds=[0, 0, 1080, 2400],
        children=[
            HierarchyNode(
                class_name="android.widget.LinearLayout",
                resource_id="com.example.fake:id/root",
                bounds=[0, 120, 1080, 2400],
                children=[
                    HierarchyNode(
                        class_name="android.widget.EditText",
                        resource_id="com.example.fake:id/et_account",
                        bounds=[100, 300, 980, 420],
                        clickable=True,
                    ),
                    HierarchyNode(
                        class_name="android.widget.EditText",
                        resource_id="com.example.fake:id/et_password",
                        bounds=[100, 460, 980, 580],
                        clickable=True,
                    ),
                    HierarchyNode(
                        class_name="android.widget.Button",
                        text="登录",
                        resource_id="com.example.fake:id/btn_login",
                        bounds=[100, 640, 980, 780],
                        clickable=True,
                    ),
                    HierarchyNode(
                        class_name="android.widget.TextView",
                        text="忘记密码",
                        bounds=[100, 820, 400, 890],
                    ),
                    HierarchyNode(
                        class_name="android.widget.ListView",
                        resource_id="com.example.fake:id/list",
                        bounds=[0, 950, 1080, 2300],
                        children=[
                            HierarchyNode(
                                class_name="android.widget.TextView",
                                text=f"列表项{i}",
                                content_desc=f"item{i}",
                                bounds=[40, 980 + i * 140, 1040, 1100 + i * 140],
                                clickable=True,
                            )
                            for i in range(3)
                        ],
                    ),
                ],
            )
        ],
    )


@dataclass
class CallRecord:
    op: str
    kwargs: dict[str, Any]
    t: float = field(default_factory=time.time)


class FakeDriver:
    """内存假设备。测试里可以：改层级树、预设 shell 响应、断言动作序列。"""

    def __init__(self, serial: str = "fake-123") -> None:
        self.serial = serial
        self._info = DeviceInfo(serial=serial)
        self._tree = _fake_tree()
        self.calls: list[CallRecord] = []
        self.shell_responses: dict[str, CommandResult] = {
            "echo hello": CommandResult(0, "hello\n", ""),
            "wm size": CommandResult(0, "Physical size: 1080x2400\n", ""),
        }
        self.foreground = {"package": "com.example.fake", "activity": ".MainActivity"}
        self._screen_text: dict[tuple[int, int], str] = {}  # (x,y) -> 输入的文本
        self._closed = False
        self.fail_on: set[str] = set()  # op 名集合，命中则 raise

    # ---- 测试辅助
    def set_hierarchy(self, tree: HierarchyNode | dict[str, Any]) -> None:
        self._tree = tree if isinstance(tree, HierarchyNode) else HierarchyNode.from_dict(tree)

    def set_info(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self._info, key, value)

    def ops(self) -> list[str]:
        return [c.op for c in self.calls]

    def calls_of(self, op: str) -> list[CallRecord]:
        return [c for c in self.calls if c.op == op]

    def _record(self, op: str, **kwargs: Any) -> None:
        if op in self.fail_on:
            raise RuntimeError(f"FakeDriver: injected failure on {op}")
        self.calls.append(CallRecord(op=op, kwargs=kwargs))

    def _require_alive(self) -> None:
        if self._closed:
            raise RuntimeError("FakeDriver: device closed")

    # ---- Driver 实现
    def alive(self) -> bool:
        return not self._closed

    def info(self) -> DeviceInfo:
        self._require_alive()
        return self._info

    def wm_size(self) -> tuple[int, int]:
        self._require_alive()
        return self._info.screen_width, self._info.screen_height

    def rotation(self) -> int:
        self._require_alive()
        return self._info.rotation

    def dump_hierarchy(self) -> HierarchyNode:
        self._require_alive()
        self._record("dump_hierarchy")
        return self._tree

    def current_app(self) -> dict[str, Any]:
        self._require_alive()
        return dict(self.foreground)

    def screenshot(self) -> bytes:
        self._require_alive()
        self._record("screenshot")
        return _TINY_PNG

    def click(self, x: int, y: int) -> None:
        self._require_alive()
        self._record("click", x=x, y=y)

    def swipe_path(self, points: list[tuple[int, int]], duration_ms: int = 300) -> None:
        self._require_alive()
        self._record("swipe_path", points=list(points), duration_ms=duration_ms)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self._require_alive()
        self._record("swipe", x1=x1, y1=y1, x2=x2, y2=y2, duration_ms=duration_ms)

    def long_press(self, x: int, y: int, duration_ms: int = 800) -> None:
        self._require_alive()
        self._record("long_press", x=x, y=y, duration_ms=duration_ms)

    def key(self, name_or_code: str | int) -> None:
        self._require_alive()
        code = resolve_key(name_or_code)
        self._record("key", keycode=code)
        if code == 4:  # back: 前台变桌面
            self.foreground = {"package": "com.android.launcher", "activity": None}

    def input_text(self, text: str) -> None:
        self._require_alive()
        self._record("input_text", text=text)

    def clear_text(self) -> None:
        self._require_alive()
        self._record("clear_text")

    def hide_keyboard(self) -> None:
        self._require_alive()
        self._record("hide_keyboard")

    def app_start(self, package: str, activity: str | None = None) -> None:
        self._require_alive()
        self._record("app_start", package=package, activity=activity)
        self.foreground = {"package": package, "activity": activity}

    def app_stop(self, package: str) -> None:
        self._require_alive()
        self._record("app_stop", package=package)
        if self.foreground.get("package") == package:
            self.foreground = {"package": "com.android.launcher", "activity": None}

    def app_clear(self, package: str) -> None:
        self._require_alive()
        self._record("app_clear", package=package)

    def app_grant(self, package: str) -> list[str]:
        self._require_alive()
        self._record("app_grant", package=package)
        return [f"{package}.permission.FAKE"]

    def shell(self, command: str, timeout_s: float = 10) -> CommandResult:
        self._require_alive()
        self._record("shell", command=command)
        if command in self.shell_responses:
            import copy

            return copy.deepcopy(self.shell_responses[command])
        # echo 前缀自动响应
        if command.startswith("echo "):
            return CommandResult(0, command[5:] + "\n", "")
        return CommandResult(0, "", "")

    def unlock(self) -> None:
        self._record("unlock")

    def close(self) -> None:
        self._closed = True


# ---------------------------------------------------------------- PNG CRC 工具（截图产物测试用）
def png_dimensions(png_bytes: bytes) -> tuple[int, int]:
    if len(png_bytes) < 24 or png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a png")
    width, height = struct.unpack(">II", png_bytes[16:24])
    return width, height


def _selftest() -> None:  # pragma: no cover
    d = FakeDriver()
    assert d.alive()
    assert d.wm_size() == (1080, 2400)
    tree = d.dump_hierarchy()
    texts = [n.text for n in tree.iter_nodes() if n.text]
    assert "登录" in texts, texts
    d.click(540, 710)
    assert d.ops()[-1] == "click"
    r = d.shell("echo hi")
    assert r.stdout.strip() == "hi"
    assert zlib.crc32(d.screenshot()) > 0
    assert png_dimensions(d.screenshot()) == (1, 1)
    print("FakeDriver selftest OK")


if __name__ == "__main__":  # pragma: no cover
    _selftest()
