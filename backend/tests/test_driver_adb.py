"""adb.py 单测：设备解析 / 命令执行 / 二进制 exec-out / 转发与推拉（全部 mock 子进程）。"""

from __future__ import annotations

import subprocess

import pytest

from app.android import adb
from app.android.driver import CommandResult
from app.errors import ApiError

DEVICES_L = """List of devices attached
emulator-5554          device product:sdk_gphone64_arm64 model:sdk_gphone64_arm64 device:emu64a transport_id:1
R58M123ABC             unauthorized usb:3342340000000000 transport_id:2
0123456789ABCDEF       offline transport_id:3
ABC123                 no permissions (user in plugdev group; are your udev rules wrong?); see [http://developer.android.com/tools/device.html]
* daemon not running; starting now at tcp:5037
* daemon started successfully
"""


class Completed:
    def __init__(self, returncode: int = 0, stdout: str | bytes = "", stderr: str | bytes = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ---------------------------------------------------------------- 路径
def test_adb_path_is_cached(monkeypatch) -> None:
    calls: list[int] = []

    def fake_find_adb() -> str:
        calls.append(1)
        return "/tmp/fake-adb"

    monkeypatch.setattr(adb.config, "find_adb", fake_find_adb)
    adb.reset_adb_path_cache()
    assert adb.adb_path() == "/tmp/fake-adb"
    assert adb.adb_path() == "/tmp/fake-adb"
    assert len(calls) == 1
    adb.reset_adb_path_cache()
    assert adb.adb_path() == "/tmp/fake-adb"
    assert len(calls) == 2


# ---------------------------------------------------------------- devices
def test_parse_devices_detailed() -> None:
    devices = adb._parse_devices(DEVICES_L)
    assert [item["serial"] for item in devices] == [
        "emulator-5554",
        "R58M123ABC",
        "0123456789ABCDEF",
        "ABC123",
    ]
    assert [item["state"] for item in devices] == [
        "device",
        "unauthorized",
        "offline",
        "no_permissions",
    ]
    first = devices[0]
    assert first["model"] == "sdk_gphone64_arm64"
    assert first["product"] == "sdk_gphone64_arm64"
    assert first["device"] == "emu64a"
    assert first["transport_id"] == 1
    assert devices[2]["transport_id"] == 3


def test_list_devices(monkeypatch) -> None:
    monkeypatch.setattr(adb, "run_adb", lambda args, timeout_s=15: CommandResult(0, DEVICES_L, ""))
    devices = adb.list_devices()
    assert len(devices) == 4
    assert adb.devices_detailed() == devices


def test_list_devices_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        adb, "run_adb", lambda args, timeout_s=15: CommandResult(1, "", "adb server died")
    )
    with pytest.raises(ApiError) as excinfo:
        adb.list_devices()
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"
    assert "adb server died" in excinfo.value.message


# ---------------------------------------------------------------- run_adb
def test_run_adb_missing_executable(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("no adb")

    monkeypatch.setattr(adb.subprocess, "run", fake_run)
    with pytest.raises(ApiError) as excinfo:
        adb.run_adb(["devices"])
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"


def test_run_adb_timeout(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="adb", timeout=15)

    monkeypatch.setattr(adb.subprocess, "run", fake_run)
    with pytest.raises(ApiError) as excinfo:
        adb.run_adb(["devices"])
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"
    assert "timed out" in excinfo.value.message


def test_run_adb_nonzero_does_not_raise(monkeypatch) -> None:
    monkeypatch.setattr(
        adb.subprocess, "run", lambda *a, **k: Completed(1, "out", "err")
    )
    result = adb.run_adb(["bogus"])
    assert result == CommandResult(code=1, stdout="out", stderr="err")


def test_run_adb_empty_args() -> None:
    with pytest.raises(ApiError) as excinfo:
        adb.run_adb([])
    assert excinfo.value.code == "INVALID_ARGUMENT"


# ---------------------------------------------------------------- forward
def test_forward_encodes_specs(monkeypatch) -> None:
    recorded: list[list[str]] = []

    def fake_run(args, timeout_s=15):
        recorded.append(list(args))
        return CommandResult(0, "", "")

    monkeypatch.setattr(adb, "run_adb", fake_run)
    adb.forward("serial1", 1234, "scrcpy_abc")
    adb.forward("serial1", "tcp:4321", "localabstract:foo")
    assert recorded[0] == ["-s", "serial1", "forward", "tcp:1234", "localabstract:scrcpy_abc"]
    assert recorded[1] == ["-s", "serial1", "forward", "tcp:4321", "localabstract:foo"]


def test_forward_failure_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        adb, "run_adb", lambda args, timeout_s=15: CommandResult(1, "", "cannot bind")
    )
    with pytest.raises(ApiError) as excinfo:
        adb.forward("s", 1234, "remote")
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"


def test_remove_forward_accepts_two_and_three_args(monkeypatch) -> None:
    recorded: list[list[str]] = []

    def fake_run(args, timeout_s=15):
        recorded.append(list(args))
        return CommandResult(0, "", "")

    monkeypatch.setattr(adb, "run_adb", fake_run)
    adb.remove_forward("s", 1234)
    adb.remove_forward("s", "tcp:4321", "ignored")
    assert recorded[0] == ["-s", "s", "forward", "--remove", "tcp:1234"]
    assert recorded[1] == ["-s", "s", "forward", "--remove", "tcp:4321"]


def test_remove_forward_failure_is_tolerant(monkeypatch) -> None:
    monkeypatch.setattr(
        adb, "run_adb", lambda args, timeout_s=15: CommandResult(1, "", "not found")
    )
    result = adb.remove_forward("s", 1234)
    assert result.code == 1  # 清理路径不抛


# ---------------------------------------------------------------- push/pull
def test_push_and_pull(monkeypatch, tmp_path) -> None:
    recorded: list[list[str]] = []

    def fake_run(args, timeout_s=15):
        recorded.append(list(args))
        return CommandResult(0, "", "")

    monkeypatch.setattr(adb, "run_adb", fake_run)
    local = tmp_path / "a.txt"
    local.write_text("hi", encoding="utf-8")
    adb.push_file("s", local, "/data/local/tmp/a.txt")
    assert recorded[-1][:3] == ["-s", "s", "push"]
    assert recorded[-1][3] == str(local)
    assert recorded[-1][4] == "/data/local/tmp/a.txt"

    target = tmp_path / "nested" / "b.bin"
    adb.pull_file("s", "/data/local/tmp/b.bin", target)
    assert target.parent.exists()
    assert recorded[-1][:3] == ["-s", "s", "pull"]


def test_push_missing_file(tmp_path) -> None:
    with pytest.raises(ApiError) as excinfo:
        adb.push_file("s", tmp_path / "missing.bin", "/data/local/tmp/x")
    assert excinfo.value.code == "INVALID_ARGUMENT"


def test_pull_failure_raises(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        adb, "run_adb", lambda args, timeout_s=15: CommandResult(1, "", "no such file")
    )
    with pytest.raises(ApiError) as excinfo:
        adb.pull_file("s", "/data/local/tmp/none", tmp_path / "none.bin")
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"


# ---------------------------------------------------------------- exec-out
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_shell_bytes_binary(monkeypatch) -> None:
    def fake_run(cmd, capture_output=True, timeout=30, **kwargs):
        return Completed(0, PNG, b"")

    monkeypatch.setattr(adb.subprocess, "run", fake_run)
    assert adb.shell_bytes("serial-x", ["screencap", "-p"]) == PNG


def test_shell_bytes_command_is_exec_out(monkeypatch) -> None:
    recorded: list[list[str]] = []

    def fake_run(cmd, capture_output=True, timeout=30, **kwargs):
        recorded.append(list(cmd))
        return Completed(0, PNG, b"")

    monkeypatch.setattr(adb.subprocess, "run", fake_run)
    adb.shell_bytes("serial-x", ["screencap", "-p"], timeout_s=20)
    adb.shell_bytes("serial-x", "screencap -p")
    assert recorded[0][1:] == ["-s", "serial-x", "exec-out", "screencap", "-p"]
    assert recorded[1][-1] == "screencap -p"


def test_shell_bytes_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        adb.subprocess, "run", lambda *a, **k: Completed(1, b"", b"device offline")
    )
    with pytest.raises(ApiError) as excinfo:
        adb.shell_bytes("serial-x", ["screencap", "-p"])
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"
    assert "device offline" in excinfo.value.message


def test_start_exec_out_command(monkeypatch) -> None:
    recorded: list[list[str]] = []

    class Proc:
        stdout = None
        stderr = None

    def fake_popen(cmd, **kwargs):
        recorded.append(list(cmd))
        return Proc()

    monkeypatch.setattr(adb.subprocess, "Popen", fake_popen)
    proc = adb.start_exec_out("serial-x", ["screenrecord", "--output-format", "h264", "-"])
    assert proc is not None
    assert recorded[0][1:] == [
        "-s",
        "serial-x",
        "exec-out",
        "screenrecord",
        "--output-format",
        "h264",
        "-",
    ]


def test_start_exec_out_missing_adb(monkeypatch) -> None:
    def fake_popen(cmd, **kwargs):
        raise FileNotFoundError("no adb")

    monkeypatch.setattr(adb.subprocess, "Popen", fake_popen)
    with pytest.raises(ApiError) as excinfo:
        adb.start_exec_out("serial-x", ["screencap", "-p"])
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"


# ---------------------------------------------------------------- shell 文本
def test_shell_helper(monkeypatch) -> None:
    recorded: list[list[str]] = []

    def fake_run(args, timeout_s=15):
        recorded.append(list(args))
        return CommandResult(0, "hello\n", "")

    monkeypatch.setattr(adb, "run_adb", fake_run)
    result = adb.shell("serial-x", "pidof screenrecord")
    assert recorded[0] == ["-s", "serial-x", "shell", "pidof screenrecord"]
    assert result.stdout == "hello\n"
    with pytest.raises(ApiError) as excinfo:
        adb.shell("serial-x", "")
    assert excinfo.value.code == "INVALID_ARGUMENT"


# ---------------------------------------------------------------- reverse
def test_reverse_and_remove_reverse(monkeypatch) -> None:
    recorded: list[list[str]] = []

    def fake_run(args, timeout_s=15):
        recorded.append(list(args))
        return CommandResult(0, "", "")

    monkeypatch.setattr(adb, "run_adb", fake_run)
    adb.reverse("serial-x", 27183, 27184)
    adb.remove_reverse("serial-x", "tcp:27183")
    assert recorded[0] == ["-s", "serial-x", "reverse", "tcp:27183", "tcp:27184"]
    assert recorded[1] == ["-s", "serial-x", "reverse", "--remove", "tcp:27183"]


def test_reverse_failure_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        adb, "run_adb", lambda args, timeout_s=15: CommandResult(1, "", "cannot bind")
    )
    with pytest.raises(ApiError) as excinfo:
        adb.reverse("serial-x", 27183, 27184)
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"


def test_adb_path_invalidated_when_setting_changes(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(adb.config, "find_adb", lambda: calls.append(1) or "/tmp/fake-adb")
    current = {"path": ""}

    def fake_get(key, default=None):
        if key == "adb.path":
            return current["path"]
        return adb.config.DEFAULTS.get(key, default)

    monkeypatch.setattr(adb.config, "get", fake_get)
    adb.reset_adb_path_cache()
    adb.adb_path()
    adb.adb_path()
    assert len(calls) == 1
    current["path"] = "/custom/adb"
    adb.adb_path()
    assert len(calls) == 2
