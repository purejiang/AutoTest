"""截图 / screenrecord 录屏（契约 §14.1）。

- ``screenshot_png(driver)``：取 ``driver.screenshot()`` 并校验 PNG magic，失败重试 1 次；
- ``ScreenRecorder``：在**设备端**录 mp4（``adb shell screenrecord ... /sdcard/xxx.mp4`` 后台运行），
  ``stop()`` 给该进程发 SIGINT 让其正常收尾（moov 落盘），再 ``adb pull`` 到
  ``config.artifacts_dir()/screenrecord/``，最后删除设备端临时文件，返回 ``(本地路径, 字节数)``。
  这样产出的是可播放的真 mp4（stdout 流式只能出 h264 裸流，浏览器/报告播不了）。

进程管理：start 时尽量拿到设备端 screenrecord 的 pid（``echo $!`` 优先，``pidof`` 兜底），
stop 只 kill 自己记录的 pid；拿不到 pid 时才退化为 ``pkill -INT -f screenrecord``。
两者都失败 -> ``ApiError("DEVICE_UNAVAILABLE", "screenrecord 无法正常收尾")`` 并清理临时文件，
**不会留下损坏的 mp4 冒充成功**。
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import config
from ..errors import ApiError
from ..logs import logger
from . import adb

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# screenrecord 设备端最长录制时间（秒）；超过由 Android 强制限制
SCREENRECORD_MAX_TIME_LIMIT_S = 180
# 等 moov 落盘的静默时间（SIGINT 后）
_MOOV_SETTLE_S = 0.3
# SIGINT / SIGTERM 后等待进程退出的上限
_STOP_GRACE_S = 3.0
_TERM_GRACE_S = 2.0
# 校验 mp4 时扫描尾部找 moov 的字节数
_MOOV_SCAN_BYTES = 64 * 1024
_POLL_INTERVAL_S = 0.1
# 启动后等 pid 出现的上限
_LAUNCH_PID_WAIT_S = 0.5
# 每次启动尝试后确认进程出现的等待上限
_LAUNCH_CONFIRM_S = 0.5


# ---------------------------------------------------------------- 截图
def screenshot_png(driver: Any) -> bytes:
    """截一张 PNG；校验 PNG 头，无效时重试 1 次，仍失败抛 ``DEVICE_UNAVAILABLE``。"""
    last_reason = "no data"
    for attempt in range(2):
        try:
            data = driver.screenshot()
        except ApiError:
            raise
        except Exception as exc:
            raise ApiError(
                "DEVICE_UNAVAILABLE",
                f"screenshot failed: {exc}",
                {"reason": type(exc).__name__},
            ) from exc
        if isinstance(data, (bytearray, memoryview)):
            data = bytes(data)
        if isinstance(data, bytes) and data.startswith(PNG_MAGIC) and len(data) > len(PNG_MAGIC):
            return data
        last_reason = f"invalid png data (len={len(data) if data is not None else 0})"
        logger.warning("screenshot attempt %d/2 failed: %s", attempt + 1, last_reason)
    raise ApiError("DEVICE_UNAVAILABLE", f"screenshot failed after retry: {last_reason}")


# ---------------------------------------------------------------- 录屏
class ScreenRecorder:
    """每设备一个屏幕录制会话（设备端 mp4）；start 后 stop 返回 (本地路径, 大小)。"""

    def __init__(self, serial_or_driver: Any) -> None:
        raw_serial = getattr(serial_or_driver, "serial", None)
        if raw_serial is None and isinstance(serial_or_driver, str):
            raw_serial = serial_or_driver
        if raw_serial is None or not str(raw_serial).strip():
            raise ApiError(
                "INVALID_ARGUMENT",
                "ScreenRecorder requires a serial string or a driver with .serial",
                {"type": type(serial_or_driver).__name__},
            )
        self.serial = str(raw_serial)
        self._lock = threading.RLock()
        self._device_path: str | None = None
        self._local_path: Path | None = None
        self._pid: int | None = None
        self._started_at: float = 0.0
        self._started_wall: float = 0.0
        self._time_limit_s: int = 0
        self._name: str = ""

    def __repr__(self) -> str:
        return f"ScreenRecorder(serial={self.serial!r}, running={self.is_running})"

    @property
    def is_running(self) -> bool:
        """是否仍在录制（达到设备端 time-limit 后视为已结束）。"""
        with self._lock:
            if self._device_path is None:
                return False
            if self._time_limit_s > 0 and time.monotonic() - self._started_at >= self._time_limit_s:
                return False
            return True

    @property
    def name(self) -> str:
        return self._name

    @property
    def time_limit_s(self) -> int:
        return self._time_limit_s

    @property
    def elapsed_s(self) -> float:
        """已录制秒数（到 time-limit 封顶；未开始时为 0）。"""
        with self._lock:
            if self._device_path is None:
                return 0.0
            elapsed = max(0.0, time.monotonic() - self._started_at)
            if self._time_limit_s > 0:
                elapsed = min(elapsed, float(self._time_limit_s))
            return round(elapsed, 1)

    @property
    def remaining_s(self) -> float:
        if self._time_limit_s <= 0:
            return 0.0
        return round(max(0.0, float(self._time_limit_s) - self.elapsed_s), 1)

    @property
    def started_at(self) -> str:
        """墙钟开始时间（ISO 8601 UTC）；未开始时为空串。"""
        if self._started_wall <= 0:
            return ""
        return datetime.fromtimestamp(self._started_wall, UTC).isoformat()

    @property
    def device_path(self) -> str | None:
        """设备端临时 mp4 路径（调试/测试用）。"""
        return self._device_path

    # ------------------------------------------------------------ 生命周期
    def start(self, name: str = "", bit_rate: int = 8_000_000, time_limit_s: int = 180) -> None:
        """开始录制（设备端后台 screenrecord）；已有进行中的录制 -> ``CONFLICT``。"""
        try:
            bit_rate = int(bit_rate)
            time_limit_s = int(time_limit_s)
        except (TypeError, ValueError) as exc:
            raise ApiError("INVALID_ARGUMENT", f"invalid screenrecord args: {exc}") from exc
        if bit_rate <= 0:
            raise ApiError("INVALID_ARGUMENT", "bit_rate must be > 0", {"bit_rate": bit_rate})
        if time_limit_s <= 0:
            raise ApiError("INVALID_ARGUMENT", "time_limit_s must be > 0", {"time_limit_s": time_limit_s})
        if time_limit_s > SCREENRECORD_MAX_TIME_LIMIT_S:
            logger.warning(
                "screenrecord time_limit_s=%s exceeds max %ss, clamped",
                time_limit_s,
                SCREENRECORD_MAX_TIME_LIMIT_S,
            )
            time_limit_s = SCREENRECORD_MAX_TIME_LIMIT_S

        with self._lock:
            if self.is_running:
                raise ApiError("CONFLICT", "screen recorder already running", {"serial": self.serial})
            if self._device_path is not None:  # 上一段已到 time-limit 但没 stop，丢弃
                self._discard_stale_locked()
            existing = self._pidof()
            if existing:
                raise ApiError(
                    "CONFLICT",
                    "device is already recording (screenrecord is running)",
                    {"serial": self.serial, "pids": existing},
                )
            device_path = self._new_device_path(name)
            local_path = self._new_local_path(name)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            started_at = time.monotonic()
            pid = self._launch(device_path, bit_rate, time_limit_s)
            if pid is None:
                self._cleanup_device_file(device_path)
                self._pkill_int()
                raise ApiError(
                    "DEVICE_UNAVAILABLE",
                    "screenrecord failed to start on device (no process pid)",
                    {"serial": self.serial, "device_path": device_path},
                )
            self._device_path = device_path
            self._local_path = local_path
            self._pid = pid
            self._started_at = started_at
            self._started_wall = time.time()
            self._time_limit_s = time_limit_s
            self._name = name
            logger.info(
                "screenrecord started serial=%s pid=%s device_path=%s local_path=%s bit_rate=%s limit=%ss",
                self.serial,
                pid,
                device_path,
                local_path,
                bit_rate,
                time_limit_s,
            )

    def stop(self) -> tuple[Path, int]:
        """停止录制、拉回 mp4 并返回 (本地路径, 字节大小)；未在录制 -> ``CONFLICT``。"""
        with self._lock:
            if self._device_path is None or self._local_path is None:
                raise ApiError("CONFLICT", "screen recorder is not running", {"serial": self.serial})
            device_path = self._device_path
            local_path = self._local_path
            pid = self._pid
            try:
                if not self._stop_device_process(pid):
                    raise ApiError(
                        "DEVICE_UNAVAILABLE",
                        "screenrecord 无法正常收尾（无法停止设备端进程）",
                        {"serial": self.serial, "pid": pid},
                    )
                time.sleep(_MOOV_SETTLE_S)  # 等 moov 落盘
                adb.pull_file(self.serial, device_path, local_path, timeout_s=60)
                size = local_path.stat().st_size if local_path.exists() else 0
                if size <= 0 or not _looks_like_mp4(local_path):
                    raise ApiError(
                        "DEVICE_UNAVAILABLE",
                        f"screenrecord produced invalid mp4: {local_path}",
                        {"serial": self.serial, "path": str(local_path), "size": size},
                    )
            except ApiError:
                self._remove_local_file(local_path)
                self._cleanup_device_file(device_path)
                self._reset_locked()
                raise
            except Exception as exc:
                self._remove_local_file(local_path)
                self._cleanup_device_file(device_path)
                self._reset_locked()
                raise ApiError(
                    "DEVICE_UNAVAILABLE",
                    f"screenrecord stop failed: {exc}",
                    {"serial": self.serial, "reason": type(exc).__name__},
                ) from exc
            self._cleanup_device_file(device_path)
            self._reset_locked()
        logger.info("screenrecord stopped serial=%s path=%s size=%s bytes", self.serial, local_path, size)
        return local_path, size

    # ------------------------------------------------------------ 启动
    def _launch(self, device_path: str, bit_rate: int, time_limit_s: int) -> int | None:
        """设备端后台起 screenrecord，尽量拿到 pid；失败返回 None。"""
        record = f"screenrecord --bit-rate {bit_rate} --time-limit {time_limit_s} {device_path}"
        commands = [
            f"nohup {record} >/dev/null 2>&1 & echo $!",
            f"{record} >/dev/null 2>&1 & echo $!",
        ]
        for command in commands:
            result = adb.shell(self.serial, command, timeout_s=15)
            candidate = _parse_pid(result.stdout)
            # 优先用 pidof 确认（进程可能刚起，短暂轮询；pidof 不支持时用 kill -0）
            polled = self._wait_pidof(_LAUNCH_CONFIRM_S)
            if polled:
                return polled[0]
            if candidate is not None and self._pid_alive(candidate):
                return candidate
            logger.debug(
                "screenrecord start attempt did not yield pid (code=%s, out=%r)",
                result.code,
                result.stdout.strip(),
            )
        # 进程可能刚起，最后再等一小会
        polled = self._wait_pidof(_LAUNCH_PID_WAIT_S)
        return polled[0] if polled else None

    def _wait_pidof(self, timeout_s: float) -> list[int]:
        deadline = time.monotonic() + timeout_s
        while True:
            polled = self._pidof()
            if polled:
                return polled
            if time.monotonic() >= deadline:
                return []
            time.sleep(_POLL_INTERVAL_S)

    # ------------------------------------------------------------ 停止
    def _stop_device_process(self, pid: int | None) -> bool:
        """只停自己启动的 screenrecord；返回是否成功收尾。"""
        if pid is not None:
            if self._pid_alive(pid):
                adb.shell(self.serial, f"kill -INT {pid}", timeout_s=10)
                if not self._wait_pid_exit(pid, _STOP_GRACE_S):
                    logger.warning("screenrecord pid=%s ignored SIGINT, sending SIGTERM", pid)
                    adb.shell(self.serial, f"kill -TERM {pid}", timeout_s=10)
                    if not self._wait_pid_exit(pid, _TERM_GRACE_S):
                        return False
            return True
        # 没拿到 pid：退化为 pkill（start 前已确认没有其他 screenrecord）
        result = adb.shell(self.serial, "pkill -INT -f screenrecord", timeout_s=10)
        text = f"{result.stdout}{result.stderr}".lower()
        if result.code == 0:
            time.sleep(_MOOV_SETTLE_S)
            return True
        if result.code == 127 or "not found" in text:
            return False
        # 没有匹配进程：可能已自然结束，继续 pull
        return True

    def _wait_pid_exit(self, pid: int, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not self._pid_alive(pid):
                return True
            time.sleep(_POLL_INTERVAL_S)
        return False

    # ------------------------------------------------------------ 设备端进程探测
    def _pidof(self) -> list[int] | None:
        """``pidof screenrecord``；命令不可用时返回 None（区别于"没有进程"返回 []）。"""
        try:
            result = adb.shell(self.serial, "pidof screenrecord", timeout_s=5)
        except ApiError as exc:
            logger.debug("pidof screenrecord failed: %s", exc)
            return None
        pids = [int(token) for token in result.stdout.split() if token.isdigit()]
        if pids:
            return pids
        text = f"{result.stdout}{result.stderr}".lower()
        if result.code == 127 or "not found" in text or "inaccessible" in text:
            return None
        return []

    def _pid_alive(self, pid: int) -> bool:
        pids = self._pidof()
        if pids is not None:
            return pid in pids
        result = adb.shell(self.serial, f"kill -0 {pid}", timeout_s=5)
        return result.code == 0

    def _pkill_int(self) -> None:
        try:
            adb.shell(self.serial, "pkill -INT -f screenrecord", timeout_s=10)
        except ApiError as exc:  # 清理路径尽量不抛
            logger.warning("pkill screenrecord failed during cleanup: %s", exc)

    # ------------------------------------------------------------ 清理
    def _cleanup_device_file(self, device_path: str) -> None:
        try:
            adb.shell(self.serial, f"rm -f {device_path}", timeout_s=10)
        except ApiError as exc:
            logger.warning("remove device screenrecord file %s failed: %s", device_path, exc)

    @staticmethod
    def _remove_local_file(local_path: Path | None) -> None:
        if local_path is None:
            return
        try:
            local_path.unlink()
        except OSError:
            pass

    def _discard_stale_locked(self) -> None:
        logger.warning("discard unfinished screenrecord for %s: %s", self.serial, self._device_path)
        if self._device_path:
            self._cleanup_device_file(self._device_path)
        self._reset_locked()

    def _reset_locked(self) -> None:
        self._device_path = None
        self._local_path = None
        self._pid = None
        self._started_at = 0.0
        self._started_wall = 0.0
        self._time_limit_s = 0
        self._name = ""

    # ------------------------------------------------------------ 路径
    def _new_device_path(self, name: str) -> str:
        token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name or "").strip()).strip("._-")[:40]
        token = token or "screenrecord"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return f"/sdcard/autotest_{token}_{stamp}_{uuid.uuid4().hex[:6]}.mp4"

    @staticmethod
    def _new_local_path(name: str) -> Path:
        base = re.sub(r"[^\w\-.]+", "_", str(name or "").strip(), flags=re.UNICODE).strip("._-")[:80]
        base = base or "screenrecord"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return config.artifacts_dir() / "screenrecord" / f"{base}-{stamp}-{uuid.uuid4().hex[:6]}.mp4"


def create_screen_recorder(serial_or_driver: Any) -> ScreenRecorder:
    """工厂（契约外扩展）：接受 serial 或带 ``.serial`` 的 driver。"""
    return ScreenRecorder(serial_or_driver)


# ---------------------------------------------------------------- 工具
def _parse_pid(stdout: str) -> int | None:
    for token in reversed((stdout or "").split()):
        if token.isdigit():
            return int(token)
    return None


def _looks_like_mp4(path: Path) -> bool:
    """校验是"已正常收尾"的 mp4：有 ftyp 头且尾部能找到 moov box。"""
    try:
        size = path.stat().st_size
        if size < 16:
            return False
        with path.open("rb") as handle:
            header = handle.read(16)
            if header[4:8] != b"ftyp":
                return False
            handle.seek(max(0, size - _MOOV_SCAN_BYTES))
            tail = handle.read()
        return b"moov" in tail
    except OSError as exc:
        logger.warning("cannot validate mp4 %s: %s", path, exc)
        return False
