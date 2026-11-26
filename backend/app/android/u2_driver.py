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

import io
import re
import shlex
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
        """PNG bytes：adb exec-out screencap -p（二进制安全，比 u2 截图快）。

        adb screencap 偶发返回空/截断数据（部分机型高负载、横竖屏切换、Unity 大画面时），
        这里校验 PNG 头并重试 3 次，仍失败再回落 u2 jsonrpc 截图，避免整次运行被一张截图拖挂。
        """
        last_error: Exception | None = None
        for _ in range(3):
            try:
                raw = adb.shell_bytes(self.serial, ["screencap", "-p"], timeout_s=20)
            except Exception as exc:  # noqa: BLE001 - 统一重试
                last_error = exc
                raw = b""
            if isinstance(raw, (bytes, bytearray)) and bytes(raw).startswith(b"\x89PNG\r\n\x1a\n"):
                return bytes(raw)
            last_error = RuntimeError(
                f"adb screencap returned invalid data ({len(raw) if isinstance(raw, (bytes, bytearray)) else 'non-bytes'} bytes)"
            )
            time.sleep(0.15)
        device = self._device()
        try:
            # u2 的 screenshot(format=...) 只支持 pillow/opencv，转成 PNG bytes
            image = device.screenshot(format="pillow")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            raw = buffer.getvalue()
            if isinstance(raw, (bytes, bytearray)) and bytes(raw).startswith(b"\x89PNG\r\n\x1a\n"):
                return bytes(raw)
            last_error = RuntimeError("u2 screenshot returned invalid PNG")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        raise ApiError(
            "DEVICE_UNAVAILABLE",
            f"screenshot failed on {self.serial}: {last_error}",
            {"serial": self.serial},
        )

    # ------------------------------------------------------------ 输入动作
    # ------------------------------------------------------------ 注入（adb first）
    # 为什么不用 u2 的 click/swipe/press：uiautomator2 服务端在注入后会同步等「界面空闲」，
    # 对 Unity/常驻动画的界面要等满 ~10s 才返回（实测 click/swipe/press 都是 10s+）；
    # 引擎默认节点超时 8s，会出现「点击已生效但节点被判 STEP_TIMEOUT」。
    # `adb shell input` 直接注入输入事件、不等待空闲，实测 ~0.1s；失败时回落 u2。
    def _adb_input(self, params: list[str], op: str, timeout_s: float = 15) -> None:
        result = adb.shell(self.serial, ["input", *params], timeout_s=timeout_s)
        code = int(getattr(result, "code", 0) or 0)
        if code != 0:
            detail = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
            raise RuntimeError(f"adb input {params[0]} failed (code={code}): {detail[:200]}")

    def click(self, x: int, y: int) -> None:
        def _impl() -> None:
            try:
                self._adb_input(["tap", str(int(x)), str(int(y))], "click")
            except Exception as exc:  # 老设备/受限 ROM 兜底
                logger.warning("adb input tap failed on %s (%s); fallback to u2 click", self.serial, exc)
                device = self._device()
                device.click(int(x), int(y))

        self._call("click", _impl)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        duration = max(1, int(duration_ms))

        def _impl() -> None:
            try:
                self._adb_input(
                    ["swipe", str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(duration)],
                    "swipe",
                )
            except Exception as exc:
                logger.warning("adb input swipe failed on %s (%s); fallback to u2 swipe", self.serial, exc)
                device = self._device()
                # u2 的 1 step ≈ 5ms；调用方给时长，驱动侧换算成 steps（≥5）
                steps = max(5, int(round(duration / 5.0)))
                device.swipe(int(x1), int(y1), int(x2), int(y2), steps=steps)

        self._call("swipe", _impl)

    def swipe_path(self, points: list[tuple[int, int]], duration_ms: int = 300) -> None:
        """按录制轨迹滑动；无镜像会话时退化为 adb 两点 swipe。

        有镜像时引擎走 scrcpy control 通道逐点回放（保留轨迹/变速），本方法只做兜底；
        兜底不再用 u2 `swipe_points`（它会等界面空闲 ~10s，常驻动画界面直接超时）。
        """
        pts = [(int(x), int(y)) for x, y in points]
        if len(pts) < 2:
            return
        self.swipe(pts[0][0], pts[0][1], pts[-1][0], pts[-1][1], duration_ms=duration_ms)

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 1000) -> None:
        """拖拽（契约外扩展，actions.drag 优先使用）：adb input swipe（不等界面空闲）。"""
        duration = max(100, int(duration_ms))

        def _impl() -> None:
            try:
                self._adb_input(
                    ["swipe", str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(duration)],
                    "drag",
                )
            except Exception as exc:
                logger.warning("adb drag failed on %s (%s); fallback to u2 drag", self.serial, exc)
                device = self._device()
                device.drag(int(x1), int(y1), int(x2), int(y2), duration / 1000.0)

        self._call("drag", _impl)

    def long_press(self, x: int, y: int, duration_ms: int = 800) -> None:
        duration = max(100, min(int(duration_ms), 60_000))

        def _impl() -> None:
            try:
                # adb input swipe 同点同坐标 + 按压时长 = 长按
                self._adb_input(
                    ["swipe", str(int(x)), str(int(y)), str(int(x)), str(int(y)), str(duration)],
                    "long_press",
                )
            except Exception as exc:
                logger.warning("adb long press failed on %s (%s); fallback to u2 long_click", self.serial, exc)
                device = self._device()
                device.long_click(int(x), int(y), duration / 1000.0)

        self._call("long_press", _impl)

    def key(self, name_or_code: str | int) -> None:
        try:
            code = resolve_key(name_or_code)
        except ValueError as exc:
            raise ApiError("INVALID_ARGUMENT", str(exc), {"key": name_or_code}) from exc

        def _impl() -> None:
            try:
                self._adb_input(["keyevent", str(int(code))], "key")
            except Exception as exc:
                logger.warning("adb keyevent failed on %s (%s); fallback to u2 press", self.serial, exc)
                device = self._device()
                device.press(code)

        self._call("key", _impl)

    def input_text(self, text: str) -> None:
        """输入文本：ASCII 走 adb ``input text``；非 ASCII 用无障碍 ``set_text``。

        为什么不用 u2 ``send_keys``：它实现是「写剪贴板 + 注入 Ctrl+V 粘贴」，
        部分机型/游戏输入框上粘贴不生效，Ctrl+V 被当成普通按键只输入一个 "v"
        （实测 send_keys('zzz111') 后输入框只有 "v"）。adb input text 实测正确；
        中文等非 ASCII 字符 adb 不支持，用无障碍 ACTION_SET_TEXT。
        """
        text = str(text)
        if not text:
            return

        def _impl() -> None:
            if all(ord(char) < 128 for char in text):
                try:
                    # input text 用 %s 表示空格；shlex.quote 防止 & ; | 等被设备 shell 解释
                    escaped = text.replace(" ", "%s")
                    self._adb_input(["text", shlex.quote(escaped)], "input_text")
                    return
                except Exception as exc:
                    logger.warning(
                        "adb input text failed on %s (%s); fallback to u2 send_keys", self.serial, exc
                    )
                self._device().send_keys(text)
                return
            device = self._device()
            try:
                device(focused=True).set_text(text)
            except Exception as exc:
                logger.warning(
                    "set_text failed on %s (%s); fallback to u2 send_keys", self.serial, exc
                )
                device.send_keys(text)

        self._call("input_text", _impl)

    def clear_text(self) -> None:
        """清空当前焦点输入框：移到末尾 + 批量 DEL（一次 adb keyevent，不等界面空闲）。"""

        def _impl() -> None:
            device = self._device()
            count = self._focused_text_length(device)
            # KEYCODE_MOVE_END=123, KEYCODE_DEL=67；input keyevent 支持一次传多个键码
            codes = ["123", *(["67"] * max(1, min(count + 1, 200)))]
            try:
                self._adb_input(["keyevent", *codes], "clear_text", timeout_s=20)
            except Exception as exc:
                logger.warning("adb clear_text failed on %s (%s); fallback to u2 clear_text", self.serial, exc)
                device.clear_text()

        self._call("clear_text", _impl)

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
    def _runtime_permissions(self, package: str) -> list[str]:
        """dumpsys package 里该应用声明的运行时权限（含已授予/未授予，如通知/存储/相机）。"""
        result = adb.shell(self.serial, ["dumpsys", "package", str(package)], timeout_s=15)
        text = str(getattr(result, "stdout", "") or "")
        permissions: list[str] = []
        in_section = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("runtime permissions:"):
                in_section = True
                continue
            if not in_section:
                continue
            if not stripped:
                break
            name, _, rest = stripped.partition(":")
            if "granted=" in rest and "." in name and name not in permissions:
                permissions.append(name)
        return permissions

    def _grant_permissions(self, package: str, permissions: list[str]) -> list[str]:
        """best effort：逐个 ``pm grant``，失败的（如厂商私有权限）记 debug 跳过。"""
        granted: list[str] = []
        for permission in permissions:
            result = adb.shell(self.serial, ["pm", "grant", str(package), permission], timeout_s=10)
            code = int(getattr(result, "code", 0) or 0)
            detail = str(getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
            if code == 0 and not detail.lower().startswith("error"):
                granted.append(permission)
            else:
                logger.debug(
                    "grant %s on %s failed (code=%s): %s", permission, package, code, detail[:160]
                )
        return granted

    def hide_keyboard(self) -> None:
        """收起输入法：确认 IME 正在显示才发返回键，避免误触发页面返回。"""

        def _impl() -> None:
            result = adb.shell(self.serial, ["dumpsys", "input_method"], timeout_s=10)
            text = str(getattr(result, "stdout", "") or "")
            if "mInputShown=true" not in text:
                return
            self.key("back")

        self._call("hide_keyboard", _impl)

    def app_start(self, package: str, activity: str | None = None) -> None:
        device = self._device()
        self._call("app_start", device.app_start, str(package), activity)

    def app_stop(self, package: str) -> None:
        device = self._device()
        self._call("app_stop", device.app_stop, str(package))

    def app_clear(self, package: str) -> None:
        """清除数据，清完自动把该应用声明的运行时权限重新授予。

        ``pm clear`` 会把运行时权限重置成「未授予」，下次启动 App 就会弹系统授权框
        （通知/存储/相机…），把登录页挡在后面，脚本等的元素永远不出现。
        自动化场景下清数据后保持权限已授予，保证每次冷启动可复现。
        """
        device = self._device()

        def _impl() -> None:
            permissions = self._runtime_permissions(str(package))
            device.app_clear(str(package))
            granted = self._grant_permissions(str(package), permissions)
            if permissions:
                logger.info(
                    "app_clear %s: re-granted %d/%d runtime permissions",
                    package,
                    len(granted),
                    len(permissions),
                )

        self._call("app_clear", _impl)

    def app_grant(self, package: str) -> list[str]:
        """补齐该应用声明的全部运行时权限（best effort），返回成功授予的列表。"""
        package = str(package)
        permissions = self._runtime_permissions(package)
        if not permissions:
            return []
        granted = self._grant_permissions(package, permissions)
        logger.info("app_grant %s: granted %d/%d runtime permissions", package, len(granted), len(permissions))
        return granted

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
