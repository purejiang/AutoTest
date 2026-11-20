"""runner.controls 单测：pause/resume 校验、stop、debug、清理。"""

from __future__ import annotations

import pytest

from app.errors import ApiError
from app.runner import controls


@pytest.fixture(autouse=True)
def _clean_controls():
    controls.cleanup_all()
    yield
    controls.cleanup_all()


def test_pause_requires_active_control():
    assert controls.request_pause(1) is None
    assert controls.request_resume(1) is None
    assert controls.request_stop(1) is None
    assert controls.request_debug(1, "step") is None
    assert controls.run_control_state(1) is None
    assert controls.is_paused(1) is False


def test_pause_then_resume_hot():
    controls.create_control(1)
    state = controls.request_pause(1)
    assert state["paused"] is True
    assert state["pause_reason"] == "user"
    assert controls.is_paused(1) is True

    resumed = controls.request_resume(1)
    assert resumed["paused"] is False
    assert resumed["resume_warnings"] == []
    assert controls.is_paused(1) is False

    with pytest.raises(ApiError) as excinfo:
        controls.request_resume(1)
    assert excinfo.value.code == "CONFLICT"
    assert excinfo.value.status_code == 409


def test_stop_sets_flag_and_unblocks_pause():
    control = controls.create_control(2)
    controls.request_pause(2)
    assert not control.pause_event.is_set()

    state = controls.request_stop(2)
    assert state["stop_requested"] is True
    assert state["status"] == "stopping"
    assert control.stop_event.is_set()
    assert control.pause_event.is_set()  # 唤醒暂停等待，让引擎立刻看到 stop

    with pytest.raises(ApiError) as excinfo:
        controls.request_pause(2)
    assert excinfo.value.code == "CONFLICT"


def test_cleanup_removes_control():
    controls.create_control(3)
    assert controls.has_control(3) is True
    controls.cleanup(3)
    assert controls.has_control(3) is False


def test_breakpoint_pause_reason_visible():
    controls.create_control(4)
    controls.request_pause(4, reason="breakpoint", at_step_id="s9")
    state = controls.run_control_state(4)
    assert state["pause_reason"] == "breakpoint"
    assert state["paused_at_step_id"] == "s9"


def test_debug_step_and_continue_toggle_step_mode():
    control = controls.create_control(5)
    controls.request_pause(5)
    state = controls.request_debug(5, "step")
    assert state["step_mode"] is True
    assert controls.is_paused(5) is False  # 放行一步
    assert control.pause_event.is_set()

    state = controls.request_debug(5, "continue")
    assert state["step_mode"] is False
    assert state["paused"] is False


def test_debug_run_from_requires_step_and_is_consumed():
    controls.create_control(6)
    with pytest.raises(ApiError) as excinfo:
        controls.request_debug(6, "run_from")
    assert excinfo.value.code == "INVALID_ARGUMENT"

    controls.request_pause(6)
    state = controls.request_debug(6, "run_from", "s3")
    assert state["pending_run_from"] == "s3"
    assert controls.consume_run_from(6) == "s3"
    assert controls.consume_run_from(6) is None


def test_unknown_debug_action_rejected():
    controls.create_control(7)
    with pytest.raises(ApiError) as excinfo:
        controls.request_debug(7, "explode")
    assert excinfo.value.code == "INVALID_ARGUMENT"


def test_pause_wait_announced_once():
    control = controls.create_control(8)
    controls.request_pause(8)
    assert controls.begin_pause_wait(control) is True
    assert controls.begin_pause_wait(control) is False
    assert controls.end_pause_wait(control) is True
    assert controls.end_pause_wait(control) is False


def test_active_run_ids_tracks_controls():
    controls.create_control(9)
    controls.create_control(10)
    assert sorted(controls.active_run_ids()) == [9, 10]
    controls.cleanup_all()
    assert controls.active_run_ids() == []
