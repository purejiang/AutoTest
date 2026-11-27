"""logcat 采集（契约 §2 `artifacts.kind=logcat`）。

能力边界：本模块只提供 logcat 采集能力（运行期自动 attach / 手动采集 / 最近日志查询），
**不**实现设备路由（`/api/devices/{serial}/logcat` 归 be-core）。

- `LogcatCapture`：后台线程 `adb -s <serial> logcat -v time` 流式读入环形缓冲；
  `stop()` 写出 artifact 文件并 insert `artifacts` 行（kind="logcat"）；
- `recent(serial, lines)`：有活跃采集时取环形缓冲尾部，否则 `logcat -d -t N` 兜底
  （优先 `app.android.adb.run_adb`，不可用时退化为 driver.shell）；
- `attach_if_needed(run_id, script_settings)`：按 `capture.logcat_auto_attach` 与脚本
  `settings.logcat` 决定运行期自动采集；只复用本 run 启动的采集，不抢占用户手动采集；
- `stop_run_capture(run_id, serial)`：运行结束只停止本 run 启动的采集（幂等）；
- `capture_status(serial)`：手动采集 API 的 active/lines/started_at/path/size 状态。

`package` 参数当前只记录、不参与 adb 过滤（P0 限制）：`adb logcat` 没有按包名
过滤的原生参数，需要先解析 pid 再 `--pid`，留待后续实现。
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
# serial -> 最近一次 stop 的摘要（status 在无活跃采集时仍可返回保存路径）
_LAST: dict[str, dict[str, Any]] = {}
_LOCK = threading.RLock()
_LINE_LIMIT = 5000
# `-v time` 行首：MM-DD HH:MM:SS.mmm  PID ...；部分格式把 PID 放在 Tag 后的括号里。
_LINE_PID = re.compile(r"^\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}\s+(\d+)\b")
_LINE_PID_PAREN = re.compile(r"^\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}.*?\(\s*(\d+)\s*\)")


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


def _adb_binary() -> str:
    try:
        from ..android.adb import adb_path

        return str(adb_path())
    except Exception:
        return str(config.find_adb())


def _resolve_pids(serial: str, packages: list[str]) -> set[str]:
    """解析包名当前 PID：pidof 优先，ps 兜底。失败返回空集合（不抛异常）。"""
    pids: set[str] = set()
    if not packages:
        return pids
    binary = _adb_binary()
    for package in packages:
        for args in (["pidof", package], ["ps", "-A", "-o", "PID,NAME"]):
            try:
                result = subprocess.run(
                    [binary, "-s", str(serial), "shell", *args],
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
            except Exception:  # pragma: no cover - adb 不可用
                continue
            if result.returncode != 0:
                continue
            if args[0] == "pidof":
                pids.update(re.findall(r"\b\d+\b", result.stdout or ""))
            else:
                for line in (result.stdout or "").splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[0].isdigit() and parts[-1] == package:
                        pids.add(parts[0])
            if pids:
                break
    return pids


def _line_pid(line: str) -> str | None:
    match = _LINE_PID.match(line) or _LINE_PID_PAREN.match(line)
    return match.group(1) if match else None


def refresh_pids(serial: str) -> None:
    """应用节点执行后立即刷新 PID，避免等下一次轮询。"""
    with _LOCK:
        capture = _ACTIVE.get(str(serial))
    if capture is not None and capture.running and capture.packages:
        capture.refresh_pids()


# ================================================================ 采集会话
class LogcatCapture:
    """单个 serial 的 logcat 后台采集。"""

    def __init__(
        self,
        serial: str,
        run_id: int | None = None,
        max_lines: int | None = None,
        package: str | None = None,
        packages: list[str] | None = None,
        started_by_run: bool = False,
    ) -> None:
        self.serial = str(serial)
        self.run_id = run_id
        # 只有运行引擎自动 attach 的采集才归该 run；手动 API 启动的采集即使带 run_id 也不归引擎管
        self.started_by_run = bool(started_by_run and run_id is not None)
        raw_packages = packages if packages is not None else ([package] if package else [])
        self.packages = [str(item).strip() for item in raw_packages if str(item).strip()]
        self.package = self.packages[0] if len(self.packages) == 1 else None
        self.max_lines = int(max_lines or config.get("capture.logcat_max_lines") or _LINE_LIMIT)
        self._lines: deque[str] = deque(maxlen=self.max_lines)
        self._lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._pid_thread: threading.Thread | None = None
        self._pids: set[str] = set()
        self._pid_lock = threading.Lock()
        self._proc: Any = None
        self._path: Path | None = None
        self._stopped = False
        self._stopped_at: str | None = None
        self._started_at = now_iso()
        self._started_monotonic = time.monotonic()

    # ---- 状态查询
    @property
    def lines(self) -> int:
        with self._lock:
            return len(self._lines)

    @property
    def started_at(self) -> str:
        return self._started_at

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def pids(self) -> set[str]:
        with self._pid_lock:
            return set(self._pids)

    def refresh_pids(self, packages: list[str] | None = None) -> None:
        """重新解析 PID；App 重启/切换后调用（也由后台监视线程周期调用）。"""
        targets = self.packages if packages is None else list(packages)
        pids = _resolve_pids(self.serial, targets)
        with self._pid_lock:
            self._pids = pids

    def state(self) -> dict[str, Any]:
        """采集状态摘要（API status / stop 响应用）。"""
        path = self._path
        size = 0
        if path is not None:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
        return {
            "serial": self.serial,
            "active": self.running,
            "lines": self.lines,
            "started_at": self._started_at,
            "path": str(path) if path is not None else None,
            "size": size,
            "max_lines": self.max_lines,
            "run_id": self.run_id,
            "started_by_run": self.started_by_run,
            "package": self.package,
            "packages": list(self.packages),
            "package_filter": bool(self.packages),
            "pids": sorted(self.pids),
            "stopped_at": self._stopped_at,
        }

    # ---- 生命周期
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def start(self) -> LogcatCapture:
        if self.running:
            return self
        if self.packages:
            self.refresh_pids()
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
        if self.packages:
            self._pid_thread = threading.Thread(
                target=self._pid_loop,
                name=f"logcat-pids-{_safe_serial(self.serial)}",
                daemon=True,
            )
            self._pid_thread.start()
        logger.info("logcat capture started: %s (packages=%s)", self.serial, self.packages)
        return self

    def _pid_loop(self) -> None:
        while not self._stop_event.wait(1.5):
            self.refresh_pids()

    def _read_loop(self) -> None:
        process = self._proc
        stream = getattr(process, "stdout", None)
        try:
            # package 模式下，时间戳行按 PID 决定是否保留；堆栈续行沿用上一行。
            include_line = not self.packages
            if stream is not None:
                for raw in stream:
                    if self._stop_event.is_set():
                        break
                    line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                    line = line.rstrip("\r\n")
                    if not line:
                        continue
                    if self.packages:
                        pid = _line_pid(line)
                        if pid is not None:
                            include_line = pid in self.pids
                    if include_line:
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
        """停止采集，写出 artifact 文件并落 `artifacts` 行；返回文件路径。

        同一实例重复 stop 是幂等的：只写一次 artifact，后续直接返回已有路径。
        """
        with self._stop_lock:
            if self._stopped:
                return self._path
            self._stopped = True
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
            pid_thread = self._pid_thread
            if pid_thread is not None and pid_thread.is_alive():
                pid_thread.join(timeout=2)
            self._thread = None
            self._pid_thread = None
            self._proc = None

            with self._lock:
                lines = list(self._lines)
            path = self._write_artifact(lines)
            self._path = path
            self._stopped_at = now_iso()
            state = self.state()
            with _LOCK:
                if _ACTIVE.get(self.serial) is self:
                    _ACTIVE.pop(self.serial, None)
                _LAST[self.serial] = state
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
                            "started_by_run": self.started_by_run,
                            "package": self.package,
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
def enabled() -> bool:
    """logcat 采集总开关（设置页 → 执行 → 采集）：脚本采集与手动采集都受它约束。"""
    return bool(config.get("capture.logcat_auto_attach"))


def attach_if_needed(
    run_id: int,
    script_settings: Any,
    max_lines: int | None = None,
    packages: list[str] | None = None,
) -> LogcatCapture | None:
    """运行期按设置自动开始采集；返回会话或 None。

    幂等：本 run 已 attach 过则返回同一会话；若该 serial 已有**用户手动**采集
    （或别的 run 的采集），不抢占，返回 None。
    """
    if not enabled():
        return None
    script_enabled = False
    if isinstance(script_settings, dict):
        script_enabled = bool(script_settings.get("logcat"))
    elif script_settings is not None:
        script_enabled = bool(getattr(script_settings, "logcat", False))
    if not script_enabled:
        return None
    try:
        run_key = int(run_id)
    except (TypeError, ValueError):
        return None
    serial = ""
    try:
        from ..models import Run

        with session_scope() as db:
            run = db.get(Run, run_key)
            serial = str(run.device_serial or "") if run is not None else ""
    except Exception:  # pragma: no cover - DB 异常
        return None
    if not serial:
        return None
    with _LOCK:
        existing = _ACTIVE.get(serial)
        if existing is not None and existing.running:
            if existing.started_by_run and existing.run_id == run_key:
                return existing
            logger.info(
                "logcat capture already active for %s (run_id=%s); skip auto attach for run %s",
                serial,
                existing.run_id,
                run_key,
            )
            return None
        capture = LogcatCapture(
            serial,
            run_id=run_key,
            max_lines=max_lines,
            packages=list(packages or []),
            started_by_run=True,
        )
        capture.start()
        _ACTIVE[serial] = capture
        return capture


def start_capture(
    serial: str,
    run_id: int | None = None,
    max_lines: int | None = None,
    package: str | None = None,
) -> LogcatCapture:
    """手动采集入口；同 serial 已有活跃采集时直接复用（幂等）。"""
    serial = str(serial)
    with _LOCK:
        existing = _ACTIVE.get(serial)
        if existing is not None and existing.running:
            return existing
        capture = LogcatCapture(
            serial,
            run_id=run_id,
            max_lines=max_lines,
            package=package,
        )
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


def stop_run_capture(run_id: int, serial: str | None = None) -> Path | None:
    """只停止由 `run_id` 自动 attach 的采集；手动采集/别的 run 的采集不动。

    幂等：本 run 没启动采集、已被手动停止或重复调用时返回 None。
    """
    try:
        run_key = int(run_id)
    except (TypeError, ValueError):
        return None
    with _LOCK:
        target: LogcatCapture | None = None
        if serial is not None:
            candidate = _ACTIVE.get(str(serial))
            if (
                candidate is not None
                and candidate.started_by_run
                and candidate.run_id == run_key
            ):
                target = candidate
        else:
            for candidate in _ACTIVE.values():
                if candidate.started_by_run and candidate.run_id == run_key:
                    target = candidate
                    break
        if target is None:
            return None
        _ACTIVE.pop(target.serial, None)
    return target.stop()


def active_capture(serial: str) -> LogcatCapture | None:
    capture = _ACTIVE.get(str(serial))
    return capture if capture is not None and capture.running else None


def is_capturing(serial: str) -> bool:
    return active_capture(serial) is not None


def capture_status(serial: str) -> dict[str, Any]:
    """采集状态：活跃时含环形缓冲行数，停止后仍返回最近一次保存的 path/size。"""
    serial = str(serial)
    with _LOCK:
        capture = _ACTIVE.get(serial)
        last = dict(_LAST.get(serial) or {})
    if capture is not None:
        state = capture.state()
        state["last"] = dict(last) if last else None
        return state
    if last:
        state = dict(last)
        state["serial"] = serial
        state["active"] = False
        state["last"] = dict(last)
        return state
    return {
        "serial": serial,
        "active": False,
        "lines": 0,
        "started_at": None,
        "path": None,
        "size": 0,
        "max_lines": int(config.get("capture.logcat_max_lines") or _LINE_LIMIT),
        "run_id": None,
        "started_by_run": False,
        "package": None,
        "stopped_at": None,
        "last": None,
    }


def recent(serial: str, lines: int = 200, *, active_only: bool = False) -> list[str]:
    """最近 logcat 行：活跃采集取环形缓冲，否则 `logcat -d -t N` 兜底。

    ``active_only=True`` 时没有活跃采集直接返回空，避免失败快照在节点失败路径上
    额外等待 adb dump；用于运行期失败证据，采集必须提前开启。
    """
    count = max(1, int(lines or 200))
    capture = active_capture(serial)
    if capture is not None:
        return capture.tail(count)
    if active_only:
        return []
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
