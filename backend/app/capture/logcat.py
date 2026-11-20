"""logcat 采集（契约 §2 `artifacts.kind=logcat`）。

能力边界：本模块只提供 logcat 采集能力（运行期自动 attach / 手动采集 / 最近日志查询），
**不**实现设备路由（`/api/devices/{serial}/logcat` 归 be-core）。

- `LogcatCapture`：后台线程 `adb -s <serial> logcat -v time` 流式读入环形缓冲；
  `stop()` 写出 artifact 文件并 insert `artifacts` 行（kind="logcat"）；
- `recent(serial, lines)`：有活跃采集时取环形缓冲尾部，否则 `logcat -d -t N` 兜底
  （优先 `app.android.adb.run_adb`，不可用时退化为 driver.shell）；
- `attach_if_needed(run_id, script_settings)`：按 `capture.logcat_auto_attach` 与脚本
  `settings.logcat` 决定运行期自动采集。
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from .. import config
from ..db import session_scope
from ..errors import ApiError
from ..logs import logger
from ..models import Artifact, now_iso

_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_ACTIVE: dict[str, LogcatCapture] = {}
_LOCK = threading.RLock()
_LINE_LIMIT = 5000


def _safe_serial(serial: str) -> str:
    return _SAFE_RE.sub("_", str(serial or "")) or "device"


# ================================================================ 子进程 / 驱动兜底
def _spawn_logcat(serial: str) -> Any:
    """启动 logcat 流式子进程（测试可通过 monkeypatch 替换）。"""
    command = "logcat"
    try:
        from ..android.adb import adb_path

        binary = adb_path()
    except Exception:  # adb 模块未就绪
        binary = config.find_adb()
    args = [str(binary), "-s", str(serial), command, "-v", "time"]
    return subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )


def _adb_logcat_dump(serial: str, lines: int) -> list[str] | None:
    """无活跃采集时的兜底：adb logcat -d -t N；adb 能力不可用返回 None。"""
    try:
        from ..android.adb import run_adb
    except Exception:
        return None
    try:
        result = run_adb(["-s", str(serial), "logcat", "-d", "-t", str(int(lines))], timeout_s=5)
    except Exception:  # pragma: no cover - adb 执行失败
        return None
    if getattr(result, "code", 1) != 0:
        return None
    return _tail_lines(str(getattr(result, "stdout", "") or ""), lines)


def _get_driver(serial: str) -> Any:
    from ..services.device_registry import get_driver

    return get_driver(serial)


def _driver_logcat_dump(serial: str, lines: int) -> list[str]:
    try:
        driver = _get_driver(serial)
    except Exception:  # pragma: no cover - 设备服务未装配
        return []
    try:
        result = driver.shell(f"logcat -d -t {int(lines)}", timeout_s=5)
    except Exception:  # pragma: no cover - 驱动异常
        return []
    return _tail_lines(str(getattr(result, "stdout", "") or ""), lines)


def _tail_lines(text: str, lines: int) -> list[str]:
    values = [line for line in str(text or "").splitlines() if line.strip()]
    return values[-int(lines) :] if lines else values


# ================================================================ 采集会话
class LogcatCapture:
    """单个 serial 的 logcat 后台采集。"""

    def __init__(
        self,
        serial: str,
        run_id: int | None = None,
        max_lines: int | None = None,
    ) -> None:
        self.serial = str(serial)
        self.run_id = run_id
        self.max_lines = int(max_lines or config.get("capture.logcat_max_lines") or _LINE_LIMIT)
        self._lines: deque[str] = deque(maxlen=self.max_lines)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: Any = None
        self._path: Path | None = None
        self._started_at = now_iso()
        self._started_monotonic = time.monotonic()

    # ---- 生命周期
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def start(self) -> LogcatCapture:
        if self.running:
            return self
        try:
            self._proc = _spawn_logcat(self.serial)
        except Exception as exc:
            raise ApiError(
                "DEVICE_UNAVAILABLE",
                "logcat 启动失败",
                {"serial": self.serial, "error": str(exc)},
            ) from exc
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._read_loop,
            name=f"logcat-{_safe_serial(self.serial)}",
            daemon=True,
        )
        self._thread.start()
        logger.info("logcat capture started: %s", self.serial)
        return self

    def _read_loop(self) -> None:
        process = self._proc
        stream = getattr(process, "stdout", None)
        try:
            if stream is not None:
                for raw in stream:
                    if self._stop_event.is_set():
                        break
                    line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                    line = line.rstrip("\r\n")
                    if not line:
                        continue
                    with self._lock:
                        self._lines.append(line)
        except Exception as exc:  # pragma: no cover - 流异常
            logger.debug("logcat read loop ended: %s", exc)
        finally:
            try:
                if process is not None:
                    process.wait(timeout=1)
            except Exception:  # pragma: no cover - 进程已退出/无 wait
                pass

    def stop(self) -> Path | None:
        """停止采集，写出 artifact 文件并落 `artifacts` 行；返回文件路径。"""
        self._stop_event.set()
        process = self._proc
        if process is not None:
            for action in ("terminate", "kill"):
                method = getattr(process, action, None)
                if not callable(method):
                    continue
                try:
                    method()
                except Exception:
                    pass
                try:
                    process.wait(timeout=2)
                    break
                except Exception:
                    continue
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)
        self._thread = None
        self._proc = None

        with self._lock:
            lines = list(self._lines)
        path = self._write_artifact(lines)
        self._path = path
        with _LOCK:
            if _ACTIVE.get(self.serial) is self:
                _ACTIVE.pop(self.serial, None)
        logger.info("logcat capture stopped: %s lines=%s path=%s", self.serial, len(lines), path)
        return path

    def _write_artifact(self, lines: list[str]) -> Path | None:
        directory = config.artifacts_dir() / "logcat"
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        path = directory / f"logcat_{_safe_serial(self.serial)}_{timestamp}.log"
        try:
            path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        except OSError as exc:  # pragma: no cover - 磁盘异常
            logger.warning("logcat artifact write failed: %s", exc)
            return None
        try:
            with session_scope() as db:
                db.add(
                    Artifact(
                        run_id=self.run_id,
                        step_result_id=None,
                        kind="logcat",
                        name=f"logcat {self.serial}",
                        path=str(path),
                        size=path.stat().st_size,
                        meta={
                            "serial": self.serial,
                            "lines": len(lines),
                            "started_at": self._started_at,
                            "duration_ms": int((time.monotonic() - self._started_monotonic) * 1000),
                        },
                        created_at=now_iso(),
                    )
                )
        except Exception as exc:  # pragma: no cover - 落库失败不丢文件
            logger.warning("logcat artifact row insert failed: %s", exc)
        return path

    # ---- 查询
    def tail(self, lines: int = 200) -> list[str]:
        count = max(0, int(lines or 0))
        with self._lock:
            values = list(self._lines)
        return values[-count:] if count else values


# ================================================================ 模块级入口
def attach_if_needed(run_id: int, script_settings: Any) -> LogcatCapture | None:
    """运行期按设置自动开始采集；返回会话或 None。"""
    if not bool(config.get("capture.logcat_auto_attach")):
        return None
    enabled = False
    if isinstance(script_settings, dict):
        enabled = bool(script_settings.get("logcat"))
    elif script_settings is not None:
        enabled = bool(getattr(script_settings, "logcat", False))
    if not enabled:
        return None
    serial = ""
    try:
        from ..models import Run

        with session_scope() as db:
            run = db.get(Run, int(run_id))
            serial = str(run.device_serial or "") if run is not None else ""
    except Exception:  # pragma: no cover - DB 异常
        return None
    if not serial:
        return None
    return start_capture(serial, run_id=int(run_id))


def start_capture(serial: str, run_id: int | None = None) -> LogcatCapture:
    serial = str(serial)
    with _LOCK:
        existing = _ACTIVE.get(serial)
        if existing is not None and existing.running:
            return existing
        capture = LogcatCapture(serial, run_id=run_id)
        capture.start()
        _ACTIVE[serial] = capture
        return capture


def stop_capture(serial: str) -> Path | None:
    serial = str(serial)
    with _LOCK:
        capture = _ACTIVE.pop(serial, None)
    if capture is None:
        return None
    return capture.stop()


def active_capture(serial: str) -> LogcatCapture | None:
    capture = _ACTIVE.get(str(serial))
    return capture if capture is not None and capture.running else None


def is_capturing(serial: str) -> bool:
    return active_capture(serial) is not None


def recent(serial: str, lines: int = 200) -> list[str]:
    """最近 logcat 行：活跃采集取环形缓冲，否则 `logcat -d -t N` 兜底。"""
    count = max(1, int(lines or 200))
    capture = active_capture(serial)
    if capture is not None:
        return capture.tail(count)
    dumped = _adb_logcat_dump(serial, count)
    if dumped is not None:
        return dumped
    return _driver_logcat_dump(serial, count)


def shutdown_all() -> None:
    with _LOCK:
        serials = list(_ACTIVE)
    for serial in serials:
        try:
            stop_capture(serial)
        except Exception as exc:  # pragma: no cover
            logger.warning("logcat shutdown failed for %s: %s", serial, exc)
