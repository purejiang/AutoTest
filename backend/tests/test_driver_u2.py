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
