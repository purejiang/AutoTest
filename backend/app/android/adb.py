"""adb 封装（契约 §14.1）：全后端唯一直接调用 adb 子进程的模块。

约定：
- adb 路径一律经 ``config.find_adb()`` 探测（不写死），首次调用后缓存，可用
  ``reset_adb_path_cache()`` 失效（测试/设置变更后）；
- 超时 / adb 不存在 -> ``ApiError("DEVICE_UNAVAILABLE")``；
- ``run_adb`` 只返回结果，**不因非 0 退出码抛错**（调用方自行判断）；
  ``forward`` / ``push_file`` / ``pull_file`` / ``shell_bytes`` 失败时抛
  ``ApiError("DEVICE_UNAVAILABLE")``；
- ``shell_bytes`` 用 ``exec-out``，二进制安全（截图 / 原始数据）；
- ``start_exec_out`` 供 screen.py 做流式录制（Popen），保证 adb 调用集中在本模块。
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .. import config
from ..errors import ApiError
from ..logs import logger
from .driver import CommandResult

_ADB_LOCK = threading.Lock()
_ADB_CACHED: str | None = None
_ADB_CACHED_SOURCE: str | None = None

_DEVICE_STATES = frozenset(
    {
        "device",
        "offline",
        "unauthorized",
        "bootloader",
        "recovery",
        "sideload",
        "rescue",
        "host",
        "connecting",
        "authorizing",
    }
)


# ---------------------------------------------------------------- 路径 / 执行
def adb_path() -> str:
    """探测并缓存 adb 可执行文件路径（配置 > PATH > 常见 SDK 目录）。

    缓存与配置项 ``adb.path`` 绑定：设置里改了 adb 路径后自动重新探测，无需调用方额外失效。
    """
    global _ADB_CACHED, _ADB_CACHED_SOURCE
    configured = str(config.get("adb.path") or "")
    with _ADB_LOCK:
        if _ADB_CACHED is None or _ADB_CACHED_SOURCE != configured:
            _ADB_CACHED = config.find_adb() or "adb"
            _ADB_CACHED_SOURCE = configured
        return _ADB_CACHED


def reset_adb_path_cache() -> None:
    """清空 adb 路径缓存（测试 / 强制重探）。"""
    global _ADB_CACHED, _ADB_CACHED_SOURCE
    with _ADB_LOCK:
        _ADB_CACHED = None
        _ADB_CACHED_SOURCE = None


def _command(args: Sequence[str | int]) -> list[str]:
    return [adb_path(), *(str(arg) for arg in args)]


def run_adb(args: list[str], timeout_s: float = 15) -> CommandResult:
    """执行一次 adb 命令（同步），返回 ``CommandResult``；非 0 退出码不抛。"""
    if not args:
        raise ApiError("INVALID_ARGUMENT", "adb args must not be empty")
    command = _command(args)
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            f"adb executable not found: {command[0]}",
            {"adb": command[0]},
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            f"adb command timed out after {timeout_s}s: {' '.join(args)}",
            {"args": list(args), "timeout_s": timeout_s},
        ) from exc
    except OSError as exc:
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            f"failed to execute adb: {exc}",
            {"adb": command[0]},
        ) from exc
    return CommandResult(code=proc.returncode, stdout=proc.stdout or "", stderr=proc.stderr or "")


def start_exec_out(
    serial: str,
    command: str | Sequence[str],
    *,
    bufsize: int = 0,
) -> subprocess.Popen:
    """以 ``adb -s SERIAL exec-out ...`` 启动子进程（stdout=PIPE），供流式采集用。

    失败（adb 不存在等）抛 ``ApiError("DEVICE_UNAVAILABLE")``；返回的 Popen 由调用方负责
    terminate/kill/wait 与关闭 stdout。
    """
    cmd = [adb_path(), "-s", str(serial), "exec-out", *_split_command(command)]
    try:
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=bufsize,
        )
    except FileNotFoundError as exc:
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            f"adb executable not found: {cmd[0]}",
            {"adb": cmd[0]},
        ) from exc
    except OSError as exc:
        raise ApiError("DEVICE_UNAVAILABLE", f"failed to start adb: {exc}") from exc


def _split_command(command: str | Sequence[str]) -> list[str]:
    if isinstance(command, str):
        return [command] if command else []
    return [str(part) for part in command]


# ---------------------------------------------------------------- 设备发现
def _parse_devices(output: str) -> list[dict[str, Any]]:
    """解析 ``adb devices -l`` 输出。

    返回项：``{"serial", "state", "model"?, "product"?, "device"?, "transport_id"?}``；
    忽略 ``List of devices attached`` 与 daemon 提示行（以 ``*`` 开头）。
    """
    devices: list[dict[str, Any]] = []
    for raw_line in output.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("*") or line.lower().startswith("list of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        item: dict[str, Any] = {"serial": parts[0]}
        rest: list[str]
        if parts[1] == "no" and len(parts) >= 3 and parts[2].startswith("permissions"):
            item["state"] = "no_permissions"
            rest = parts[3:]
        else:
            item["state"] = parts[1]
            rest = parts[2:]
        if item["state"] not in _DEVICE_STATES and item["state"] != "no_permissions":
            # 非设备行（例如 adb 服务日志）跳过
            continue
        for token in rest:
            key, sep, value = token.partition(":")
            if not sep or not value:
                continue
            if key in ("model", "product", "device", "usb", "connection"):
                item[key] = value
            elif key == "transport_id":
                try:
                    item["transport_id"] = int(value)
                except ValueError:
                    item["transport_id"] = value
        devices.append(item)
    return devices


def list_devices() -> list[dict[str, Any]]:
    """``adb devices -l`` 解析出的在线/离线设备列表（含 model/transport_id 等元信息）。"""
    result = run_adb(["devices", "-l"], timeout_s=10)
    if result.code != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            f"adb devices failed: {message or 'unknown error'}",
            {"code": result.code},
        )
    return _parse_devices(result.stdout)


def devices_detailed() -> list[dict[str, Any]]:
    """同 ``list_devices()``（保留独立名字兼容契约 §14.1）。"""
    return list_devices()


# ---------------------------------------------------------------- 端口转发
def _local_spec(local: int | str) -> str:
    if isinstance(local, int):
        return f"tcp:{local}"
    text = str(local).strip()
    return text if ":" in text else f"tcp:{text}"


def _remote_spec(remote: int | str) -> str:
    if isinstance(remote, int):
        return f"tcp:{remote}"
    text = str(remote).strip()
    if ":" in text:
        return text
    return f"localabstract:{text}"


def forward(serial: str, local: int | str, remote: int | str) -> CommandResult:
    """``adb forward tcp:local localabstract:remote``；失败抛 DEVICE_UNAVAILABLE。"""
    result = run_adb(["-s", str(serial), "forward", _local_spec(local), _remote_spec(remote)])
    if result.code != 0:
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            f"adb forward failed: {(result.stderr or result.stdout).strip()}",
            {"serial": serial, "local": local, "remote": remote},
        )
    return result


def remove_forward(serial: str, local: int | str, remote: int | str | None = None) -> CommandResult:
    """移除端口转发；清理路径**不抛**（幂等），失败只记 warning。"""
    result = run_adb(["-s", str(serial), "forward", "--remove", _local_spec(local)], timeout_s=10)
    if result.code != 0:
        logger.warning(
            "remove_forward(%s, %s) failed: %s", serial, local, (result.stderr or result.stdout).strip()
        )
    return result


def reverse(serial: str, remote: int | str, local: int | str) -> CommandResult:
    """``adb reverse tcp:remote tcp:local``（scrcpy 等用）；失败抛 DEVICE_UNAVAILABLE。"""
    result = run_adb(["-s", str(serial), "reverse", _local_spec(remote), _local_spec(local)])
    if result.code != 0:
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            f"adb reverse failed: {(result.stderr or result.stdout).strip()}",
            {"serial": serial, "remote": remote, "local": local},
        )
    return result


def remove_reverse(serial: str, remote: int | str) -> CommandResult:
    """移除 reverse；清理路径**不抛**，失败只记 warning。"""
    result = run_adb(["-s", str(serial), "reverse", "--remove", _local_spec(remote)], timeout_s=10)
    if result.code != 0:
        logger.warning(
            "remove_reverse(%s, %s) failed: %s", serial, remote, (result.stderr or result.stdout).strip()
        )
    return result


# ---------------------------------------------------------------- 文本 shell
def shell(serial: str, command: str | Sequence[str], timeout_s: float = 15) -> CommandResult:
    """``adb -s SERIAL shell <command>``（文本；非 0 不抛，调用方判断）。

    说明：adb 会把 shell 之后的 argv 用空格拼成一条命令，因此整串命令作为一个参数传入
    （如 ``"nohup ... >/dev/null 2>&1 & echo $!"``）也能正常工作。
    """
    parts = _split_command(command)
    if not parts:
        raise ApiError("INVALID_ARGUMENT", "shell command must not be empty")
    return run_adb(["-s", str(serial), "shell", *parts], timeout_s=timeout_s)


# ---------------------------------------------------------------- 文件传输
def push_file(serial: str, local: str | Path, remote: str, timeout_s: float = 120) -> CommandResult:
    local_path = Path(local).expanduser()
    if not local_path.exists():
        raise ApiError("INVALID_ARGUMENT", f"local file not found: {local_path}")
    result = run_adb(["-s", str(serial), "push", str(local_path), str(remote)], timeout_s=timeout_s)
    if result.code != 0:
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            f"adb push failed: {(result.stderr or result.stdout).strip()}",
            {"serial": serial, "local": str(local_path), "remote": remote},
        )
    return result


def pull_file(serial: str, remote: str, local: str | Path, timeout_s: float = 120) -> CommandResult:
    local_path = Path(local).expanduser()
    if local_path.parent and not local_path.parent.exists():
        local_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_adb(["-s", str(serial), "pull", str(remote), str(local_path)], timeout_s=timeout_s)
    if result.code != 0:
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            f"adb pull failed: {(result.stderr or result.stdout).strip()}",
            {"serial": serial, "remote": remote, "local": str(local_path)},
        )
    return result


# ---------------------------------------------------------------- 二进制 shell
def shell_bytes(serial: str, command: str | Sequence[str], timeout_s: float = 30) -> bytes:
    """``adb -s SERIAL exec-out``：二进制安全（截图等），失败抛 DEVICE_UNAVAILABLE。"""
    parts = _split_command(command)
    if not parts:
        raise ApiError("INVALID_ARGUMENT", "shell command must not be empty")
    cmd = [adb_path(), "-s", str(serial), "exec-out", *parts]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout_s)
    except FileNotFoundError as exc:
        raise ApiError(
            "DEVICE_UNAVAILABLE", f"adb executable not found: {cmd[0]}", {"adb": cmd[0]}
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            f"adb exec-out timed out after {timeout_s}s: {' '.join(parts)}",
            {"serial": serial, "command": parts},
        ) from exc
    except OSError as exc:
        raise ApiError("DEVICE_UNAVAILABLE", f"failed to execute adb: {exc}") from exc
    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            f"adb exec-out failed (code {proc.returncode}): {stderr or 'no stderr'}",
            {"serial": serial, "command": parts, "code": proc.returncode},
        )
    return proc.stdout or b""
