"""logcat 采集：环形缓冲、artifact 落库、recent 兜底、自动 attach。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from app import config
from app.android.driver import CommandResult, FakeDriver
from app.capture import logcat
from app.db import session_scope
from app.models import Artifact, Run


@pytest.fixture()
def logcat_env(tmp_path, temp_db):
    config.reset_to_defaults()
    data_dir = tmp_path / "data"
    config.set_db_overrides({"runtime.data_dir": str(data_dir)})
    yield data_dir
    logcat.shutdown_all()
    logcat._ACTIVE.clear()
    config.reset_to_defaults()


class _FakeStream:
    """假 stdout：读完给定行后阻塞，直到进程被 terminate（模拟持续 logcat）。"""

    def __init__(self, lines: list[str]) -> None:
        self._iterator = iter(lines)
        self._gate = threading.Event()

    def __iter__(self):
        return self

    def __next__(self) -> str:
        try:
            return next(self._iterator)
        except StopIteration:
            self._gate.wait(5)
            raise StopIteration from None

    def open_gate(self) -> None:
        self._gate.set()


class _FakeProcess:
    def __init__(self, lines: list[str]) -> None:
        self.stdout = _FakeStream(lines)
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True
        self.stdout.open_gate()

    def kill(self) -> None:
        self.killed = True
        self.stdout.open_gate()

    def wait(self, timeout: float | None = None) -> int:
        return 0


def _wait_for_line(capture: logcat.LogcatCapture, expected: str, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if capture.tail(1) == [expected]:
            return
        time.sleep(0.01)
    raise AssertionError(f"logcat capture did not reach line: {expected} (tail={capture.tail(3)})")


# ---------------------------------------------------------------- 环形缓冲 + artifact
def test_ring_buffer_and_artifact(logcat_env, monkeypatch) -> None:
    process = _FakeProcess([f"line{i}" for i in range(1, 6)])
    monkeypatch.setattr(logcat, "_spawn_logcat", lambda serial: process)

    capture = logcat.LogcatCapture("fake-log", max_lines=3)
    capture.start()
    try:
        _wait_for_line(capture, "line5")
        assert capture.tail(10) == ["line3", "line4", "line5"]  # 环形缓冲只留最后 3 行
    finally:
        path = capture.stop()

    assert isinstance(path, Path)
    assert path.is_file()
    assert path.parent == config.artifacts_dir() / "logcat"
    assert path.read_text(encoding="utf-8").splitlines() == ["line3", "line4", "line5"]
    assert process.terminated is True

    with session_scope() as db:
        artifacts = list(db.scalars(select(Artifact).where(Artifact.kind == "logcat")))
        assert len(artifacts) == 1
        assert artifacts[0].path == str(path)
        assert artifacts[0].size == path.stat().st_size
        assert artifacts[0].meta["lines"] == 3
        assert artifacts[0].meta["serial"] == "fake-log"


def test_capture_recent_uses_active_ring_buffer(logcat_env, monkeypatch) -> None:
    process = _FakeProcess(["alpha", "beta", "gamma"])
    monkeypatch.setattr(logcat, "_spawn_logcat", lambda serial: process)

    capture = logcat.start_capture("fake-log2")
    try:
        _wait_for_line(capture, "gamma")
        assert logcat.is_capturing("fake-log2") is True
        assert logcat.recent("fake-log2", lines=2) == ["beta", "gamma"]
        assert logcat.active_capture("fake-log2") is capture
    finally:
        path = logcat.stop_capture("fake-log2")
    assert path is not None and path.is_file()
    assert logcat.is_capturing("fake-log2") is False


# ---------------------------------------------------------------- recent 兜底
def test_recent_falls_back_to_driver_shell(logcat_env, monkeypatch) -> None:
    driver = FakeDriver("fake-fallback")
    driver.shell_responses["logcat -d -t 5"] = CommandResult(0, "one\ntwo\n", "")
    monkeypatch.setattr(logcat, "_adb_logcat_dump", lambda serial, lines: None)
    monkeypatch.setattr(logcat, "_get_driver", lambda serial: driver)

    assert logcat.recent("fake-fallback", lines=5) == ["one", "two"]
    assert driver.ops()[-1] == "shell"
    assert driver.calls[-1].kwargs["command"] == "logcat -d -t 5"


def test_recent_prefers_adb_dump(logcat_env, monkeypatch) -> None:
    monkeypatch.setattr(logcat, "_adb_logcat_dump", lambda serial, lines: ["from-adb"])

    def unexpected(serial: str):
        raise AssertionError("driver fallback should not be used when adb dump works")

    monkeypatch.setattr(logcat, "_get_driver", unexpected)
    assert logcat.recent("fake-adb", lines=10) == ["from-adb"]


def test_tail_lines_helper() -> None:
    text = "a\nb\n\nc\n"
    assert logcat._tail_lines(text, 2) == ["b", "c"]
    assert logcat._tail_lines("", 5) == []


# ---------------------------------------------------------------- 自动 attach
def test_attach_if_needed_respects_config_and_script_settings(logcat_env, monkeypatch) -> None:
    data_dir = str(logcat_env)
    with session_scope() as db:
        db.add(Run(id=1, script_name="x", device_serial="fake-1", status="running"))

    monkeypatch.setattr(logcat, "_spawn_logcat", lambda serial: _FakeProcess(["attached"]))
    # 默认 capture.logcat_auto_attach=false -> 不采集
    assert logcat.attach_if_needed(1, {"logcat": True}) is None

    config.set_db_overrides({"runtime.data_dir": data_dir, "capture.logcat_auto_attach": True})
    assert logcat.attach_if_needed(1, {"logcat": False}) is None
    capture = logcat.attach_if_needed(1, {"logcat": True})
    assert capture is not None
    assert capture.run_id == 1
    try:
        _wait_for_line(capture, "attached")
    finally:
        path = logcat.stop_capture("fake-1")
    assert path is not None
    with session_scope() as db:
        artifact = db.scalars(select(Artifact).where(Artifact.kind == "logcat")).first()
        assert artifact is not None
        assert artifact.run_id == 1
