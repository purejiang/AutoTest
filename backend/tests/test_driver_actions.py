"""actions.py 单测：对 FakeDriver 的薄封装与错误归一化。"""

from __future__ import annotations

import pytest

from app.android import actions
from app.android.driver import FakeDriver
from app.errors import ApiError


@pytest.fixture()
def driver() -> FakeDriver:
    return FakeDriver()


def test_tap(driver: FakeDriver) -> None:
    actions.tap(driver, 540, 710)
    call = driver.calls_of("click")[-1]
    assert call.kwargs == {"x": 540, "y": 710}


def test_swipe(driver: FakeDriver) -> None:
    actions.swipe(driver, (10, 20), (30, 40), duration_ms=250)
    kwargs = driver.calls_of("swipe")[-1].kwargs
    assert kwargs == {"x1": 10, "y1": 20, "x2": 30, "y2": 40, "duration_ms": 250}


def test_swipe_rejects_bad_point(driver: FakeDriver) -> None:
    with pytest.raises(ApiError) as excinfo:
        actions.swipe(driver, (1, 2, 3), (4, 5))
    assert excinfo.value.code == "INVALID_ARGUMENT"
    assert driver.calls == []


def test_drag_falls_back_to_swipe(driver: FakeDriver) -> None:
    actions.drag(driver, (0, 0), (100, 100), duration_ms=1000)
    kwargs = driver.calls_of("swipe")[-1].kwargs
    assert kwargs["duration_ms"] == 1000
    assert driver.calls_of("drag") == []


def test_drag_uses_driver_drag_when_available() -> None:
    class DragDriver(FakeDriver):
        def drag(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 500) -> None:
            self._record("drag", x1=x1, y1=y1, x2=x2, y2=y2, duration_ms=duration_ms)

    driver = DragDriver()
    actions.drag(driver, (1, 2), (3, 4), duration_ms=600)
    assert driver.calls_of("drag")[-1].kwargs == {
        "x1": 1,
        "y1": 2,
        "x2": 3,
        "y2": 4,
        "duration_ms": 600,
    }


def test_long_press(driver: FakeDriver) -> None:
    actions.long_press(driver, 100, 200, duration_ms=1200)
    assert driver.calls_of("long_press")[-1].kwargs == {"x": 100, "y": 200, "duration_ms": 1200}


def test_press_key_by_name_and_code(driver: FakeDriver) -> None:
    actions.press_key(driver, "back")
    actions.press_key(driver, "66")
    actions.press_key(driver, 3)
    codes = [call.kwargs["keycode"] for call in driver.calls_of("key")]
    assert codes == [4, 66, 3]


def test_press_key_invalid(driver: FakeDriver) -> None:
    with pytest.raises(ApiError) as excinfo:
        actions.press_key(driver, "not-a-key")
    assert excinfo.value.code == "INVALID_ARGUMENT"
    assert driver.calls == []


def test_input_text_and_clear(driver: FakeDriver) -> None:
    actions.input_text(driver, "中文 & <特殊>")
    assert driver.calls_of("input_text")[-1].kwargs == {"text": "中文 & <特殊>"}

    actions.clear_and_input(driver, "new text")
    ops = [op for op in driver.ops() if op in ("clear_text", "input_text")]
    assert ops[-2:] == ["clear_text", "input_text"]
    assert driver.calls_of("input_text")[-1].kwargs == {"text": "new text"}


def test_action_failure_normalized() -> None:
    driver = FakeDriver()
    driver.fail_on = {"click"}
    with pytest.raises(ApiError) as excinfo:
        actions.tap(driver, 1, 2)
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"
    assert "click" in excinfo.value.message


def test_input_failure_propagates() -> None:
    driver = FakeDriver()
    driver.fail_on = {"input_text"}
    with pytest.raises(ApiError) as excinfo:
        actions.input_text(driver, "x")
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"
