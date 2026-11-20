"""网络抓包（可选能力，契约 §0 / §7.1）。

M8 可选：mitmproxy 未安装或 `capture.network.enabled=false` 时，**所有入口**都抛
`ApiError("UNSUPPORTED", "网络抓包未启用")`。启用时提供最小可用骨架：
按 serial 维护采集会话，产出 `.mitm` 文件并在停止时落 `artifacts` 行（kind="network"）。
本模块不实现设备路由（`/api/devices/{serial}/network/start|stop` 归 be-core）。
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from .. import config
from ..db import session_scope
from ..errors import ApiError
from ..logs import logger
from ..models import Artifact, now_iso

_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_ACTIVE: dict[str, NetworkCapture] = {}
_LOCK = threading.RLock()

_DISABLED_MESSAGE = "网络抓包未启用"


def _safe_serial(serial: str) -> str:
    return _SAFE_RE.sub("_", str(serial or "")) or "device"


def _mitmproxy_available() -> bool:
    """mitmproxy 可导入（测试可 monkeypatch 该函数，避免真实依赖）。"""
    try:
        import mitmproxy  # noqa: F401
    except Exception:
        return False
    return True


def available() -> bool:
    """抓包可用 = 配置开启 且 mitmproxy 可导入。"""
    if not bool(config.get("capture.network.enabled")):
        return False
    return _mitmproxy_available()


def _ensure_available() -> None:
    if not bool(config.get("capture.network.enabled")):
        raise ApiError("UNSUPPORTED", _DISABLED_MESSAGE)
    if not _mitmproxy_available():
        raise ApiError("UNSUPPORTED", "网络抓包未启用（缺少 mitmproxy 可选依赖）")


class NetworkCapture:
    """最小抓包会话骨架：维护 mitm 文件与生命周期，addon 钩子按需扩展。"""

    def __init__(self, serial: str, run_id: int | None = None) -> None:
        self.serial = str(serial)
        self.run_id = run_id
        self.started_at = now_iso()
        self._started_monotonic = time.monotonic()
        self.path: Path | None = None
        self.active = False
        self.addon: Any = None

    def start(self) -> NetworkCapture:
        directory = config.artifacts_dir() / "network"
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        self.path = directory / f"network_{_safe_serial(self.serial)}_{timestamp}.mitm"
        header = {
            "format": "autotest-mitm-skeleton",
            "serial": self.serial,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "flows": [],
        }
        try:
            self.path.write_text(json.dumps(header, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:  # pragma: no cover - 磁盘异常
            raise ApiError("INTERNAL", "网络抓包文件创建失败", {"error": str(exc)}) from exc
        self.active = True
        logger.info("network capture started: %s -> %s", self.serial, self.path)
        return self

    def stop(self) -> dict[str, Any]:
        self.active = False
        size = 0
        if self.path is not None:
            try:
                size = self.path.stat().st_size
            except OSError:  # pragma: no cover
                size = 0
            try:
                with session_scope() as db:
                    db.add(
                        Artifact(
                            run_id=self.run_id,
                            step_result_id=None,
                            kind="network",
                            name=f"network {self.serial}",
                            path=str(self.path),
                            size=size,
                            meta={
                                "serial": self.serial,
                                "format": "mitm-skeleton",
                                "started_at": self.started_at,
                                "duration_ms": int((time.monotonic() - self._started_monotonic) * 1000),
                            },
                            created_at=now_iso(),
                        )
                    )
            except Exception as exc:  # pragma: no cover - 落库失败不丢文件
                logger.warning("network artifact row insert failed: %s", exc)
        return self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return {
            "serial": self.serial,
            "status": "running" if self.active else "stopped",
            "active": self.active,
            "path": str(self.path) if self.path is not None else None,
            "size": self._size(),
        }

    def _size(self) -> int:
        try:
            return int(self.path.stat().st_size) if self.path is not None else 0
        except OSError:  # pragma: no cover
            return 0


# ================================================================ 模块级入口（未启用一律 UNSUPPORTED）
def start(serial: str, run_id: int | None = None) -> dict[str, Any]:
    _ensure_available()
    serial = str(serial)
    with _LOCK:
        existing = _ACTIVE.get(serial)
        if existing is not None and existing.active:
            return existing.to_dict()
        capture = NetworkCapture(serial, run_id=run_id).start()
        _ACTIVE[serial] = capture
        return capture.to_dict()


def stop(serial: str) -> dict[str, Any]:
    _ensure_available()
    serial = str(serial)
    with _LOCK:
        capture = _ACTIVE.pop(serial, None)
    if capture is None:
        raise ApiError("NOT_FOUND", "没有进行中的网络抓包", {"serial": serial})
    return capture.stop()


def status(serial: str) -> dict[str, Any]:
    _ensure_available()
    capture = _ACTIVE.get(str(serial))
    if capture is None or not capture.active:
        return {"serial": str(serial), "status": "stopped", "active": False, "path": None, "size": 0}
    return capture.to_dict()


def shutdown_all() -> None:
    if not bool(config.get("capture.network.enabled")):
        return
    with _LOCK:
        serials = list(_ACTIVE)
    for serial in serials:
        try:
            stop(serial)
        except Exception as exc:  # pragma: no cover
            logger.warning("network shutdown failed for %s: %s", serial, exc)
