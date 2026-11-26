"""u2_driver.py 单测：只验证 import / 懒连接 / 无设备语义（不接真机）。"""

from __future__ import annotations

import pytest
import uiautomator2

from app.android import u2_driver
from app.android.driver import Driver
from app.android.u2_driver import U2Driver, create_u2_driver
from app.errors import ApiError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def test_import_and_protocol() -> None:
    driver = create_u2_driver("serial-abc")
    assert isinstance(driver, U2Driver)
    assert isinstance(driver, Driver)  # 满足 Driver 协议
    assert driver.serial == "serial-abc"
    assert driver.KEY_CODES["back"] == 4


def test_init_is_lazy_no_connect(monkeypatch) -> None:
    calls: list[str] = []

    def fake_connect(serial):  # pragma: no cover - 不应被调用
        calls.append(serial)
        raise AssertionError("construction must not connect")

    monkeypatch.setattr(uiautomator2, "connect", fake_connect)
    driver = U2Driver("serial-lazy")
    assert calls == []
    assert driver._device_obj is None


def test_alive_without_device_returns_false(monkeypatch) -> None:
    calls: list[str] = []

    def fake_connect(serial):
        calls.append(serial)
        raise uiautomator2.ConnectError(f"device {serial} not online")

    monkeypatch.setattr(uiautomator2, "connect", fake_connect)
    driver = U2Driver("ghost-serial")
    assert driver.alive() is False  # 不抛，返回 False
    # 连接失败不缓存，下次仍会重试
    assert driver.alive() is False
    assert calls == ["ghost-serial", "ghost-serial"]


def test_alive_true_with_fake_device(monkeypatch) -> None:
    class FakeU2Device:
        def window_size(self):
            return (1080, 2400)

    monkeypatch.setattr(uiautomator2, "connect", lambda serial: FakeU2Device())
    driver = U2Driver("serial-ok")
    assert driver.alive() is True
    # 懒连接只发生一次
    device = driver.device
    assert driver.device is device


def test_closed_driver(monkeypatch) -> None:
    class FakeU2Device:
        def window_size(self):
            return (1080, 2400)

    monkeypatch.setattr(uiautomator2, "connect", lambda serial: FakeU2Device())
    driver = U2Driver("serial-close")
    assert driver.alive() is True
    driver.close()
    assert driver.alive() is False
    with pytest.raises(ApiError) as excinfo:
        driver.info()
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"


def test_screenshot_uses_adb(monkeypatch) -> None:
    calls: list[tuple] = []

    def fake_shell_bytes(serial, command, timeout_s=30):
        calls.append((serial, command, timeout_s))
        return PNG

    monkeypatch.setattr(u2_driver.adb, "shell_bytes", fake_shell_bytes)
    driver = U2Driver("serial-shot")
    assert driver.screenshot() == PNG
    assert calls == [("serial-shot", ["screencap", "-p"], 20)]


def test_missing_device_actions_raise_device_unavailable(monkeypatch) -> None:
    def fake_connect(serial):
        raise uiautomator2.ConnectError("not online")

    monkeypatch.setattr(uiautomator2, "connect", fake_connect)
    driver = U2Driver("ghost")
    with pytest.raises(ApiError) as excinfo:
        driver.click(1, 2)
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"


# ---------------------------------------------------------------- adb-first 注入
# 背景：u2 的 click/swipe/press 在 Unity/常驻动画界面上会同步等界面空闲 ~10s，
# 超过默认 8s 节点超时；驱动改为 adb input 优先（~0.1s），失败再回落 u2。

def _install_fake_adb(monkeypatch, calls, *, fail=False):
    from types import SimpleNamespace

    def fake_shell(serial, command, timeout_s=15):
        calls.append((serial, list(command), timeout_s))
        if fail:
            raise RuntimeError("adb is gone")
        return SimpleNamespace(code=0, stdout="", stderr="")

    monkeypatch.setattr(u2_driver.adb, "shell", fake_shell)


def test_click_uses_adb_input_tap(monkeypatch) -> None:
    calls: list[tuple] = []
    _install_fake_adb(monkeypatch, calls)
    driver = U2Driver("serial-click")
    driver.click(10, 20)
    assert calls == [("serial-click", ["input", "tap", "10", "20"], 15)]


def test_click_falls_back_to_u2_when_adb_fails(monkeypatch) -> None:
    calls: list[tuple] = []
    _install_fake_adb(monkeypatch, calls, fail=True)
    clicks: list[tuple[int, int]] = []

    class FakeU2Device:
        def click(self, x, y):
            clicks.append((x, y))

    monkeypatch.setattr(uiautomator2, "connect", lambda serial: FakeU2Device())
    driver = U2Driver("serial-fallback")
    driver.click(3, 4)
    assert clicks == [(3, 4)]


def test_swipe_uses_adb_input_swipe(monkeypatch) -> None:
    calls: list[tuple] = []
    _install_fake_adb(monkeypatch, calls)
    driver = U2Driver("serial-swipe")
    driver.swipe(1, 2, 3, 4, duration_ms=250)
    assert calls == [("serial-swipe", ["input", "swipe", "1", "2", "3", "4", "250"], 15)]


def test_long_press_uses_adb_swipe_at_same_point(monkeypatch) -> None:
    calls: list[tuple] = []
    _install_fake_adb(monkeypatch, calls)
    driver = U2Driver("serial-long")
    driver.long_press(100, 200, duration_ms=800)
    assert calls == [("serial-long", ["input", "swipe", "100", "200", "100", "200", "800"], 15)]


def test_swipe_path_falls_back_to_two_point_adb_swipe(monkeypatch) -> None:
    calls: list[tuple] = []
    _install_fake_adb(monkeypatch, calls)
    driver = U2Driver("serial-path")
    driver.swipe_path([(0, 0), (50, 60), (100, 120)], duration_ms=300)
    assert calls == [("serial-path", ["input", "swipe", "0", "0", "100", "120", "300"], 15)]


def test_key_uses_adb_keyevent(monkeypatch) -> None:
    calls: list[tuple] = []
    _install_fake_adb(monkeypatch, calls)
    driver = U2Driver("serial-key")
    driver.key("back")
    assert calls == [("serial-key", ["input", "keyevent", "4"], 15)]


def test_clear_text_uses_single_adb_keyevent(monkeypatch) -> None:
    calls: list[tuple] = []
    _install_fake_adb(monkeypatch, calls)

    class FakeU2Device:
        def dump_hierarchy(self):
            return (
                '<hierarchy><node class="android.widget.EditText" focused="true" '
                'text="abc" bounds="[0,0][10,10]" /></hierarchy>'
            ).encode()

    monkeypatch.setattr(uiautomator2, "connect", lambda serial: FakeU2Device())
    driver = U2Driver("serial-clear")
    driver.clear_text()
    # 移到末尾 + len("abc")+1 次 DEL，合并成一次 adb 调用
    assert calls == [("serial-clear", ["input", "keyevent", "123", "67", "67", "67", "67"], 20)]


# ---------------------------------------------------------------- 清数据后的权限
DUMPSYS_PACKAGE = """Packages:
  Package [com.demo.app] (abcd):
    userId=10123
    requested permissions:
      android.permission.POST_NOTIFICATIONS
    install permissions:
      android.permission.INTERNET: granted=true
    runtime permissions:
      android.permission.POST_NOTIFICATIONS: granted=false, flags=[ USER_SENSITIVE]
      android.permission.READ_EXTERNAL_STORAGE: granted=false
      com.huawei.permission.ACCESS_APPROXIMATELY_LOCATION: granted=false

    enabledComponents:
"""


def _install_permission_adb(monkeypatch, calls, cleared):
    from types import SimpleNamespace

    def fake_shell(serial, command, timeout_s=15):
        calls.append((serial, list(command), timeout_s))
        if list(command)[:2] == ["dumpsys", "package"]:
            return SimpleNamespace(code=0, stdout=DUMPSYS_PACKAGE, stderr="")
        return SimpleNamespace(code=0, stdout="", stderr="")

    monkeypatch.setattr(u2_driver.adb, "shell", fake_shell)

    class FakeU2Device:
        def app_clear(self, package):
            cleared.append(package)

    monkeypatch.setattr(uiautomator2, "connect", lambda serial: FakeU2Device())


def test_app_clear_regrants_declared_runtime_permissions(monkeypatch) -> None:
    calls: list[tuple] = []
    cleared: list[str] = []
    _install_permission_adb(monkeypatch, calls, cleared)
    driver = U2Driver("serial-clear-perm")
    driver.app_clear("com.demo.app")

    assert cleared == ["com.demo.app"]
    assert calls[0][1] == ["dumpsys", "package", "com.demo.app"]
    grants = [call[1] for call in calls if call[1][:2] == ["pm", "grant"]]
    assert grants == [
        ["pm", "grant", "com.demo.app", "android.permission.POST_NOTIFICATIONS"],
        ["pm", "grant", "com.demo.app", "android.permission.READ_EXTERNAL_STORAGE"],
        ["pm", "grant", "com.demo.app", "com.huawei.permission.ACCESS_APPROXIMATELY_LOCATION"],
    ]


def test_app_grant_returns_granted_permissions(monkeypatch) -> None:
    calls: list[tuple] = []
    cleared: list[str] = []
    _install_permission_adb(monkeypatch, calls, cleared)
    driver = U2Driver("serial-grant")
    granted = driver.app_grant("com.demo.app")
    assert granted == [
        "android.permission.POST_NOTIFICATIONS",
        "android.permission.READ_EXTERNAL_STORAGE",
        "com.huawei.permission.ACCESS_APPROXIMATELY_LOCATION",
    ]
    assert cleared == []  # 授权不动数据


# ---------------------------------------------------------------- 收起键盘
def test_hide_keyboard_sends_back_only_when_ime_shown(monkeypatch) -> None:
    from types import SimpleNamespace

    def _install(ime_shown: bool, calls):
        def fake_shell(serial, command, timeout_s=15):
            calls.append((serial, list(command), timeout_s))
            shown = "mInputShown=true" if ime_shown else "mInputShown=false"
            return SimpleNamespace(code=0, stdout=f"mCurMethodId=abc\n{shown}\n", stderr="")

        monkeypatch.setattr(u2_driver.adb, "shell", fake_shell)

    shown_calls: list[tuple] = []
    _install(True, shown_calls)
    U2Driver("serial-ime").hide_keyboard()
    assert [call[1] for call in shown_calls] == [
        ["dumpsys", "input_method"],
        ["input", "keyevent", "4"],
    ]

    hidden_calls: list[tuple] = []
    _install(False, hidden_calls)
    U2Driver("serial-ime2").hide_keyboard()
    assert [call[1] for call in hidden_calls] == [["dumpsys", "input_method"]]


# ---------------------------------------------------------------- 文本输入
def test_input_text_ascii_uses_adb_input(monkeypatch) -> None:
    calls: list[tuple] = []
    _install_fake_adb(monkeypatch, calls)
    driver = U2Driver("serial-text")
    driver.input_text("test123456")
    assert calls == [("serial-text", ["input", "text", "test123456"], 15)]


def test_input_text_ascii_escapes_spaces_and_shell_chars(monkeypatch) -> None:
    calls: list[tuple] = []
    _install_fake_adb(monkeypatch, calls)
    driver = U2Driver("serial-text-escape")
    driver.input_text("a b&c")
    assert calls == [("serial-text-escape", ["input", "text", "'a%sb&c'"], 15)]


def test_input_text_ascii_falls_back_to_u2_send_keys(monkeypatch) -> None:
    calls: list[tuple] = []
    _install_fake_adb(monkeypatch, calls, fail=True)
    sent: list[str] = []

    class FakeU2Device:
        def send_keys(self, text):
            sent.append(text)

    monkeypatch.setattr(uiautomator2, "connect", lambda serial: FakeU2Device())
    driver = U2Driver("serial-text-fallback")
    driver.input_text("abc")
    assert sent == ["abc"]


def test_input_text_unicode_uses_accessibility_set_text(monkeypatch) -> None:
    calls: list[tuple] = []
    _install_fake_adb(monkeypatch, calls)
    texts: list[str] = []

    class _Selector:
        def set_text(self, text):
            texts.append(text)

    class FakeU2Device:
        def __call__(self, **kwargs):
            assert kwargs == {"focused": True}
            return _Selector()

    monkeypatch.setattr(uiautomator2, "connect", lambda serial: FakeU2Device())
    driver = U2Driver("serial-text-cn")
    driver.input_text("中文abc")
    assert texts == ["中文abc"]
    assert calls == []  # 不走 adb input text（不支持非 ASCII）


# ---------------------------------------------------------------- 截图重试
def test_screenshot_retries_invalid_screencap(monkeypatch) -> None:
    PNG_OK = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    calls: list[int] = []

    def fake_shell_bytes(serial, command, timeout_s=30):
        calls.append(1)
        if len(calls) == 1:
            return b""  # 第一次空数据
        return PNG_OK

    monkeypatch.setattr(u2_driver.adb, "shell_bytes", fake_shell_bytes)
    driver = U2Driver("serial-shot-retry")
    assert driver.screenshot() == PNG_OK
    assert len(calls) == 2


def test_screenshot_falls_back_to_u2_when_adb_always_bad(monkeypatch) -> None:
    PNG_OK = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8

    monkeypatch.setattr(u2_driver.adb, "shell_bytes", lambda *a, **k: b"garbage")
    shots: list[str] = []

    class FakeU2Device:
        def screenshot(self, format="pillow"):
            from PIL import Image

            shots.append(format)
            return Image.new("RGB", (2, 2), (0, 0, 0))

    monkeypatch.setattr(uiautomator2, "connect", lambda serial: FakeU2Device())
    driver = U2Driver("serial-shot-fallback")
    shot = driver.screenshot()
    assert shot.startswith(b"\x89PNG\r\n\x1a\n")
    assert shots == ["pillow"]
