"""uiautomator2 驱动实现（契约 §4：Driver 协议的真机实现）。

要点：
- **懒连接**：``__init__`` 只保存 serial，首次真正用到设备时才 ``uiautomator2.connect(serial)``；
  连接失败统一 ``ApiError("DEVICE_UNAVAILABLE")``；
- ``dump_hierarchy``：u2 dump XML -> ``hierarchy.parse_xml``（额外暴露 ``dump_hierarchy_raw``）；
- ``screenshot``：走 ``adb exec-out screencap -p``（比 u2 的 jsonrpc 截图快且稳）；
- ``info/rotation``：u2 ``device_info`` + ``wm size``；rotation 为角度（0/90/180/270）；
- ``clear_text``：先用 u2 能力，失败降级为「光标移到末尾 + 多次 KEYCODE_DEL」；
- 无真机时：``alive()`` **返回 False 而不抛**（连接失败的 ApiError 被吞并记 debug 日志），
  其余动作照常抛 ``ApiError("DEVICE_UNAVAILABLE")``。

uiautomator2 3.7.1 实际 API（已核对安装包源码）：
``connect(serial) -> Device``；``Device.dump_hierarchy() -> str``；``click(x, y)``；
``swipe(fx, fy, tx, ty, duration=<秒>, steps=<每步约5ms>)``；``long_click(x, y, duration=<秒>)``；
``drag(sx, sy, ex, ey, duration=<秒>)``；``press(int|str)``；``send_keys(text)``；
``clear_text()``；``shell(cmd, timeout=60) -> ShellResponse(output, exit_code)``；
``device_info`` 是 property（getprop）；``info`` 是 property（jsonrpc，含 displayRotation）；
``window_size() -> (w, h)``；``app_start/app_stop/app_clear``；``unlock()``。
"""

from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any

from ..errors import ApiError
from ..logs import logger
from . import adb, hierarchy
from .driver import KEY_CODES, CommandResult, DeviceInfo, HierarchyNode, resolve_key

_TAG_NAME = "node"
_SIZE_RE_OVERRIDE = re.compile(r"Override size:\s*(\d+)x(\d+)")
_SIZE_RE_PHYSICAL = re.compile(r"Physical size:\s*(\d+)x(\d+)")


class U2Driver:
    """uiautomator2 实现的 Driver（同步接口，在线程池中调用）。"""

    # 供 actions.press_key 等复用；与 driver.KEY_CODES 一致
    KEY_CODES = KEY_CODES

    def __init__(self, serial: str) -> None:
        self.serial = str(serial)
        self._device_obj: Any = None
        self._lock = threading.RLock()
        self._closed = False

    # ------------------------------------------------------------ 连接管理
    def __repr__(self) -> str:
        return f"U2Driver(serial={self.serial!r})"

    @property
    def device(self) -> Any:
        """懒连接后的 uiautomator2 Device（首次访问会尝试连接）。"""
        return self._device()

    def _device(self) -> Any:
        if self._closed:
            raise ApiError("DEVICE_UNAVAILABLE", f"driver for {self.serial} is closed")
        with self._lock:
            if self._device_obj is not None:
                return self._device_obj
            try:
                import uiautomator2 as u2  # 延迟导入：模块 import 不触发 adb
                device = u2.connect(self.serial)
            except Exception as exc:
                raise ApiError(
                    "DEVICE_UNAVAILABLE",
                    f"uiautomator2 connect failed for {self.serial}: {exc}",
                    {"serial": self.serial, "reason": type(exc).__name__},
                ) from exc
            self._device_obj = device
            return device

    def _call(self, op: str, func: Any, *args: Any, **kwargs: Any) -> Any:
        """执行一个 u2 调用并归一化异常。"""
        try:
            return func(*args, **kwargs)
        except ApiError:
            raise
        except ValueError as exc:
            raise ApiError("INVALID_ARGUMENT", f"{op}: {exc}", {"op": op}) from exc
        except Exception as exc:
            raise ApiError(
                "DEVICE_UNAVAILABLE",
                f"u2 {op} failed on {self.serial}: {exc}",
                {"serial": self.serial, "op": op, "reason": type(exc).__name__},
            ) from exc

    # ------------------------------------------------------------ 设备信息
    def alive(self) -> bool:
        """设备是否可用；无设备/连接失败返回 False（不抛）。

        快路径用 `adb get-state`（实测 ~20ms）；u2 的 window_size RPC 要 ~195ms，
        而 alive() 在每次 API 请求的依赖解析里都会调，必须廉价。
        """
        if self._closed:
            return False
        try:
            result = adb.run_adb(["-s", self.serial, "get-state"], timeout_s=2)
            state = (getattr(result, "stdout", "") or "").strip().lower()
            if state == "device":
                return True
            if state in ("offline", "unauthorized", "unknown", "no", "bootloader"):
                return False
        except Exception:
            logger.debug("adb get-state failed for %s", self.serial, exc_info=True)
        try:
            device = self._device()
            device.window_size()
            return True
        except Exception as exc:
            logger.debug("U2Driver.alive(%s) -> False: %s", self.serial, exc)
            return False

    _INFO_TTL_S = 5.0

    def info(self) -> DeviceInfo:
        """设备信息：进程内 5s TTL 缓存（3 次 RPC ≈ 580ms，列表/预检会频繁用到）。"""
        now = time.monotonic()
        cached = getattr(self, "_info_cache", None)
        if cached is not None and now - cached[0] < self._INFO_TTL_S:
            return cached[1]
        result = self._call("info", self._info_impl)
        self._info_cache = (now, result)
        return result

    def _info_impl(self) -> DeviceInfo:
        device = self._device()
        device_info: dict[str, Any] = {}
        try:
            device_info = dict(device.device_info or {})
        except Exception as exc:
            logger.debug("u2 device_info failed for %s: %s", self.serial, exc)
        window_info: dict[str, Any] = {}
        try:
            window_info = dict(device.info or {})
        except Exception as exc:
            logger.debug("u2 info failed for %s: %s", self.serial, exc)
        width, height = self.wm_size()
        try:
            sdk = int(device_info.get("sdk") or 0)
        except (TypeError, ValueError):
            sdk = 0
        try:
            rotation = int(window_info.get("displayRotation", 0) or 0) * 90
        except (TypeError, ValueError):
            rotation = 0
        return DeviceInfo(
            serial=self.serial,
            model=str(device_info.get("model") or ""),
            brand=str(device_info.get("brand") or ""),
            android_version=str(device_info.get("version") or ""),
            sdk=sdk,
            screen_width=width,
            screen_height=height,
            rotation=rotation % 360,
        )

    def wm_size(self) -> tuple[int, int]:
        return self._call("wm_size", self._wm_size_impl)

    def _wm_size_impl(self) -> tuple[int, int]:
        device = self._device()
        try:
            size = device.window_size()
            if size and len(size) == 2:
                return int(size[0]), int(size[1])
        except Exception as exc:
            logger.debug("u2 window_size failed for %s: %s", self.serial, exc)
        output = self.shell("wm size", timeout_s=5).stdout
        match = _SIZE_RE_OVERRIDE.search(output) or _SIZE_RE_PHYSICAL.search(output)
        if not match:
            raise ApiError(
                "DEVICE_UNAVAILABLE",
                f"cannot determine screen size for {self.serial}: {output.strip()!r}",
                {"serial": self.serial},
            )
        return int(match.group(1)), int(match.group(2))

    def rotation(self) -> int:
        return self._call("rotation", self._rotation_impl)

    def _rotation_impl(self) -> int:
        device = self._device()
        info = dict(device.info or {})
        try:
            return (int(info.get("displayRotation", 0) or 0) * 90) % 360
        except (TypeError, ValueError):
            return 0

    def dump_hierarchy(self) -> HierarchyNode:
        return hierarchy.parse_xml(self.dump_hierarchy_raw())

    def dump_hierarchy_raw(self) -> str:
        """原始 uiautomator XML（契约外扩展，供 API 层 ``?raw=true`` 使用）。"""
        device = self._device()
        return self._call("dump_hierarchy", device.dump_hierarchy)

    def current_app(self) -> dict[str, Any]:
        """当前前台应用：优先 adb dumpsys（实测 ~0.13s），u2 的 app_current 在部分 ROM 上要 ~5.4s。

        真机基准（Xiaomi/Android 16）：dumpsys window 0.125s vs u2.app_current 5.4s；
        前台判断被引擎大量使用（assert foreground / app 节点 / resume-check），必须走快路径。
        """
        try:
            fast = self._current_app_adb()
            if fast and fast.get("package"):
                return fast
        except Exception:  # adb 失败不致命，回落 u2
            logger.debug("adb current_app failed for %s", self.serial, exc_info=True)
        return self._call("current_app", self._current_app_impl)

    _FOCUS_RE = re.compile(
        r"mCurrentFocus=Window\{[0-9a-fA-F]+ u\d+ (?P<pkg>[^\s/]+)/(?P<act>[^}\s]+)\}"
    )
    _RESUMED_RE = re.compile(
        r"(?:topResumedActivity|mResumedActivity)=ActivityRecord\{[^}]*?\s(?P<pkg>[^\s/]+)/(?P<act>[^\s}]+)"
    )

    def _current_app_adb(self) -> dict[str, Any] | None:
        result = adb.shell(
            self.serial,
            "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp' | head -2",
            timeout_s=5,
        )
        text = result.stdout or ""
        match = self._FOCUS_RE.search(text) or self._RESUMED_RE.search(text)
        if not match:
            result = adb.shell(
                self.serial,
                "dumpsys activity activities | grep -E 'topResumedActivity|mResumedActivity' | head -2",
                timeout_s=5,
            )
            match = self._RESUMED_RE.search(result.stdout or "")
            if not match:
                return None
        package = match.group("pkg")
        activity = match.group("act")
        if activity.startswith("."):
            activity = f"{package}{activity}"
        return {"package": package, "activity": activity}

    def _current_app_impl(self) -> dict[str, Any]:
        device = self._device()
        data = dict(device.app_current() or {})
        result: dict[str, Any] = {
            "package": str(data.get("package") or ""),
            "activity": data.get("activity") or None,
        }
        if "pid" in data:
            result["pid"] = data["pid"]
        return result

    def screenshot(self) -> bytes:
        """PNG bytes：adb exec-out screencap -p（二进制安全，比 u2 截图快）。"""
        return adb.shell_bytes(self.serial, ["screencap", "-p"], timeout_s=20)

    # ------------------------------------------------------------ 输入动作
    def click(self, x: int, y: int) -> None:
        device = self._device()
        self._call("click", device.click, int(x), int(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        device = self._device()
        # u2 的 1 step ≈ 5ms；调用方给时长，驱动侧换算成 steps（≥5）
        steps = max(5, int(round(max(0, int(duration_ms)) / 5.0)))
        self._call("swipe", device.swipe, int(x1), int(y1), int(x2), int(y2), steps=steps)

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 1000) -> None:
        """拖拽（契约外扩展，actions.drag 优先使用；u2 drag duration 单位为秒）。"""
        device = self._device()
        duration = max(0.1, max(0, int(duration_ms)) / 1000.0)
        self._call("drag", device.drag, int(x1), int(y1), int(x2), int(y2), duration)

    def long_press(self, x: int, y: int, duration_ms: int = 800) -> None:
        device = self._device()
        duration = max(0.1, max(0, int(duration_ms)) / 1000.0)
        self._call("long_press", device.long_click, int(x), int(y), duration)

    def key(self, name_or_code: str | int) -> None:
        try:
            code = resolve_key(name_or_code)
        except ValueError as exc:
            raise ApiError("INVALID_ARGUMENT", str(exc), {"key": name_or_code}) from exc
        device = self._device()
        self._call("key", device.press, code)

    def input_text(self, text: str) -> None:
        device = self._device()
        self._call("input_text", device.send_keys, str(text))

    def clear_text(self) -> None:
        """优先 u2 ``clear_text``；失败降级为「移到末尾 + 多次 DEL」（不静默吞错）。"""
        device = self._device()
        try:
            device.clear_text()
            return
        except Exception as exc:
            logger.warning(
                "u2 clear_text failed on %s (%s); fallback to keyevent deletes", self.serial, exc
            )
        self._fallback_clear_text(device)

    def _fallback_clear_text(self, device: Any) -> None:
        def _impl() -> None:
            device = self._device()
            count = self._focused_text_length(device)
            device.shell("input keyevent KEYCODE_MOVE_END")
            for _ in range(max(1, min(count + 1, 200))):
                device.press(67)  # KEYCODE_DEL

        self._call("clear_text_fallback", _impl)

    def _focused_text_length(self, device: Any) -> int:
        """从 dump 里找 focused="true" 节点的 text 长度，用于估算删除次数（缺省 50）。"""
        try:
            raw = device.dump_hierarchy()
            root = ET.fromstring(raw)
        except Exception as exc:
            logger.debug("fallback clear_text: dump failed (%s), delete 50 times", exc)
            return 50
        for element in root.iter(_TAG_NAME):
            attrs = element.attrib
            if attrs.get("focused") == "true":
                return len(attrs.get("text") or "")
        return 50

    # ------------------------------------------------------------ 应用/系统
    def app_start(self, package: str, activity: str | None = None) -> None:
        device = self._device()
        self._call("app_start", device.app_start, str(package), activity)

    def app_stop(self, package: str) -> None:
        device = self._device()
        self._call("app_stop", device.app_stop, str(package))

    def app_clear(self, package: str) -> None:
        device = self._device()
        self._call("app_clear", device.app_clear, str(package))

    def shell(self, command: str, timeout_s: float = 10) -> CommandResult:
        """u2 shell -> CommandResult；u2 的 ShellResponse 无独立 stderr（合并在 output）。"""

        def _impl() -> CommandResult:
            device = self._device()
            response = device.shell(command, timeout=timeout_s)
            output = getattr(response, "output", "") or ""
            raw_code = getattr(response, "exit_code", 0)
            try:
                code = int(raw_code)
            except (TypeError, ValueError):
                code = 0
            return CommandResult(code=code, stdout=str(output), stderr="")

        return self._call("shell", _impl)

    def unlock(self) -> None:
        device = self._device()
        self._call("unlock", device.unlock)

    def close(self) -> None:
        """释放连接引用（不杀 u2 server，避免影响其他会话）。"""
        with self._lock:
            self._closed = True
            device = self._device_obj
            self._device_obj = None
        if device is not None:
            closer = getattr(device, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception as exc:  # 关闭失败不影响调用方
                    logger.debug("u2 device close failed for %s: %s", self.serial, exc)


def create_u2_driver(serial: str) -> U2Driver:
    """工厂（契约 §14.1）：返回懒连接的 U2Driver。"""
    return U2Driver(serial)
