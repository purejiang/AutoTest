"""设备与手动控制 API（契约 §7.1）。

三条硬性纪律（所有注入类端点）：
1. 坐标解析：`x,y` 直接使用；`nx,ny` 必须经 be-media 的
   `app.mirror.map_normalized_to_device()` 反算，无活跃镜像 -> INVALID_ARGUMENT；
2. 录制挂钩：点击类 `hooks.on_before_pointer()` -> 注入 -> `hooks.on_action()`；
   滑动/key/text/app 直接 `hooks.on_action()`（环缓冲在 session 内部）；
3. 占用检查：device 被 run 占用 -> 409 DEVICE_BUSY；run 处于 paused 时放行并额外
   `hooks.note_manual()` + 一条 §5 `manual_intervention` 运行事件（RunEventBus）；
   录制/镜像期间允许手动控制。

注意：mirror 的 start/stop/status 端点与 WS 归 be-media（app/api/mirror.py, §14.3），本文件不重复注册。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from .. import config, models
from ..android.driver import Driver, resolve_key
from ..api_schemas import (
    AppRequest,
    ConnectRequest,
    KeyRequest,
    LongPressRequest,
    PointRequest,
    ScreenRecordStartRequest,
    ShellRequest,
    SwipeRequest,
    TextRequest,
    items_response,
)
from ..deps import get_db, get_driver_or_503
from ..errors import ApiError, device_unavailable
from ..logs import logger
from ..recorder import hooks
from ..services import device_registry

router = APIRouter(prefix="/api/devices", tags=["devices"])

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_SCREEN_RECORDERS: dict[str, Any] = {}
_SCREEN_RECORDER_LOCK = threading.RLock()


# ---------------------------------------------------------------- 队友模块探测（缺失即降级报错）
def _adb_module() -> Any | None:
    try:
        from ..android import adb  # noqa: PLC0415 - be-driver 模块可能尚未落地
    except ImportError:
        return None
    return adb


def _screen_module() -> Any | None:
    try:
        from ..android import screen  # noqa: PLC0415
    except ImportError:
        return None
    return screen


def _mirror_module() -> Any | None:
    try:
        from .. import mirror  # noqa: PLC0415 - be-media 模块
    except ImportError:
        return None
    return mirror


def _network_fn(action: str) -> Any | None:
    try:
        from ..capture import network  # noqa: PLC0415 - be-report 模块
    except ImportError:
        return None
    for name in (f"{action}_capture", action):
        fn = getattr(network, name, None)
        if callable(fn):
            return fn
    return None


def _try_mirror(serial: str, method: str, **kwargs: Any) -> bool:
    """镜像页操作快路径：优先走 scrcpy control socket（实测 ~10-40ms，u2 注入 ~160ms+）。

    无活跃镜像会话 / 无 control socket / 发送失败 → 返回 False，调用方回落 u2 注入。
    """
    try:
        from .. import mirror as mirror_mod  # 局部导入：避免无镜像依赖时的启动开销
    except Exception:  # pragma: no cover - mirror 模块缺失时直接回落
        return False
    func = getattr(mirror_mod, method, None)
    if func is None:
        return False
    try:
        return bool(func(serial, **kwargs))
    except Exception:  # noqa: BLE001 - 快路径异常一律回落
        logger.debug("mirror fast path %s failed for %s", method, serial, exc_info=True)
        return False


def _device_call(serial: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
    """设备交互统一包装：设备层异常 -> DEVICE_UNAVAILABLE（不让 500 漏给前端）。"""
    try:
        return fn(*args, **kwargs)
    except ApiError:
        raise
    except Exception as exc:
        logger.exception("device %s call failed: %s", serial, getattr(fn, "__name__", fn))
        raise device_unavailable(f"device {serial} unavailable: {exc}", {"serial": serial}) from exc


# ---------------------------------------------------------------- 坐标解析
def _map_normalized(serial: str, nx: float, ny: float) -> tuple[int, int]:
    module = _mirror_module()
    mapper = getattr(module, "map_normalized_to_device", None) if module is not None else None
    if callable(mapper):
        try:
            point = mapper(serial, float(nx), float(ny))
        except ApiError:
            raise
        except Exception:
            logger.warning("map_normalized_to_device failed for %s", serial, exc_info=True)
            point = None
        if point is not None:
            return int(point[0]), int(point[1])
    raise ApiError(
        "INVALID_ARGUMENT",
        "normalized coordinates require an active mirror",
        {"serial": serial, "hint": "start mirror first or send absolute x/y"},
    )


def _resolve_point(serial: str, payload: PointRequest) -> tuple[int, int]:
    if payload.x is not None and payload.y is not None:
        return int(payload.x), int(payload.y)
    if payload.nx is not None and payload.ny is not None:
        return _map_normalized(serial, float(payload.nx), float(payload.ny))
    raise ApiError("INVALID_ARGUMENT", "coordinates required", {"serial": serial})


def _resolve_swipe(serial: str, payload: SwipeRequest) -> tuple[int, int, int, int]:
    if (
        payload.x1 is not None
        and payload.y1 is not None
        and payload.x2 is not None
        and payload.y2 is not None
    ):
        return int(payload.x1), int(payload.y1), int(payload.x2), int(payload.y2)
    if (
        payload.nx1 is not None
        and payload.ny1 is not None
        and payload.nx2 is not None
        and payload.ny2 is not None
    ):
        start = _map_normalized(serial, float(payload.nx1), float(payload.ny1))
        end = _map_normalized(serial, float(payload.nx2), float(payload.ny2))
        return start[0], start[1], end[0], end[1]
    raise ApiError("INVALID_ARGUMENT", "coordinates required", {"serial": serial})


# ---------------------------------------------------------------- 占用 / 录制
def _paused_run_id(session: Session, run_ref: Any) -> int | None:
    """run 占用且状态 paused -> run_id；否则 None。"""
    try:
        run_id = int(run_ref)
    except (TypeError, ValueError):
        return None
    row = session.get(models.Run, run_id)
    if row is not None and row.status == "paused":
        return run_id
    return None


def _manual_state(serial: str, session: Session) -> int | None:
    """校验手动控制是否允许；返回 paused run 的 id（需 note_manual + manual_intervention 留痕）。"""
    manual_run_id: int | None = None
    for entry in device_registry.occupancy(serial):
        purpose = str(entry.get("purpose"))
        if purpose == "run":
            paused_run_id = _paused_run_id(session, entry.get("ref_id"))
            if paused_run_id is not None:
                manual_run_id = paused_run_id
                continue
            raise ApiError(
                "DEVICE_BUSY",
                f"device {serial} is busy running {entry.get('ref_id')}",
                {"serial": serial, "holder": entry, "hint": "pause the run to control the device manually"},
            )
        if purpose in ("recording", "mirror"):
            continue  # 录制/镜像期间允许手动控制（录制会留痕）
        raise ApiError("DEVICE_BUSY", f"device {serial} is busy with {purpose}", {"serial": serial, "holder": entry})
    return manual_run_id


def _emit_manual_intervention(serial: str, run_id: int, kind: str, payload: dict[str, Any]) -> None:
    """暂停 run 期间的手动操作 -> §5 manual_intervention 运行事件（失败只告警，控制优先）。"""
    try:
        from ..events import RunEventBus  # noqa: PLC0415 - be-runner 模块，缺失时降级
    except ImportError:
        logger.warning("RunEventBus not available yet; manual_intervention for run %s dropped", run_id)
        return
    try:
        RunEventBus.emit(int(run_id), "manual_intervention", {"kind": kind, "payload": dict(payload)})
    except Exception:
        logger.warning(
            "emit manual_intervention failed (run=%s serial=%s kind=%s)", run_id, serial, kind, exc_info=True
        )


def _after_action(
    serial: str,
    kind: str,
    payload: dict[str, Any],
    pre: dict[str, Any] | None = None,
    manual_run_id: int | None = None,
) -> None:
    hooks.on_action(serial, kind, payload, pre)
    if manual_run_id is not None:
        hooks.note_manual(serial, kind, payload)
        _emit_manual_intervention(serial, manual_run_id, kind, payload)


# ---------------------------------------------------------------- 列表 / 连接
@router.get("")
def list_devices(refresh: bool = False) -> dict[str, Any]:
    """GET /api/devices：真机 + fake 设备（§7.1 字段）。"""
    return items_response(device_registry.list_devices(refresh=refresh))


@router.post("/connect")
def connect(payload: ConnectRequest) -> dict[str, Any]:
    adb = _adb_module()
    if adb is None:
        raise ApiError("DEVICE_UNAVAILABLE", "adb module is not available yet (app.android.adb missing)")
    timeout_s = float(config.get("adb.connect_timeout_s") or 20)
    try:
        result = adb.run_adb(["connect", payload.address], timeout_s=timeout_s)
    except Exception as exc:
        raise device_unavailable(f"adb connect failed: {exc}", {"address": payload.address}) from exc
    output = f"{result.stdout or ''}{result.stderr or ''}".strip()
    if int(getattr(result, "code", 1) or 0) != 0 or "cannot" in output.lower() or "failed" in output.lower():
        raise device_unavailable(
            f"adb connect failed: {output or 'unknown error'}", {"address": payload.address, "output": output}
        )
    return {"ok": True, "address": payload.address, "output": output}


@router.post("/{serial}/disconnect")
def disconnect(serial: str) -> dict[str, Any]:
    adb = _adb_module()
    if adb is None:
        raise ApiError("DEVICE_UNAVAILABLE", "adb module is not available yet (app.android.adb missing)")
    try:
        result = adb.run_adb(["disconnect", serial], timeout_s=10)
    except Exception as exc:
        raise device_unavailable(f"adb disconnect failed: {exc}", {"serial": serial}) from exc
    device_registry.evict(serial)
    output = f"{result.stdout or ''}{result.stderr or ''}".strip()
    if int(getattr(result, "code", 1) or 0) != 0:
        raise device_unavailable(f"adb disconnect failed: {output}", {"serial": serial})
    return {"ok": True, "serial": serial, "output": output}


# ---------------------------------------------------------------- 信息类
@router.get("/{serial}/info")
def get_info(serial: str, driver: Driver = Depends(get_driver_or_503)) -> dict[str, Any]:
    info = _device_call(serial, driver.info)
    item: dict[str, Any] = info.to_dict()
    item["serial"] = serial
    item["state"] = "device" if _device_call(serial, driver.alive) else "offline"
    try:
        item["current_app"] = _device_call(serial, driver.current_app)
    except ApiError:
        item["current_app"] = None
    item["mirror"] = device_registry.mirror_info(serial)
    reason = device_registry.busy_reason(serial)
    item["busy"] = reason is not None
    if reason is not None:
        item["busy_reason"] = reason
    return item


def _raw_xml(driver: Driver, node: Any) -> str | None:
    """raw XML：优先 driver.dump_hierarchy_raw()，其次 be-driver 的 to_xml_string 序列化。"""
    raw_fn = getattr(driver, "dump_hierarchy_raw", None)
    if callable(raw_fn):
        try:
            return str(raw_fn())
        except Exception:
            logger.warning("dump_hierarchy_raw failed", exc_info=True)
    try:
        from ..android import hierarchy  # noqa: PLC0415 - be-driver 模块
    except ImportError:
        return None
    serializer = getattr(hierarchy, "to_xml_string", None)
    if callable(serializer):
        try:
            return str(serializer(node))
        except Exception:
            logger.warning("to_xml_string failed", exc_info=True)
    return None


@router.get("/{serial}/hierarchy")
def get_hierarchy(
    serial: str,
    raw: bool = False,
    driver: Driver = Depends(get_driver_or_503),
) -> dict[str, Any]:
    node = _device_call(serial, driver.dump_hierarchy)
    body: dict[str, Any] = {"node": node.to_dict()}
    if raw:
        body["raw"] = _raw_xml(driver, node)
        if body["raw"] is None:
            body["raw_unavailable"] = "raw xml not supported by current driver"
    return body


def _capture_png(serial: str, driver: Driver) -> bytes:
    screen = _screen_module()
    fn = getattr(screen, "screenshot_png", None) if screen is not None else None
    if callable(fn):
        return _device_call(serial, fn, driver)
    data = _device_call(serial, driver.screenshot)
    if not data or not bytes(data).startswith(PNG_MAGIC):
        raise device_unavailable("screenshot failed: invalid png payload", {"serial": serial})
    return bytes(data)


@router.get("/{serial}/screenshot")
def get_screenshot(serial: str, driver: Driver = Depends(get_driver_or_503)) -> Response:
    data = _capture_png(serial, driver)
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/{serial}/apps")
def list_apps(
    serial: str,
    keyword: str = "",
    pkg_type: str = Query("third_party", alias="type"),
    driver: Driver = Depends(get_driver_or_503),
) -> dict[str, Any]:
    flags = {"third_party": "-3", "third": "-3", "system": "-s", "all": ""}  # "third" 为兼容别名
    pkg_type = pkg_type.strip().lower()
    if pkg_type not in flags:
        raise ApiError(
            "INVALID_ARGUMENT",
            f"invalid type: {pkg_type}",
            {"allowed": ["all", "system", "third_party"]},
        )
    command = "pm list packages" + (f" {flags[pkg_type]}" if flags[pkg_type] else "")
    result = _device_call(serial, driver.shell, command, 15)
    if int(getattr(result, "code", 0) or 0) != 0:
        raise device_unavailable(
            f"pm list packages failed: {(result.stderr or '').strip()}", {"serial": serial}
        )
    needle = keyword.strip().lower()
    packages: list[str] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("package:"):
            continue
        package = line[len("package:") :].strip()
        if not package:
            continue
        if needle and needle not in package.lower():
            continue
        packages.append(package)
    unique = sorted(dict.fromkeys(packages))
    return items_response([{"package": package} for package in unique])


@router.get("/{serial}/logcat")
def get_logcat(
    serial: str,
    lines: int = Query(200, ge=1, le=5000),
    driver: Driver = Depends(get_driver_or_503),
) -> dict[str, Any]:
    result = _device_call(serial, driver.shell, f"logcat -d -t {int(lines)}", 20)
    output = list((result.stdout or "").splitlines())
    return {"lines": output, "total": len(output), "serial": serial}


@router.post("/{serial}/shell")
def run_shell(
    serial: str,
    payload: ShellRequest,
    driver: Driver = Depends(get_driver_or_503),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    # shell 同属手动控制：run 占用 -> 409（paused 放行并留干预痕迹），与 tap/key/text 同口径
    manual_run_id = _manual_state(serial, session)
    timeout_s = max(0.1, payload.timeout_ms / 1000.0)
    result = _device_call(serial, driver.shell, payload.command, timeout_s)
    body = {"code": int(result.code), "stdout": result.stdout or "", "stderr": result.stderr or ""}
    if manual_run_id is not None:
        event_payload = {
            "command": payload.command,
            "timeout_ms": int(payload.timeout_ms),
            "code": int(result.code),
        }
        hooks.note_manual(serial, "shell", event_payload)
        _emit_manual_intervention(serial, manual_run_id, "shell", event_payload)
    return body


# ---------------------------------------------------------------- 手动控制（录制挂钩 + 占用 + 坐标）
def _control_response(serial: str, kind: str, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "serial": serial, "kind": kind, **extra}


@router.post("/{serial}/tap")
def tap(
    serial: str,
    payload: PointRequest,
    driver: Driver = Depends(get_driver_or_503),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    x, y = _resolve_point(serial, payload)
    manual_run_id = _manual_state(serial, session)
    pre = hooks.on_before_pointer(serial)
    if not _try_mirror(serial, "inject_tap", x=x, y=y):
        _device_call(serial, driver.click, x, y)
    _after_action(serial, "tap", {"x": x, "y": y, "duration_ms": 0}, pre, manual_run_id)
    return _control_response(serial, "tap", x=x, y=y)


@router.post("/{serial}/long_press")
def long_press(
    serial: str,
    payload: LongPressRequest,
    driver: Driver = Depends(get_driver_or_503),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    x, y = _resolve_point(serial, payload)
    manual_run_id = _manual_state(serial, session)
    pre = hooks.on_before_pointer(serial)
    if not _try_mirror(serial, "inject_long_press", x=x, y=y, duration_ms=int(payload.duration_ms)):
        _device_call(serial, driver.long_press, x, y, int(payload.duration_ms))
    _after_action(
        serial,
        "long_press",
        {"x": x, "y": y, "duration_ms": int(payload.duration_ms)},
        pre,
        manual_run_id,
    )
    return _control_response(serial, "long_press", x=x, y=y, duration_ms=payload.duration_ms)


@router.post("/{serial}/swipe")
def swipe(
    serial: str,
    payload: SwipeRequest,
    driver: Driver = Depends(get_driver_or_503),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    x1, y1, x2, y2 = _resolve_swipe(serial, payload)
    manual_run_id = _manual_state(serial, session)
    duration = int(payload.duration_ms)
    if not _try_mirror(serial, "inject_swipe", x1=x1, y1=y1, x2=x2, y2=y2, duration_ms=duration):
        _device_call(serial, driver.swipe, x1, y1, x2, y2, duration)
    _after_action(
        serial,
        "swipe",
        {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration_ms": duration},
        None,
        manual_run_id,
    )
    return _control_response(serial, "swipe", x1=x1, y1=y1, x2=x2, y2=y2, duration_ms=duration)


@router.post("/{serial}/key")
def press_key(
    serial: str,
    payload: KeyRequest,
    driver: Driver = Depends(get_driver_or_503),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    key_name: str | None = None
    if payload.keycode is not None:
        code = int(payload.keycode)
    else:
        key_name = str(payload.key)
        try:
            code = resolve_key(key_name)
        except ValueError as exc:
            raise ApiError("INVALID_ARGUMENT", str(exc), {"key": key_name}) from exc
    manual_run_id = _manual_state(serial, session)
    if not _try_mirror(serial, "inject_key", keycode=code):
        _device_call(serial, driver.key, code)
    _after_action(serial, "key", {"key": key_name, "keycode": code}, None, manual_run_id)
    return _control_response(serial, "key", key=key_name, keycode=code)


@router.post("/{serial}/text")
def input_text(
    serial: str,
    payload: TextRequest,
    driver: Driver = Depends(get_driver_or_503),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    manual_run_id = _manual_state(serial, session)
    # 快路径：scrcpy control 注入文本；clear_first 走 u2（control 没有清空语义）
    if payload.clear_first:
        _device_call(serial, driver.clear_text)
        _device_call(serial, driver.input_text, payload.text)
    elif not _try_mirror(serial, "inject_text", text=payload.text):
        _device_call(serial, driver.input_text, payload.text)
    _after_action(
        serial, "input", {"text": payload.text, "clear_first": bool(payload.clear_first)}, None, manual_run_id
    )
    return _control_response(serial, "input", text=payload.text, clear_first=payload.clear_first)


@router.post("/{serial}/app")
def control_app(
    serial: str,
    payload: AppRequest,
    driver: Driver = Depends(get_driver_or_503),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    package = (payload.package or "").strip()
    if not package:
        raise ApiError("INVALID_ARGUMENT", "package is required for app action", {"action": payload.action})
    manual_run_id = _manual_state(serial, session)
    if payload.action == "launch":
        _device_call(serial, driver.app_start, package, payload.activity)
    elif payload.action == "stop":
        _device_call(serial, driver.app_stop, package)
    elif payload.action == "clear":
        _device_call(serial, driver.app_clear, package)
    elif payload.action == "restart":
        _device_call(serial, driver.app_stop, package)
        _device_call(serial, driver.app_start, package, payload.activity)
    _after_action(
        serial,
        "app",
        {"action": payload.action, "package": package, "activity": payload.activity},
        None,
        manual_run_id,
    )
    return _control_response(
        serial, "app", action=payload.action, package=package, activity=payload.activity
    )


# ---------------------------------------------------------------- 录屏
def _make_screen_recorder(screen_module: Any, serial: str, driver: Driver) -> Any:
    factory = getattr(screen_module, "create_screen_recorder", None)
    if callable(factory):
        try:
            return factory(driver)
        except Exception:
            logger.warning("create_screen_recorder(driver) failed, falling back to class", exc_info=True)
    recorder_cls = getattr(screen_module, "ScreenRecorder", None)
    if recorder_cls is None:
        raise ApiError("UNSUPPORTED", "screen recording is not available in current driver build")
    try:
        return recorder_cls(serial)
    except TypeError:
        return recorder_cls(serial=serial)


@router.post("/{serial}/screenrecord/start")
def screenrecord_start(
    serial: str,
    payload: ScreenRecordStartRequest,
    driver: Driver = Depends(get_driver_or_503),
) -> dict[str, Any]:
    screen_module = _screen_module()
    if screen_module is None:
        raise ApiError(
            "UNSUPPORTED", "screen recording requires app.android.screen (driver module not available yet)"
        )
    name = (payload.name or "").strip() or f"screenrecord-{serial}-{int(time.time())}"
    with _SCREEN_RECORDER_LOCK:
        if serial in _SCREEN_RECORDERS:
            raise ApiError("CONFLICT", "screen recording already in progress", {"serial": serial})
        recorder = _make_screen_recorder(screen_module, serial, driver)
        try:
            recorder.start(name=name, bit_rate=int(payload.bit_rate), time_limit_s=int(payload.time_limit_s))
        except ApiError:
            raise
        except Exception as exc:
            raise device_unavailable(f"screenrecord start failed: {exc}", {"serial": serial}) from exc
        _SCREEN_RECORDERS[serial] = recorder
    return {
        "ok": True,
        "serial": serial,
        "recording": True,
        "name": name,
        "time_limit_s": int(payload.time_limit_s),
    }


@router.post("/{serial}/screenrecord/stop")
def screenrecord_stop(serial: str) -> dict[str, Any]:
    with _SCREEN_RECORDER_LOCK:
        recorder = _SCREEN_RECORDERS.pop(serial, None)
    if recorder is None:
        raise ApiError("NOT_FOUND", "no active screen recording", {"serial": serial})
    try:
        result = recorder.stop()
    except ApiError:
        raise
    except Exception as exc:
        raise device_unavailable(f"screenrecord stop failed: {exc}", {"serial": serial}) from exc
    path, size = result if isinstance(result, tuple) else (result, 0)
    return {
        "ok": True,
        "serial": serial,
        "path": str(path),
        "size": int(size or 0),
        "name": Path(str(path)).name,
    }


# ---------------------------------------------------------------- 网络抓包（可选，M8）
def _ensure_network_enabled() -> None:
    if not config.get("capture.network.enabled"):
        raise ApiError(
            "UNSUPPORTED",
            "network capture is disabled (capture.network.enabled=false)",
            {"setting": "capture.network.enabled"},
        )


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


@router.post("/{serial}/network/start")
def network_start(serial: str) -> dict[str, Any]:
    _ensure_network_enabled()
    fn = _network_fn("start")
    if fn is None:
        raise ApiError("UNSUPPORTED", "network capture module is not available")
    try:
        result = fn(serial)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError("UNSUPPORTED", f"network capture failed to start: {exc}", {"serial": serial}) from exc
    return {"ok": True, "serial": serial, "started": True, "capture": _jsonable(result)}


@router.post("/{serial}/network/stop")
def network_stop(serial: str) -> dict[str, Any]:
    _ensure_network_enabled()
    fn = _network_fn("stop")
    if fn is None:
        raise ApiError("UNSUPPORTED", "network capture module is not available")
    try:
        result = fn(serial)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError("UNSUPPORTED", f"network capture failed to stop: {exc}", {"serial": serial}) from exc
    return {"ok": True, "serial": serial, "started": False, "capture": _jsonable(result)}


__all__ = ["router"]
