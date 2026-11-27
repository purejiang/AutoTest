"""logcat 手动采集 REST（契约 §7.1）。

由 `app.main` 动态发现并 include（暴露 `router`）。与 `app/api/devices.py` 的
`GET /{serial}/logcat`（最近日志）互补：

- `POST /api/devices/{serial}/logcat/start`：启动后台采集（可带 run_id/max_lines/package）；
- `POST /api/devices/{serial}/logcat/stop`：停止并写出 `artifacts/logcat/*.log` + Artifact 行；
- `GET  /api/devices/{serial}/logcat/status`：active/lines/started_at/path/size。

`package` 参数会按运行时 PID 过滤：开始采集时解析一次，后台每 1.5s 刷新（App 重启后
PID 变化自动跟上），日志行按 PID 保留，续行跟随上一条。响应 `package_filter=true`；
不传 package 时仍是整机日志。
停止接口幂等：没有活跃采集时返回最近一次保存结果（`already_stopped=true`）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field

from .. import config
from ..android.driver import Driver
from ..api_schemas import ApiModel
from ..capture import logcat
from ..deps import get_driver_or_503
from ..errors import ApiError, device_unavailable

router = APIRouter(prefix="/api/devices", tags=["devices"])

# 环形缓冲上限：防止手动 API 一次申请过大内存（默认 5000）
MAX_LINES_LIMIT = 100_000


class LogcatStartRequest(ApiModel):
    """logcat/start 可选请求体；空 body 等价于全默认值。"""

    run_id: int | None = Field(default=None, ge=1)
    max_lines: int | None = Field(default=None, ge=1, le=MAX_LINES_LIMIT)
    package: str | None = Field(default=None, max_length=200)


def _ensure_device_alive(serial: str, driver: Driver) -> None:
    """设备不存在/离线时给 DEVICE_UNAVAILABLE（而不是启动一个读不到流的线程）。"""
    try:
        alive = bool(driver.alive())
    except ApiError:
        raise
    except Exception as exc:
        raise device_unavailable(
            f"device {serial} unavailable: {exc}", {"serial": serial}
        ) from exc
    if not alive:
        raise device_unavailable(f"device {serial} is not available", {"serial": serial})


def _state_body(serial: str) -> dict[str, Any]:
    state = logcat.capture_status(serial)
    state["ok"] = True
    state["enabled"] = logcat.enabled()
    return state


@router.post("/{serial}/logcat/start")
def start_logcat(
    serial: str,
    payload: LogcatStartRequest | None = None,
    driver: Driver = Depends(get_driver_or_503),
) -> dict[str, Any]:
    """启动（或复用）该 serial 的后台 logcat 采集。"""
    if not logcat.enabled():
        raise ApiError(
            "UNSUPPORTED",
            "logcat capture is disabled (capture.logcat_auto_attach=false)",
            {"setting": "capture.logcat_auto_attach"},
        )
    _ensure_device_alive(serial, driver)
    max_lines = payload.max_lines if payload is not None else None
    package = ((payload.package or "").strip() or None) if payload is not None else None
    run_id = payload.run_id if payload is not None else None
    capture = logcat.start_capture(
        serial,
        run_id=run_id,
        max_lines=max_lines,
        package=package,
    )
    body = _state_body(serial)
    body.update(
        {
            "started": True,
            "package_requested": package,
            "package_filter": bool(capture.state().get("package_filter")),
            "capture": capture.state(),
        }
    )
    return body


@router.post("/{serial}/logcat/stop")
def stop_logcat(serial: str) -> dict[str, Any]:
    """停止并保存；没有活跃采集时幂等返回最近一次结果。"""
    was_active = logcat.is_capturing(serial)
    logcat.stop_capture(serial)
    state = _state_body(serial)
    saved_path = state.get("path")
    body: dict[str, Any] = {
        "ok": True,
        "serial": serial,
        "active": False,
        "stopped": was_active,
        "already_stopped": not was_active,
        "path": saved_path,
        "size": int(state.get("size") or 0),
        "lines": int(state.get("lines") or 0),
        "started_at": state.get("started_at"),
        "stopped_at": state.get("stopped_at"),
        "run_id": state.get("run_id"),
        "url": config.artifact_url(saved_path) if saved_path else None,
        "last": state.get("last"),
    }
    return body


@router.get("/{serial}/logcat/status")
def logcat_status(serial: str) -> dict[str, Any]:
    """采集状态；无活跃采集时也返回最近一次 path/size（status.last）。"""
    return _state_body(serial)


__all__ = ["MAX_LINES_LIMIT", "LogcatStartRequest", "router"]
