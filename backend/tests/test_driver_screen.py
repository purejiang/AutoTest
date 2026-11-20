"""screen.py 单测：截图校验 + ScreenRecorder（设备端录 mp4，假 adb，不依赖真机）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.android import screen
from app.android.driver import CommandResult, FakeDriver
from app.android.screen import ScreenRecorder, create_screen_recorder, screenshot_png
from app.errors import ApiError

# 最小合法 mp4 结构：ftyp 头 + 尾部 moov（ScreenRecorder 会校验）
MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100 + b"moov" + b"\x00" * 16


class FakeAdb:
    """脚本化 adb.shell / adb.pull_file。"""

    def __init__(self) -> None:
        self.shell_calls: list[str] = []
        self.pull_calls: list[tuple[str, str, str]] = []
        self.removed: list[str] = []
        self.pids: list[int] = []
        self.start_pid: int | None = 4242
        self.start_echo_pid = True
        self.pidof_unsupported = False
        self.pkill_code = 0
        self.pkill_stderr = ""
        self.ignore_sigint = False
        self.pull_bytes: bytes = MP4_BYTES
        self.pull_error: ApiError | None = None
        self.artifacts: Path = Path(".")

    # ---- adb.shell
    def shell(self, serial: str, command: str, timeout_s: float = 15) -> CommandResult:
        self.shell_calls.append(command)
        if "screenrecord --bit-rate" in command:
            if self.start_pid is None:
                return CommandResult(0, "", "")
            self.pids = [self.start_pid]
            if self.start_echo_pid:
                return CommandResult(0, f"{self.start_pid}\n", "")
            return CommandResult(0, "", "")
        if command.startswith("pidof screenrecord"):
            if self.pidof_unsupported:
                return CommandResult(127, "", "sh: pidof: not found")
            if not self.pids:
                return CommandResult(1, "", "")
            return CommandResult(0, "\n".join(str(pid) for pid in self.pids) + "\n", "")
        if command.startswith("kill -0 "):
            pid = int(command.split()[-1])
            return CommandResult(0 if pid in self.pids else 1, "", "")
        if command.startswith("kill -INT ") or command.startswith("kill -TERM "):
            if self.ignore_sigint:
                return CommandResult(0, "", "")
            self.pids = []
            return CommandResult(0, "", "")
        if command.startswith("pkill -INT"):
            if self.pkill_code == 0:
                self.pids = []
            return CommandResult(self.pkill_code, "", self.pkill_stderr)
        if command.startswith("rm -f "):
            self.removed.append(command[len("rm -f ") :])
            return CommandResult(0, "", "")
        return CommandResult(0, "", "")

    # ---- adb.pull_file
    def pull_file(self, serial: str, remote: str, local: str | Path, timeout_s: float = 120) -> CommandResult:
        self.pull_calls.append((serial, remote, str(local)))
        if self.pull_error is not None:
            raise self.pull_error
        Path(local).write_bytes(self.pull_bytes)
        return CommandResult(0, "", "")


@pytest.fixture()
def fake_adb(monkeypatch, tmp_path) -> FakeAdb:
    artifacts = tmp_path / "artifacts"
    monkeypatch.setattr(screen.config, "artifacts_dir", lambda: artifacts)
    monkeypatch.setattr(screen, "_MOOV_SETTLE_S", 0.0)
    monkeypatch.setattr(screen, "_STOP_GRACE_S", 0.05)
    monkeypatch.setattr(screen, "_TERM_GRACE_S", 0.05)
    monkeypatch.setattr(screen, "_POLL_INTERVAL_S", 0.005)
    monkeypatch.setattr(screen, "_LAUNCH_PID_WAIT_S", 0.02)
    monkeypatch.setattr(screen, "_LAUNCH_CONFIRM_S", 0.02)
    fake = FakeAdb()
    fake.artifacts = artifacts
    monkeypatch.setattr(screen.adb, "shell", fake.shell)
    monkeypatch.setattr(screen.adb, "pull_file", fake.pull_file)
    return fake


# ---------------------------------------------------------------- 截图
def test_screenshot_png_from_fake_driver() -> None:
    data = screenshot_png(FakeDriver())
    assert data.startswith(screen.PNG_MAGIC)
    assert len(data) > len(screen.PNG_MAGIC)


def test_screenshot_png_retries_once_then_raises() -> None:
    class BadDriver:
        serial = "bad"

        def __init__(self) -> None:
            self.calls = 0

        def screenshot(self) -> bytes:
            self.calls += 1
            return b"not a png"

    driver = BadDriver()
    with pytest.raises(ApiError) as excinfo:
        screenshot_png(driver)
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"
    assert driver.calls == 2


def test_screenshot_png_driver_exception() -> None:
    class BrokenDriver:
        serial = "broken"

        def screenshot(self) -> bytes:
            raise RuntimeError("device gone")

    with pytest.raises(ApiError) as excinfo:
        screenshot_png(BrokenDriver())
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"


# ---------------------------------------------------------------- 录屏
def test_screen_recorder_start_stop(fake_adb: FakeAdb) -> None:
    recorder = ScreenRecorder("fake-serial")
    assert recorder.is_running is False
    recorder.start("登录流程", bit_rate=4_000_000, time_limit_s=30)
    assert recorder.is_running is True
    assert recorder.device_path is not None
    assert recorder.device_path.startswith("/sdcard/autotest_")
    assert recorder.device_path.endswith(".mp4")

    # 启动命令：设备端屏幕录制 + 静默后台 + 回 pid
    start_commands = [cmd for cmd in fake_adb.shell_calls if "screenrecord --bit-rate" in cmd]
    assert start_commands
    start = start_commands[0]
    assert start.startswith("nohup screenrecord --bit-rate 4000000 --time-limit 30 /sdcard/")
    assert ">/dev/null 2>&1 & echo $!" in start
    assert "rm -f" not in start
    device_path = recorder.device_path
    assert device_path is not None

    path, size = recorder.stop()
    assert path.exists()
    assert path.suffix == ".mp4"
    assert path.parent == fake_adb.artifacts / "screenrecord"
    assert "登录流程" in path.name
    assert size == len(MP4_BYTES)
    assert path.stat().st_size == size
    assert recorder.is_running is False

    # stop 只 kill 自己的 pid -> 等收尾 -> pull -> 删设备端临时文件
    assert f"kill -INT {fake_adb.start_pid}" in fake_adb.shell_calls
    assert any(command.startswith("kill -TERM ") for command in fake_adb.shell_calls) is False
    assert fake_adb.pull_calls[0][1] == device_path
    assert fake_adb.removed == [device_path]
    assert path.read_bytes() == MP4_BYTES


def test_screen_recorder_conflicts(fake_adb: FakeAdb) -> None:
    recorder = ScreenRecorder("fake-serial")
    with pytest.raises(ApiError) as excinfo:
        recorder.stop()
    assert excinfo.value.code == "CONFLICT"

    recorder.start("x", 4_000_000, 10)
    with pytest.raises(ApiError) as excinfo:
        recorder.start("y", 4_000_000, 10)
    assert excinfo.value.code == "CONFLICT"
    recorder.stop()


def test_screen_recorder_conflict_when_device_busy(fake_adb: FakeAdb) -> None:
    fake_adb.pids = [999]  # 设备上已有 screenrecord
    recorder = ScreenRecorder("fake-serial")
    with pytest.raises(ApiError) as excinfo:
        recorder.start("x", 4_000_000, 10)
    assert excinfo.value.code == "CONFLICT"
    assert recorder.is_running is False


def test_screen_recorder_start_after_stop(fake_adb: FakeAdb) -> None:
    recorder = ScreenRecorder("fake-serial")
    recorder.start("first", 4_000_000, 10)
    first_path, _ = recorder.stop()
    fake_adb.start_pid = 5252
    recorder.start("second", 4_000_000, 10)
    second_path, second_size = recorder.stop()
    assert first_path != second_path
    assert second_size == len(MP4_BYTES)


def test_screen_recorder_pidof_fallback(fake_adb: FakeAdb) -> None:
    fake_adb.start_echo_pid = False
    fake_adb.start_pid = 777
    recorder = ScreenRecorder("fake-serial")
    recorder.start("x", 4_000_000, 10)
    path, size = recorder.stop()
    assert size == len(MP4_BYTES)
    assert path.exists()
    assert "kill -INT 777" in fake_adb.shell_calls


def test_screen_recorder_pidof_unsupported_uses_kill0(fake_adb: FakeAdb) -> None:
    fake_adb.pidof_unsupported = True
    recorder = ScreenRecorder("fake-serial")
    recorder.start("x", 4_000_000, 10)
    path, size = recorder.stop()
    assert size == len(MP4_BYTES)
    assert path.exists()
    assert any(cmd.startswith("kill -0 ") for cmd in fake_adb.shell_calls)


def test_screen_recorder_pkill_fallback_when_no_pid(fake_adb: FakeAdb) -> None:
    recorder = ScreenRecorder("fake-serial")
    assert recorder._stop_device_process(None) is True
    assert "pkill -INT -f screenrecord" in fake_adb.shell_calls

    fake_adb.pkill_code = 127
    fake_adb.pkill_stderr = "sh: pkill: not found"
    assert recorder._stop_device_process(None) is False

    fake_adb.pkill_code = 1
    fake_adb.pkill_stderr = ""
    assert recorder._stop_device_process(None) is True  # 没有匹配进程 = 已自然结束


def test_screen_recorder_natural_end(fake_adb: FakeAdb) -> None:
    recorder = ScreenRecorder("fake-serial")
    recorder.start("x", 4_000_000, 10)
    fake_adb.pids = []  # 设备端已自然结束（time-limit）
    path, size = recorder.stop()
    assert size == len(MP4_BYTES)
    assert not any(cmd.startswith("kill -INT ") for cmd in fake_adb.shell_calls)


def test_screen_recorder_invalid_mp4_removed(fake_adb: FakeAdb) -> None:
    fake_adb.pull_bytes = b"not an mp4"
    recorder = ScreenRecorder("fake-serial")
    recorder.start("bad", 4_000_000, 10)
    device_path = recorder.device_path
    with pytest.raises(ApiError) as excinfo:
        recorder.stop()
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"
    assert device_path in fake_adb.removed  # 设备端临时文件已清理
    local_dir = fake_adb.artifacts / "screenrecord"
    assert list(local_dir.glob("*.mp4")) == []  # 不留损坏文件
    assert recorder.is_running is False


def test_screen_recorder_pull_failure_cleans_up(fake_adb: FakeAdb) -> None:
    fake_adb.pull_error = ApiError("DEVICE_UNAVAILABLE", "pull failed")
    recorder = ScreenRecorder("fake-serial")
    recorder.start("x", 4_000_000, 10)
    device_path = recorder.device_path
    with pytest.raises(ApiError):
        recorder.stop()
    assert device_path in fake_adb.removed


def test_screen_recorder_stop_failure_cleans_up(fake_adb: FakeAdb) -> None:
    fake_adb.ignore_sigint = True
    recorder = ScreenRecorder("fake-serial")
    recorder.start("x", 4_000_000, 10)
    device_path = recorder.device_path
    with pytest.raises(ApiError) as excinfo:
        recorder.stop()
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"
    assert "无法正常收尾" in excinfo.value.message
    assert device_path in fake_adb.removed
    assert fake_adb.pull_calls == []


def test_screen_recorder_start_failure(fake_adb: FakeAdb) -> None:
    fake_adb.start_pid = None  # 启动不返回 pid，pidof 也拿不到
    recorder = ScreenRecorder("fake-serial")
    with pytest.raises(ApiError) as excinfo:
        recorder.start("x", 4_000_000, 10)
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"
    assert recorder.is_running is False
    assert recorder.device_path is None
    assert fake_adb.removed  # 清理设备端可能生成的临时文件


def test_screen_recorder_discards_stale_on_restart(fake_adb: FakeAdb) -> None:
    recorder = ScreenRecorder("fake-serial")
    recorder.start("first", 4_000_000, 10)
    old_device_path = recorder.device_path
    fake_adb.pids = []

    # 模拟已过 time-limit：is_running 变 False
    recorder._started_at -= 100
    assert recorder.is_running is False

    fake_adb.start_pid = 8888
    recorder.start("second", 4_000_000, 10)
    assert old_device_path in fake_adb.removed  # 丢弃上一段设备端文件
    recorder.stop()


def test_screen_recorder_invalid_args(fake_adb: FakeAdb) -> None:
    recorder = ScreenRecorder("fake-serial")
    with pytest.raises(ApiError) as excinfo:
        recorder.start("x", bit_rate=0, time_limit_s=10)
    assert excinfo.value.code == "INVALID_ARGUMENT"
    with pytest.raises(ApiError) as excinfo:
        recorder.start("x", bit_rate=4_000_000, time_limit_s=0)
    assert excinfo.value.code == "INVALID_ARGUMENT"


def test_screen_recorder_accepts_driver(fake_adb: FakeAdb) -> None:
    driver = FakeDriver(serial="fake-999")
    recorder = create_screen_recorder(driver)
    assert recorder.serial == "fake-999"
    assert ScreenRecorder(driver).serial == "fake-999"
    with pytest.raises(ApiError):
        ScreenRecorder(object())


def test_screen_recorder_clamps_time_limit(fake_adb: FakeAdb) -> None:
    recorder = ScreenRecorder("fake-serial")
    recorder.start("long", 4_000_000, 999)
    assert f"--time-limit {screen.SCREENRECORD_MAX_TIME_LIMIT_S}" in " ".join(fake_adb.shell_calls)
    recorder.stop()
