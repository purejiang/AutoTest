"""设备注册表（契约 §14.2）：serial -> Driver 缓存 + 设备发现 + 占用表。

设计要点：
- 真机：`app.android.adb.list_devices()` 发现，懒创建 `app.android.u2_driver.create_u2_driver()`；
  这两个模块由 be-driver 实现，缺失时**降级**（列表照常返回 fake 设备，取驱动报 DEVICE_UNAVAILABLE）；
- FakeDriver：serial 以 `fake` 开头（`FAKE_PREFIX`）时返回模块级单例，这是无真机联调入口；
  是否出现在 `GET /api/devices` 列表由 `_fake_listing_enabled()` 决定（无真机或 AUTOTEST_FAKE_DEVICE=1）；
- 占用表：run 与 recording 互斥，mirror 与两者兼容；同 purpose 同 ref 重复 acquire 幂等；
- 线程安全：全部状态在 `_LOCK` 内读写（API 在线程池里跑同步驱动）。
"""

from __future__ import annotations

import os
import threading
from typing import Any

from ..android.driver import DeviceInfo, Driver, FakeDriver
from ..errors import ApiError
from ..logs import logger

FAKE_PREFIX = "fake"
FAKE_DEFAULT_SERIAL = "fake-1"
PURPOSES: tuple[str, ...] = ("run", "recording", "mirror")
_BUSY_PURPOSES: tuple[str, ...] = ("run", "recording")

_LOCK = threading.RLock()
_DRIVERS: dict[str, Driver] = {}  # 真机 driver 缓存
_FAKE_DRIVERS: dict[str, FakeDriver] = {}  # fake 单例
_OCCUPANCY: dict[str, list[dict[str, Any]]] = {}  # serial -> [{purpose, ref_id}]


# ---------------------------------------------------------------- 外部模块探测（缺失即降级）
def _adb_module() -> Any | None:
    try:
        from ..android import adb  # noqa: PLC0415 - 队友模块可能尚未落地
    except ImportError:
        return None
    return adb


def _u2_factory() -> Any | None:
    try:
        from ..android import u2_driver  # noqa: PLC0415
    except ImportError:
        return None
    return getattr(u2_driver, "create_u2_driver", None)


def _mirror_module() -> Any | None:
    try:
        from .. import mirror  # noqa: PLC0415 - be-media 模块
    except ImportError:
        return None
    return mirror


def _is_fake(serial: str) -> bool:
    return str(serial).strip().lower().startswith(FAKE_PREFIX)


# ---------------------------------------------------------------- fake 设备
def _fake_driver(serial: str) -> FakeDriver:
    with _LOCK:
        driver = _FAKE_DRIVERS.get(serial)
        if driver is None:
            driver = FakeDriver(serial=serial)
            _FAKE_DRIVERS[serial] = driver
        return driver


# ---------------------------------------------------------------- 真机发现
def _real_devices() -> list[dict[str, Any]]:
    """adb 设备列表；adb 未就绪时返回 []（列表接口不能因此 500）。"""
    adb = _adb_module()
    if adb is None:
        return []
    try:
        found = adb.list_devices() or []
    except Exception:  # adb 二进制缺失/超时/输出异常
        logger.warning("adb list_devices failed", exc_info=True)
        return []
    return [dict(item) for item in found if isinstance(item, dict) and item.get("serial")]


def _online_serials() -> list[str]:
    return [
        str(item["serial"])
        for item in _real_devices()
        if str(item.get("state") or "").lower() == "device"
    ]


def _auto_serial() -> str:
    serials = _online_serials()
    if len(serials) == 1:
        return serials[0]
    if not serials:
        if _adb_module() is None:
            message = "adb module is not available yet; pass serial=\"fake-1\" for the offline fake device"
        else:
            message = "no online device found; pass serial=\"fake-1\" for the offline fake device"
        raise ApiError("DEVICE_UNAVAILABLE", message, {"devices": []})
    raise ApiError(
        "DEVICE_UNAVAILABLE",
        f"multiple online devices, specify serial explicitly: {serials}",
        {"devices": serials},
    )


# ---------------------------------------------------------------- 列表项字段（§7.1 / 前端 Device）
def _mirror_info(serial: str) -> dict[str, Any]:
    module = _mirror_module()
    fn = getattr(module, "mirror_status", None) if module is not None else None
    if not callable(fn):
        return {"active": False}
    try:
        status = fn(serial) or {}
    except Exception:  # 镜像层异常不能拖垮设备列表
        logger.warning("mirror_status failed for %s", serial, exc_info=True)
        return {"active": False}
    info: dict[str, Any] = {"active": bool(status.get("active"))}
    for key in ("width", "height", "rotation", "session_id", "max_fps"):
        if status.get(key) is not None:
            info[key] = status[key]
    return info


def _busy_reason(serial: str) -> str | None:
    with _LOCK:
        holders = [dict(entry) for entry in _OCCUPANCY.get(serial, []) if entry["purpose"] in _BUSY_PURPOSES]
    if not holders:
        return None
    return ", ".join(f"{entry['purpose']}:{entry['ref_id']}" for entry in holders)


def _empty_item(serial: str, state: str) -> dict[str, Any]:
    return {
        "serial": serial,
        "state": state,
        "model": "",
        "brand": "",
        "android_version": "",
        "sdk": 0,
        "screen_width": 0,
        "screen_height": 0,
        "rotation": 0,
        "mirror": {"active": False},
        "busy": False,
        "fake": False,
        "is_fake": False,
    }


def _apply_runtime_fields(item: dict[str, Any], serial: str) -> dict[str, Any]:
    item["mirror"] = _mirror_info(serial)
    reason = _busy_reason(serial)
    item["busy"] = reason is not None
    if reason is not None:
        item["busy_reason"] = reason
    return item


def _info_for(serial: str) -> DeviceInfo | None:
    with _LOCK:
        driver = _DRIVERS.get(serial)
    if driver is None:
        return None
    try:
        return driver.info()
    except Exception:
        logger.warning("driver.info failed for %s", serial, exc_info=True)
        return None


def _real_item(device: dict[str, Any]) -> dict[str, Any]:
    serial = str(device["serial"])
    item = _empty_item(serial, str(device.get("state") or "unknown"))
    # adb devices -l 只给 model/product/device/transport_id；其余字段需要 driver.info()
    for key in ("model", "product", "device", "transport_id", "usb"):
        if device.get(key) not in (None, ""):
            item[key] = device[key]
    info = _info_for(serial)
    if info is not None:
        for key, value in info.to_dict().items():
            item[key] = value
    return _apply_runtime_fields(item, serial)


def _fake_item(serial: str) -> dict[str, Any]:
    driver = _fake_driver(serial)
    item = _empty_item(serial, "device")
    try:
        item.update(driver.info().to_dict())
    except Exception:  # pragma: no cover - fake 永不失败
        pass
    item["fake"] = True
    item["is_fake"] = True
    item["model"] = item.get("model") or "Fake Device"
    return _apply_runtime_fields(item, serial)


_FAKE_FLAG_ENV = "AUTOTEST_FAKE_DEVICE"
_FAKE_FLAG_TRUE = ("1", "true", "yes", "on")


def fake_listing_enabled(online_real_count: int) -> bool:
    """fake 设备是否进入列表：无任何真实在线设备，或显式 AUTOTEST_FAKE_DEVICE=1。

    显式传 serial（如 `fake-1`）取驱动不受此开关影响，联调始终可用。
    """
    if str(os.getenv(_FAKE_FLAG_ENV, "")).strip().lower() in _FAKE_FLAG_TRUE:
        return True
    return online_real_count == 0


def list_devices(refresh: bool = False) -> list[dict[str, Any]]:
    """§7.1 GET /api/devices 的 items：真机在前；无真机（或强制开关）时附带 fake 演示设备。"""
    if refresh:
        with _LOCK:
            dead = [serial for serial, driver in _DRIVERS.items() if not _alive(driver)]
            for serial in dead:
                _DRIVERS.pop(serial, None)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for device in _real_devices():
        serial = str(device["serial"])
        if serial in seen:
            continue
        seen.add(serial)
        items.append(_real_item(device))
    online_real = sum(1 for item in items if str(item.get("state") or "").lower() == "device")
    if not fake_listing_enabled(online_real):
        return items
    with _LOCK:
        fake_serials = dict.fromkeys([FAKE_DEFAULT_SERIAL, *_FAKE_DRIVERS.keys()])
    for serial in fake_serials:
        if serial in seen:
            continue
        seen.add(serial)
        items.append(_fake_item(serial))
    return items


def _alive(driver: Driver) -> bool:
    try:
        return bool(driver.alive())
    except Exception:
        return False


# ---------------------------------------------------------------- 驱动获取
def get_driver(serial: str | None = None) -> Driver:
    """按 serial 取 Driver。

    - serial=None/"auto"：唯一在线真机（0 或多台 -> DEVICE_UNAVAILABLE，不猜）；
    - serial 以 "fake" 开头：模块级 FakeDriver 单例；
    - 真机：懒创建 u2 driver 并缓存（dead 自动重建）。
    """
    if serial is None or str(serial).strip() in ("", "auto"):
        resolved = _auto_serial()
    else:
        resolved = str(serial).strip()
    if _is_fake(resolved):
        return _fake_driver(resolved)
    with _LOCK:
        cached = _DRIVERS.get(resolved)
    if cached is not None:
        if _alive(cached):
            return cached
        evict(resolved)
    factory = _u2_factory()
    if factory is None:
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            "uiautomator2 driver module is not available yet (app.android.u2_driver missing)",
            {"serial": resolved},
        )
    try:
        driver = factory(resolved)
    except Exception as exc:
        logger.exception("create_u2_driver failed for %s", resolved)
        raise ApiError(
            "DEVICE_UNAVAILABLE", f"failed to create driver for {resolved}: {exc}", {"serial": resolved}
        ) from exc
    with _LOCK:
        _DRIVERS[resolved] = driver
    return driver


def evict(serial: str) -> None:
    """丢弃缓存（disconnect / driver 失效时调用）。fake 单例保留。"""
    with _LOCK:
        driver = _DRIVERS.pop(str(serial), None)
    if driver is not None:
        try:
            driver.close()
        except Exception:
            logger.debug("driver.close failed for %s", serial, exc_info=True)


# ---------------------------------------------------------------- 占用表
def acquire(serial: str, purpose: str, ref_id: Any) -> None:
    """占设备。run/recording 互斥、mirror 兼容；同 purpose 同 ref 幂等。"""
    if purpose not in PURPOSES:
        raise ApiError("INVALID_ARGUMENT", f"invalid purpose: {purpose}", {"purpose": purpose})
    resolved = str(serial)
    ref = str(ref_id)
    with _LOCK:
        holders = _OCCUPANCY.setdefault(resolved, [])
        for entry in holders:
            if entry["purpose"] == purpose and entry["ref_id"] == ref:
                return
        for entry in holders:
            if purpose == "mirror" or entry["purpose"] == "mirror":
                continue
            raise ApiError(
                "DEVICE_BUSY",
                f"device {resolved} is busy with {entry['purpose']} {entry['ref_id']}",
                {
                    "serial": resolved,
                    "purpose": purpose,
                    "holder": {"purpose": entry["purpose"], "ref_id": entry["ref_id"]},
                },
            )
        holders.append({"purpose": purpose, "ref_id": ref})


def release(serial: str, purpose: str, ref_id: Any) -> None:
    """释放占用；不存在时静默（幂等）。"""
    resolved = str(serial)
    ref = str(ref_id)
    with _LOCK:
        holders = _OCCUPANCY.get(resolved)
        if not holders:
            return
        remaining = [e for e in holders if not (e["purpose"] == purpose and e["ref_id"] == ref)]
        if remaining:
            _OCCUPANCY[resolved] = remaining
        else:
            _OCCUPANCY.pop(resolved, None)


def occupancy(serial: str) -> list[dict[str, Any]]:
    """[{purpose, ref_id}] 快照。"""
    with _LOCK:
        return [dict(entry) for entry in _OCCUPANCY.get(str(serial), [])]


def is_busy(serial: str) -> bool:
    """是否被 run/recording 占用（mirror 不算 busy）。"""
    return _busy_reason(str(serial)) is not None


def busy_reason(serial: str) -> str | None:
    """当前占用描述（给 §7.1 列表项与 info 用）；空闲返回 None。"""
    return _busy_reason(str(serial))


def mirror_info(serial: str) -> dict[str, Any]:
    """镜像状态摘要（be-media 未就绪时返回 {"active": False}）。"""
    return _mirror_info(str(serial))


def busy_purposes(serial: str) -> list[str]:
    with _LOCK:
        return [entry["purpose"] for entry in _OCCUPANCY.get(str(serial), [])]


# ---------------------------------------------------------------- 测试辅助
def reset_for_tests() -> None:
    """清空缓存与占用表（pytest 每个用例独立状态）。"""
    with _LOCK:
        drivers = list(_DRIVERS.values())
        _DRIVERS.clear()
        _FAKE_DRIVERS.clear()
        _OCCUPANCY.clear()
    for driver in drivers:
        try:
            driver.close()
        except Exception:
            pass


__all__ = [
    "FAKE_DEFAULT_SERIAL",
    "FAKE_PREFIX",
    "PURPOSES",
    "acquire",
    "busy_purposes",
    "busy_reason",
    "evict",
    "fake_listing_enabled",
    "get_driver",
    "is_busy",
    "list_devices",
    "mirror_info",
    "occupancy",
    "release",
    "reset_for_tests",
]
