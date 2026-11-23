"""录制会话（契约 §8 / §14.5）。

两种录制模式：
- `WebRecorderSession`：镜像/手动注入路径。API 层通过 `app.recorder.hooks` 调用
  `before_pointer()`（注入前抓层级快照）与 `handle()`（注入后留痕），本类负责归因、
  落库 `recording_events`、维护 `recordings.event_count`、向 WS 订阅者发布事件；
- `PhysicalRecorder`：真机物理触摸。消费 `app.android.getevent.stream_events` 的
  TouchSample / KeyEvent，把 down/move/up 归并为 tap/long_press/swipe/drag，
  坐标经 `app.android.touchmap` 映射（不可用时恒等），触摸设备/时钟锚点/标定写入
  `recordings.meta`。

模块级会话管理：`create()/get()/stop()/pause()/resume()/shutdown_all()/to_script()`。
规则：短事务 + `(recording_id, seq)` 唯一约束冲突重试 ≤3；暂停时事件直接丢弃，
不落库（只累计 dropped 计数，停止时写入 meta）。
"""

from __future__ import annotations

import asyncio
import math
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .. import config
from ..android.driver import KEY_CODES, resolve_key
from ..db import session_scope
from ..errors import ApiError
from ..logs import logger
from ..models import Recording, RecordingEvent, Script, now_iso
from ..schemas import validate_script
from . import convert, hooks

DriverGetter = Callable[[str], Any]

# 层级采样缓存有效期：超过则 before_pointer 退回同步 dump
_HIER_CACHE_MAX_AGE_MS = 1500

_KEYCODE_TO_NAME: dict[int, str] = {code: name for name, code in KEY_CODES.items()}

# Linux input-event-codes（getevent KeyEvent.keycode 口径）-> 契约 §3 KeyName。
# be-media 的 getevent 产出 Linux key code（如 KEY_BACK=158），与 Android keycode 不同。
_LINUX_KEYCODE_TO_NAME: dict[int, str] = {
    158: "back",  # KEY_BACK
    102: "home",  # KEY_HOME
    580: "recents",  # KEY_APP_SWITCH
    139: "menu",  # KEY_MENU
    115: "volume_up",  # KEY_VOLUMEUP
    114: "volume_down",  # KEY_VOLUMEDOWN
    116: "power",  # KEY_POWER
    28: "enter",  # KEY_ENTER
    111: "delete",  # KEY_DELETE
    444: "notification",  # KEY_NOTIFICATION_CENTER
}
_SERIAL_RE = re.compile(r"[^A-Za-z0-9_.-]+")


# ================================================================ 事件总线（WS 用）
class RecordingEventBus:
    """录制事件内存 pubsub：写库成功后发布；WS 端订阅队列 + 断线 replay。

    事件帧与契约 §6.3 对齐：{"kind":"event","seq","type","payload","confidence","ts"}。
    """

    _subs: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}
    _lock = threading.Lock()

    @classmethod
    def publish(cls, recording_id: str, frame: dict[str, Any]) -> None:
        with cls._lock:
            subscribers = list(cls._subs.get(recording_id, ()))
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(_safe_put, queue, frame)
            except RuntimeError:  # 事件循环已关闭 -> 摘除订阅
                cls.unsubscribe(recording_id, queue)

    @classmethod
    def subscribe(cls, recording_id: str) -> asyncio.Queue:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        with cls._lock:
            cls._subs.setdefault(recording_id, []).append((loop, queue))
        return queue

    @classmethod
    def unsubscribe(cls, recording_id: str, queue: asyncio.Queue) -> None:
        with cls._lock:
            subscribers = [item for item in cls._subs.get(recording_id, []) if item[1] is not queue]
            if subscribers:
                cls._subs[recording_id] = subscribers
            else:
                cls._subs.pop(recording_id, None)

    @classmethod
    def replay(cls, recording_id: str, since_seq: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
        with session_scope() as db:
            statement = (
                select(RecordingEvent)
                .where(RecordingEvent.recording_id == recording_id, RecordingEvent.seq > int(since_seq or 0))
                .order_by(RecordingEvent.seq)
            )
            if limit:
                statement = statement.limit(int(limit))
            rows = list(db.scalars(statement))
        return [
            {
                "kind": "event",
                "seq": row.seq,
                "type": row.kind,
                "payload": row.payload or {},
                "confidence": row.confidence,
                "ts": row.created_at,
            }
            for row in rows
        ]


def _safe_put(queue: asyncio.Queue, frame: dict[str, Any]) -> None:
    try:
        queue.put_nowait(frame)
    except asyncio.QueueFull:  # pragma: no cover - 无界队列不会触发
        pass


# ================================================================ DB：事件追加/计数
def _append_event(
    recording_id: str,
    kind: str,
    payload: dict[str, Any],
    confidence: str = convert.CONFIDENCE_HIGH,
) -> dict[str, Any] | None:
    """短事务分配 seq 并落库；暂停/已停止/不存在时返回 None。冲突重试 ≤3。"""
    recording_id = str(recording_id)
    for attempt in range(3):
        try:
            with session_scope() as db:
                recorder = db.get(Recording, recording_id)
                if recorder is None:
                    return None
                if recorder.status not in ("recording",):
                    return None
                max_seq = db.scalar(
                    select(func.max(RecordingEvent.seq)).where(RecordingEvent.recording_id == recording_id)
                )
                total = db.scalar(
                    select(func.count()).select_from(RecordingEvent).where(RecordingEvent.recording_id == recording_id)
                )
                seq = int(max_seq or 0) + 1
                created_at = now_iso()
                db.add(
                    RecordingEvent(
                        recording_id=recording_id,
                        seq=seq,
                        kind=str(kind),
                        payload=payload,
                        confidence=str(confidence),
                        created_at=created_at,
                    )
                )
                recorder.event_count = int(total or 0) + 1
                db.flush()
        except IntegrityError:  # 并发分配撞唯一约束 -> 重试
            logger.debug("recording event seq conflict, retry %s/%s (%s)", attempt + 1, 3, recording_id)
            time.sleep(0.01 * (attempt + 1))
            continue
        frame = {
            "kind": "event",
            "seq": seq,
            "type": str(kind),
            "payload": payload,
            "confidence": str(confidence),
            "ts": created_at,
        }
        RecordingEventBus.publish(recording_id, frame)
        return frame
    logger.warning("recording event seq allocation failed after retries: %s", recording_id)
    return None


def _recompute_event_count(recording_id: str) -> int:
    with session_scope() as db:
        total = db.scalar(
            select(func.count()).select_from(RecordingEvent).where(RecordingEvent.recording_id == recording_id)
        )
        recorder = db.get(Recording, recording_id)
        if recorder is not None:
            recorder.event_count = int(total or 0)
        return int(total or 0)


def _update_recording(recording_id: str, **fields: Any) -> None:
    with session_scope() as db:
        recorder = db.get(Recording, recording_id)
        if recorder is None:
            return
        for key, value in fields.items():
            setattr(recorder, key, value)


# ================================================================ 驱动获取
def _default_driver_getter(serial: str) -> Any:
    """默认从 services.device_registry 取驱动；未装配（单测）时返回 None。"""
    try:
        from ..services.device_registry import get_driver

        return get_driver(serial)
    except Exception:  # pragma: no cover - 依赖 be-core 装配
        return None


# ================================================================ 基础会话
class _BaseRecorder:
    mode = "web"
    recording_id: str

    def __init__(
        self,
        recording_id: str,
        serial: str,
        driver_getter: DriverGetter | None = None,
    ) -> None:
        self.recording_id = str(recording_id)
        self.serial = str(serial)
        self.driver_getter: DriverGetter = driver_getter or _default_driver_getter
        self.paused = False
        self.dropped = 0
        self.meta: dict[str, Any] = {"mode": self.mode}
        self.created_at = now_iso()

    def _driver(self) -> Any:
        try:
            return self.driver_getter(self.serial)
        except Exception:  # pragma: no cover - 驱动异常不能影响录制
            return None

    def set_paused(self, paused: bool) -> None:
        self.paused = bool(paused)

    def before_pointer(self) -> dict[str, Any] | None:
        raise NotImplementedError

    def handle(self, kind: str, payload: dict[str, Any], pre: dict[str, Any] | None) -> None:
        raise NotImplementedError

    def note_manual(self, kind: str, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    # 供子类/测试复用
    def _record_event(
        self,
        kind: str,
        payload: dict[str, Any],
        confidence: str = convert.CONFIDENCE_HIGH,
    ) -> dict[str, Any] | None:
        if self.paused:
            self.dropped += 1
            return None
        return _append_event(self.recording_id, kind, payload, confidence)


# ================================================================ Web 录制
class WebRecorderSession(_BaseRecorder):
    """镜像/手动注入录制会话；实现 `recorder.hooks.RecorderSessionLike`。"""

    mode = "web"

    def __init__(
        self,
        recording_id: str,
        serial: str,
        driver_getter: DriverGetter | None = None,
    ) -> None:
        super().__init__(recording_id, serial, driver_getter)
        try:
            self.meta["click_dump"] = bool(config.get("recording.click_dump"))
        except Exception:  # pragma: no cover
            self.meta["click_dump"] = True
        self._last_signature: tuple[Any, ...] | None = None
        self._last_signature_at = 0.0
        # 层级采样缓存：录制期间后台每 ~500ms 抓一次，pointerdown 时直接用最近快照，
        # 避免「先注入 down 再同步 dump」抓到动作之后的界面（会导致归因错误）
        self._hier_cache: dict[str, Any] | None = None
        self._hier_cache_at: float = 0.0
        self._sampler_stop = threading.Event()
        self._sampler: threading.Thread | None = None
        self._hier_lock = threading.RLock()
        # 最近一次点击命中的 Target：input 事件本身没有坐标，用它当输入目标，
        # 否则回放时只能"输入到当前焦点"，焦点不对就整段丢字。
        self._last_pointer_target: dict[str, Any] | None = None
        self._last_pointer_target_at: float = 0.0
        self._start_hierarchy_sampler()

    # ---- hooks 协议
    def before_pointer(self) -> dict[str, Any] | None:
        if self.paused:
            return None
        try:
            if not config.get("recording.click_dump"):
                return None
        except Exception:  # pragma: no cover
            pass
        # 优先用后台采样缓存（通常 <500ms 新鲜，零等待）
        with self._hier_lock:
            cached = self._hier_cache
            cached_at = self._hier_cache_at
        if cached is not None and (time.monotonic() - cached_at) * 1000 <= _HIER_CACHE_MAX_AGE_MS:
            return cached
        driver = self._driver()
        if driver is None:
            return None
        try:
            tree = driver.dump_hierarchy().to_dict()
            with self._hier_lock:
                self._hier_cache = tree
                self._hier_cache_at = time.monotonic()
            return tree
        except Exception as exc:  # 录制不能影响控制
            logger.debug("before_pointer dump failed for %s: %s", self.serial, exc)
            return cached

    def _start_hierarchy_sampler(self) -> None:
        """后台层级采样：录制期间维持一份 ~500ms 新的层级快照，供点击归因使用。"""
        if self._sampler is not None:
            return
        try:
            interval_ms = int(config.get("recording.hierarchy_refresh_ms") or 500)
        except Exception:  # pragma: no cover
            interval_ms = 500
        interval_s = max(0.2, interval_ms / 1000.0)

        def loop() -> None:
            while not self._sampler_stop.is_set():
                if not self.paused:
                    driver = self._driver()
                    if driver is not None:
                        try:
                            tree = driver.dump_hierarchy().to_dict()
                            with self._hier_lock:
                                self._hier_cache = tree
                                self._hier_cache_at = time.monotonic()
                        except Exception:  # 录制不能影响控制
                            logger.debug("hierarchy sampler dump failed for %s", self.serial, exc_info=True)
                self._sampler_stop.wait(interval_s)

        self._sampler = threading.Thread(target=loop, name=f"rec-hier-{self.serial}", daemon=True)
        self._sampler.start()

    def _stop_hierarchy_sampler(self) -> None:
        self._sampler_stop.set()
        sampler = self._sampler
        self._sampler = None
        if sampler is not None and sampler.is_alive():
            sampler.join(timeout=2.0)

    def handle(self, kind: str, payload: dict[str, Any], pre: dict[str, Any] | None) -> None:
        if self.paused:
            self.dropped += 1
            return
        normalized = self._normalize(kind, payload or {}, pre, source="web")
        if normalized is None:
            return
        event_payload, confidence = normalized
        written = self._record_event(kind, event_payload, confidence)
        if written is not None:
            self._remember_signature(kind, event_payload)

    def note_manual(self, kind: str, payload: dict[str, Any]) -> None:
        if self.paused:
            self.dropped += 1
            return
        normalized = self._normalize(kind, payload or {}, None, source="manual")
        if normalized is None:
            return
        event_payload, confidence = normalized
        signature = _event_signature(kind, event_payload)
        if self._last_signature == signature and (time.monotonic() - self._last_signature_at) < 1.5:
            # api 层对"运行暂停期间的手动操作"会同时调用 on_action + note_manual，
            # 这里去重，避免同一动作落两条事件。
            return
        written = self._record_event(kind, event_payload, confidence)
        if written is not None:
            self._remember_signature(kind, event_payload)

    def _remember_signature(self, kind: str, payload: dict[str, Any]) -> None:
        self._last_signature = _event_signature(kind, payload)
        self._last_signature_at = time.monotonic()

    # ---- 归一化 + 归因
    def _normalize(
        self,
        kind: str,
        payload: dict[str, Any],
        pre: dict[str, Any] | None,
        source: str,
    ) -> tuple[dict[str, Any], str] | None:
        kind = str(kind or "").strip().lower()
        body: dict[str, Any] = {
            "x": None,
            "y": None,
            "duration_ms": None,
            "text": None,
            "key": None,
            "keycode": None,
            "from": None,
            "to": None,
            "node": None,
            "target": None,
            "package": None,
            "action": None,
            "source": source,
            "confidence_reason": "",
        }
        driver = self._driver()
        confidence = convert.CONFIDENCE_HIGH
        reason = ""

        if kind in ("tap", "long_press", "long_click"):
            x, y = payload.get("x"), payload.get("y")
            if x is None or y is None:
                logger.debug("dropping pointer event without coordinates: %s %s", kind, payload)
                return None
            try:
                x, y = int(x), int(y)
            except (TypeError, ValueError):
                return None
            body["x"], body["y"] = x, y
            body["duration_ms"] = int(payload.get("duration_ms") or 0)
            target, confidence, reason = convert.attribute_with_confidence(driver, pre, x, y)
            body["node"] = target
            body["target"] = target
            if target and target.get("by") != "point":
                self._last_pointer_target = dict(target)
                self._last_pointer_target_at = time.monotonic()
        elif kind in ("swipe", "drag"):
            start, end = _extract_points(payload)
            if start is None or end is None:
                logger.debug("dropping gesture event without from/to: %s %s", kind, payload)
                return None
            body["from"], body["to"] = start, end
            body["duration_ms"] = int(payload.get("duration_ms") or 0)
            path = payload.get("path")
            if isinstance(path, list) and len(path) >= 2:
                body["path"] = path
            target, _node_confidence, _node_reason = convert.attribute_with_confidence(
                driver, pre, start["x"], start["y"]
            )
            body["node"] = target
            body["target"] = target
            reason = "坐标手势（起点归因仅作参考）"
        elif kind == "input":
            body["text"] = str(payload.get("text") or "")
            target = _valid_payload_target(payload)
            if target is None:
                # 复用最近一次点击的元素（5s 内）作为输入目标：回放时先点它聚焦再输入
                recent = self._last_pointer_target
                if recent is not None and (time.monotonic() - self._last_pointer_target_at) <= 5.0:
                    target = dict(recent)
            body["node"] = target
            body["target"] = target
            reason = "文本输入事件（目标复用最近点击元素）" if target else "文本输入事件"
        elif kind == "key":
            key = payload.get("key")
            keycode = payload.get("keycode")
            if key is None and keycode is None:
                return None
            body["key"] = key
            try:
                body["keycode"] = int(keycode) if keycode is not None else resolve_key(str(key))
            except (TypeError, ValueError):
                body["keycode"] = keycode if isinstance(keycode, int) else None
            reason = "按键事件"
        elif kind == "app":
            body["package"] = payload.get("package")
            body["action"] = payload.get("action")
            reason = "应用切换事件（转脚本时忽略）"
        else:
            # 未知 kind：保留原始信息，交由 convert 决定是否可转换
            for key, value in payload.items():
                body.setdefault(key, value)
            confidence = convert.CONFIDENCE_MEDIUM
            reason = "未分类事件"

        body["confidence_reason"] = reason
        return body, confidence


def _normalize_signature_value(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(key), _normalize_signature_value(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_normalize_signature_value(item) for item in value)
    return value


def _payload_signature(payload: dict[str, Any]) -> tuple[Any, ...]:
    return _normalize_signature_value(payload)


def _event_signature(kind: str, payload: dict[str, Any]) -> tuple[Any, ...]:
    body = {key: value for key, value in payload.items() if key != "source"}
    return str(kind), _payload_signature(body)


def _extract_points(payload: dict[str, Any]) -> tuple[dict[str, int] | None, dict[str, int] | None]:
    def as_point(value: Any) -> dict[str, int] | None:
        if not isinstance(value, dict):
            return None
        x, y = value.get("x"), value.get("y")
        try:
            return {"x": int(x), "y": int(y)}
        except (TypeError, ValueError):
            return None

    start = as_point(payload.get("from")) or as_point({"x": payload.get("x1"), "y": payload.get("y1")})
    end = as_point(payload.get("to")) or as_point({"x": payload.get("x2"), "y": payload.get("y2")})
    return start, end


def _valid_payload_target(payload: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("target", "node"):
        value = payload.get(key)
        if isinstance(value, dict) and value.get("by"):
            return value
    return None


# ================================================================ 物理录制
# 物理触摸轨迹上限：120Hz 下约 2s，足够覆盖常见滑动；超出后保留最后一点。
_MAX_PHYSICAL_PATH_SAMPLES = 240


@dataclass
class _GestureState:
    start_t: float
    start_x: int
    start_y: int
    last_t: float
    last_x: int
    last_y: int
    samples: list[dict[str, int]] = field(default_factory=list)


def _touch_device_meta(device: Any) -> dict[str, Any]:
    """TouchDevice（be-media getevent）-> recording.meta 片段。"""
    if isinstance(device, dict):
        return dict(device)
    if device is None:
        return {}
    meta: dict[str, Any] = {"touch_device": getattr(device, "path", None) or str(device)}
    name = getattr(device, "name", None)
    if name:
        meta["touch_device_name"] = str(name)
    for axis in ("abs_x", "abs_y"):
        value = getattr(device, axis, None)
        to_tuple = getattr(value, "to_tuple", None)
        if callable(to_tuple):
            try:
                meta[axis] = list(to_tuple())
            except Exception:  # pragma: no cover - 轴信息不可用
                continue
    return meta


def _probe_touch_device(serial: str) -> dict[str, Any]:
    """探测触摸设备（getevent 能力由 be-media 提供）；失败抛 UNSUPPORTED。"""
    try:
        from ..android import getevent
    except Exception as exc:  # 模块尚未就绪/无真机
        raise ApiError(
            "UNSUPPORTED",
            "无法识别触摸设备，物理录制不可用（getevent 能力不可用）",
            {"serial": serial, "error": str(exc)},
        ) from exc
    candidates = (
        "touch_device_detection",  # be-media 实际接口
        "detect_touch_device",
        "probe_touch_device",
        "find_touch_device",
        "touch_devices",
    )
    for name in candidates:
        func_ = getattr(getevent, name, None)
        if not callable(func_):
            continue
        try:
            result = func_(serial)
        except Exception as exc:  # adb 不可用 / 探测失败：统一按"无法识别触摸设备"
            raise ApiError(
                "UNSUPPORTED",
                "无法识别触摸设备，物理录制不可用",
                {"serial": serial, "error": str(exc)},
            ) from exc
        if result:
            return _touch_device_meta(result)
    raise ApiError(
        "UNSUPPORTED",
        "无法识别触摸设备，物理录制不可用（getevent 未探测到触摸设备）",
        {"serial": serial},
    )


def _stream_events_factory(serial: str, on_touch: Any, on_key: Any, stop_event: threading.Event) -> Any:
    """后台线程入口；真实实现在 be-media 的 app.android.getevent。"""
    from ..android.getevent import stream_events

    return stream_events(serial, on_touch, on_key, stop_event)


def _map_point(
    serial: str,
    x: Any,
    y: Any,
    mapper: Any = None,
    screen: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """物理触摸坐标 -> 设备像素。

    be-media 的 `TouchSample.x/y` 是 0..1 归一化值（契约 §8），需经 touchmap 映射；
    已是设备像素（>1）时按原值返回（便于手工构造采样/单测）。
    """
    try:
        fx, fy = float(x), float(y)
    except (TypeError, ValueError):
        return int(x), int(y)
    normalized = 0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0
    if not normalized:
        return int(round(fx)), int(round(fy))
    if mapper is not None:
        apply = getattr(mapper, "apply", None)
        if callable(apply):
            try:
                mapped_x, mapped_y = apply(fx, fy)
                return int(round(float(mapped_x))), int(round(float(mapped_y)))
            except Exception:  # 屏幕尺寸未知等 -> 退化为等比缩放
                pass
    if screen:
        width, height = int(screen[0]), int(screen[1])
        if width > 0 and height > 0:
            px = min(max(0, int(round(fx * (width - 1)))), width - 1)
            py = min(max(0, int(round(fy * (height - 1)))), height - 1)
            return px, py
    try:
        from ..android import touchmap  # 模块级兜底（若 be-media 提供便捷函数）
    except Exception:
        return 0, 0
    for name in ("map_normalized", "map_point", "map_touch", "to_device"):
        func_ = getattr(touchmap, name, None)
        if not callable(func_):
            continue
        try:
            result = func_(serial, fx, fy)
        except TypeError:
            try:
                result = func_(fx, fy)
            except Exception:  # pragma: no cover
                continue
        except Exception:  # pragma: no cover
            continue
        if isinstance(result, (tuple, list)) and len(result) >= 2:
            try:
                return int(result[0]), int(result[1])
            except (TypeError, ValueError):
                continue
    return 0, 0


def _coerce_keycode(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
        lowered = text.lower()
        if lowered.startswith("keycode_"):
            lowered = lowered[len("keycode_") :]
        if lowered in KEY_CODES:
            return KEY_CODES[lowered]
    return None


class PhysicalRecorder(_BaseRecorder):
    """物理触摸录制：后台线程消费 getevent 流，归并手势后落库。

    注意：getevent 看到的是内核层真实触摸，因此本类**不**响应 hooks 的注入动作
    （`handle`/`note_manual` 为空操作），避免与物理采样重复记录。
    """

    mode = "physical"

    def __init__(
        self,
        recording_id: str,
        serial: str,
        driver_getter: DriverGetter | None = None,
    ) -> None:
        super().__init__(recording_id, serial, driver_getter)
        self.meta.update(
            {
                "touch_device": None,
                "touch_device_name": None,
                "clock_anchor": None,
                "calibration": None,
                "screen": None,
            }
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._gesture: _GestureState | None = None
        self._touch_mapper: Any = None
        self._screen_size: tuple[int, int] | None = None

    # ---- 生命周期
    def start(self) -> PhysicalRecorder:
        probe = _probe_touch_device(self.serial)
        self.meta.update(probe if isinstance(probe, dict) else {"touch_device": probe})
        self.meta.setdefault("clock_anchor", time.time())
        self._prepare_mapping()
        self._persist_meta()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._consume,
            name=f"recorder-{self.recording_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def _prepare_mapping(self) -> None:
        """解析 TouchMapper（标定/旋转）+ 屏幕尺寸，供归一化触摸坐标映射。"""
        driver = self._driver()
        screen: tuple[int, int] | None = None
        if driver is not None:
            try:
                width, height = driver.wm_size()
                screen = (int(width), int(height))
            except Exception:  # pragma: no cover - 驱动实现差异
                screen = None
        mapper: Any = None
        try:
            from ..android import touchmap

            loader = getattr(touchmap, "load_touchmap", None)
            if callable(loader):
                mapper = loader(self.serial)
            if mapper is not None and screen is not None:
                setter = getattr(mapper, "set_screen_size", None)
                if callable(setter):
                    try:
                        setter(*screen)
                    except Exception:  # pragma: no cover - 非法尺寸
                        pass
            if mapper is not None and driver is not None:
                add_rotation = getattr(mapper, "add_rotation", None)
                if callable(add_rotation):
                    try:
                        add_rotation(int(driver.rotation()), time.monotonic())
                    except Exception:  # pragma: no cover
                        pass
        except Exception:  # touchmap 未就绪：退化为按屏幕尺寸等比缩放
            mapper = None
        self._touch_mapper = mapper
        self._screen_size = screen
        self.meta["screen"] = list(screen) if screen is not None else None
        if mapper is not None:
            to_dict = getattr(mapper, "to_dict", None)
            if callable(to_dict):
                try:
                    snapshot = to_dict()
                    self.meta["calibration"] = snapshot.get("calibration", snapshot)
                except Exception:  # pragma: no cover
                    pass

    def _consume(self) -> None:
        factory = _stream_events_factory  # 允许测试 monkeypatch
        try:
            factory(self.serial, self._on_touch, self._on_key, self._stop_event)
        except Exception as exc:  # 真机断流/线程异常不应影响主流程
            logger.warning("physical recorder stream ended for %s: %s", self.serial, exc)

    def stop(self) -> bool:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        alive = bool(thread is not None and thread.is_alive())
        self._thread = None
        if alive:  # pragma: no cover - 需要真机异常行为
            logger.warning("physical recorder thread did not stop in time: %s", self.recording_id)
        return not alive

    # ---- hooks 协议（物理模式不重复记录注入动作）
    def before_pointer(self) -> dict[str, Any] | None:
        return None

    def handle(self, kind: str, payload: dict[str, Any], pre: dict[str, Any] | None) -> None:
        logger.debug("physical recorder ignores injected action: %s", kind)

    def note_manual(self, kind: str, payload: dict[str, Any]) -> None:
        logger.debug("physical recorder ignores manual note: %s", kind)

    # ---- getevent 回调
    def _on_touch(self, sample: Any) -> None:
        try:
            # be-media 的 TouchSample.x/y 是 0..1 归一化浮点，必须保持 float 再映射
            t = float(sample.t)
            x = float(sample.x)
            y = float(sample.y)
            down = bool(getattr(sample, "down", True))
        except (TypeError, ValueError, AttributeError):
            return
        mapped_x, mapped_y = _map_point(self.serial, x, y, self._touch_mapper, self._screen_size)
        if self._gesture is None:
            if not down:
                return
            self._gesture = _GestureState(
                t,
                mapped_x,
                mapped_y,
                t,
                mapped_x,
                mapped_y,
                samples=[{"x": mapped_x, "y": mapped_y, "dt_ms": 0}],
            )
            return
        gesture = self._gesture
        gesture.last_t, gesture.last_x, gesture.last_y = t, mapped_x, mapped_y
        elapsed = max(0, int(round((t - gesture.start_t) * 1000)))
        sample = {"x": mapped_x, "y": mapped_y, "dt_ms": elapsed}
        if len(gesture.samples) < _MAX_PHYSICAL_PATH_SAMPLES:
            # 保留抬手点/坐标变化点；静止停顿由 duration_ms 与最后 dt 的差额体现。
            if (
                not gesture.samples
                or mapped_x != gesture.samples[-1]["x"]
                or mapped_y != gesture.samples[-1]["y"]
                or not down
            ):
                gesture.samples.append(sample)
        else:
            gesture.samples[-1] = sample
        if not down:
            self._finish_gesture()

    def _finish_gesture(self) -> None:
        gesture = self._gesture
        self._gesture = None
        if gesture is None:
            return
        duration_ms = max(0, int(round((gesture.last_t - gesture.start_t) * 1000)))
        distance = int(
            round(math.hypot(gesture.last_x - gesture.start_x, gesture.last_y - gesture.start_y))
        )
        # 画圆/画圈会回到起点，终点位移很小；用轨迹离起点的最大距离判别是否真的滑了。
        extent = max(
            (
                math.hypot(int(sample.get("x", gesture.start_x)) - gesture.start_x,
                           int(sample.get("y", gesture.start_y)) - gesture.start_y)
                for sample in gesture.samples
            ),
            default=float(distance),
        )
        kind = convert.gesture_from_samples(duration_ms, distance, extent)
        body: dict[str, Any] = {
            "x": None,
            "y": None,
            "duration_ms": duration_ms,
            "text": None,
            "key": None,
            "keycode": None,
            "from": None,
            "to": None,
            "node": None,
            "target": None,
            "package": None,
            "action": None,
            "source": "physical",
            "confidence_reason": "",
        }
        if kind in ("tap", "long_press"):
            body["x"], body["y"] = gesture.start_x, gesture.start_y
            target, confidence, reason = self._attribute_now(gesture.start_x, gesture.start_y)
            body["node"], body["target"] = target, target
            body["confidence_reason"] = reason
        else:
            start = {"x": gesture.start_x, "y": gesture.start_y}
            end = {"x": gesture.last_x, "y": gesture.last_y}
            body["from"], body["to"] = start, end
            if len(gesture.samples) >= 2:
                body["path"] = list(gesture.samples)
            target, _confidence, _reason = self._attribute_now(start["x"], start["y"])
            body["node"], body["target"] = target, target
            body["confidence_reason"] = "物理滑动（起点归因仅作参考）"
            confidence = convert.CONFIDENCE_HIGH
        self._record_event(kind, body, confidence)

    def _attribute_now(self, x: int, y: int) -> tuple[dict[str, Any] | None, str, str]:
        """事件后补抓一次层级做归因（每事件 ≤1 次 dump）。"""
        driver = self._driver()
        if driver is None:
            return {"by": "point", "x": x, "y": y}, convert.CONFIDENCE_LOW, "无可用驱动，使用坐标定位"
        try:
            tree = driver.dump_hierarchy().to_dict()
        except Exception:
            tree = None
        target, confidence, reason = convert.attribute_with_confidence(driver, tree, x, y)
        return target, confidence, reason

    def _on_key(self, event: Any) -> None:
        try:
            down = bool(getattr(event, "down", True))
        except AttributeError:
            return
        if not down:
            return
        code = _coerce_keycode(getattr(event, "keycode", None))
        if code is None:
            return
        # 兼容 Linux key code（getevent 实际口径）与 Android keycode
        key_name = _LINUX_KEYCODE_TO_NAME.get(code) or _KEYCODE_TO_NAME.get(code)
        if key_name is None:  # 只记录契约支持的按键
            return
        payload = {
            "x": None,
            "y": None,
            "duration_ms": 0,
            "text": None,
            "key": key_name,
            "keycode": code,
            "from": None,
            "to": None,
            "node": None,
            "target": None,
            "package": None,
            "action": None,
            "source": "physical",
            "confidence_reason": "物理按键事件",
        }
        self._record_event("key", payload, convert.CONFIDENCE_HIGH)

    def _persist_meta(self) -> None:
        with session_scope() as db:
            recorder = db.get(Recording, self.recording_id)
            if recorder is None:
                return
            merged = dict(recorder.meta or {})
            merged.update(self.meta)
            recorder.meta = merged


# ================================================================ 会话管理
_SESSIONS: dict[str, _BaseRecorder] = {}
_SESSIONS_LOCK = threading.RLock()


def _mark_started(recording_id: str, meta: dict[str, Any]) -> None:
    with session_scope() as db:
        recorder = db.get(Recording, recording_id)
        if recorder is None:
            return
        recorder.status = "recording"
        recorder.started_at = recorder.started_at or now_iso()
        recorder.ended_at = None
        merged = dict(recorder.meta or {})
        merged.update(meta or {})
        recorder.meta = merged


def create(
    recording_id: str,
    serial: str,
    mode: str = "web",
    driver_getter: DriverGetter | None = None,
) -> _BaseRecorder:
    """创建并注册会话；physical 模式会先探测触摸设备（失败抛 UNSUPPORTED）。"""
    recording_id = str(recording_id)
    serial = str(serial)
    with _SESSIONS_LOCK:
        existing = _SESSIONS.get(recording_id)
        if existing is not None:
            return existing
        if mode == "physical":
            session: _BaseRecorder = PhysicalRecorder(recording_id, serial, driver_getter)
            session.start()  # type: ignore[attr-defined]
        else:
            session = WebRecorderSession(recording_id, serial, driver_getter)
        _SESSIONS[recording_id] = session
    hooks.register(serial, session)
    _mark_started(recording_id, session.meta)
    logger.info("recording session created: %s serial=%s mode=%s", recording_id, serial, mode)
    return session


def get(recording_id: str) -> _BaseRecorder | None:
    with _SESSIONS_LOCK:
        return _SESSIONS.get(str(recording_id))


def get_by_serial(serial: str) -> _BaseRecorder | None:
    with _SESSIONS_LOCK:
        for session in _SESSIONS.values():
            if session.serial == serial:
                return session
    return None


def is_active(recording_id: str) -> bool:
    return get(recording_id) is not None


def pause(recording_id: str) -> _BaseRecorder | None:
    session = get(recording_id)
    if session is not None:
        session.set_paused(True)
    _update_recording(str(recording_id), status="paused")
    return session


def resume(recording_id: str) -> _BaseRecorder | None:
    session = get(recording_id)
    if session is not None:
        session.set_paused(False)
    _update_recording(str(recording_id), status="recording")
    return session


def stop(recording_id: str) -> bool:
    """停止会话：join 物理线程、注销 hooks、释放设备占用、更新录制行。"""
    recording_id = str(recording_id)
    with _SESSIONS_LOCK:
        session = _SESSIONS.pop(recording_id, None)
    stopped = True
    if session is not None:
        stopper = getattr(session, "stop", None)
        if callable(stopper):
            try:
                stopped = bool(stopper())
            except Exception as exc:  # pragma: no cover - 防御
                logger.warning("recording session stop failed: %s", exc)
        stopper_sampler = getattr(session, "_stop_hierarchy_sampler", None)
        if callable(stopper_sampler):
            try:
                stopper_sampler()
            except Exception:  # pragma: no cover - 防御
                logger.debug("hierarchy sampler stop failed", exc_info=True)
        hooks.unregister(session.serial, recording_id)
        _release_device(session.serial, recording_id)
        event_count = 0
        with session_scope() as db:
            recorder = db.get(Recording, recording_id)
            if recorder is not None:
                recorder.status = "stopped"
                recorder.ended_at = recorder.ended_at or now_iso()
                total = db.scalar(
                    select(func.count())
                    .select_from(RecordingEvent)
                    .where(RecordingEvent.recording_id == recording_id)
                )
                recorder.event_count = int(total or 0)
                event_count = recorder.event_count
                if session.dropped:
                    merged = dict(recorder.meta or {})
                    merged["dropped_while_paused"] = int(merged.get("dropped_while_paused", 0)) + int(
                        session.dropped
                    )
                    recorder.meta = merged
        logger.info("recording session stopped: %s (events=%s)", recording_id, event_count)
        return stopped
    # 会话不在内存（例如服务重启后）：仅确保 DB 状态收敛
    with session_scope() as db:
        recorder = db.get(Recording, recording_id)
        if recorder is not None and recorder.status != "stopped":
            recorder.status = "stopped"
            recorder.ended_at = recorder.ended_at or now_iso()
    return True


def shutdown_all() -> None:
    with _SESSIONS_LOCK:
        recording_ids = list(_SESSIONS)
    for recording_id in recording_ids:
        try:
            stop(recording_id)
        except Exception as exc:  # pragma: no cover - 关机路径尽力而为
            logger.warning("shutdown recording %s failed: %s", recording_id, exc)


def shutdown() -> None:
    """别名：供 main.py lifespan 调用。"""
    shutdown_all()


def probe_physical(serial: str) -> dict[str, Any]:
    """API 创建 physical 录制前的预检（失败抛 ApiError UNSUPPORTED）。"""
    return _probe_touch_device(str(serial))


def _release_device(serial: str, recording_id: str) -> None:
    try:
        from ..services.device_registry import release

        release(serial, "recording", recording_id)
    except Exception:  # pragma: no cover - 设备服务未装配
        pass


# ================================================================ 事件 -> 脚本
def to_script(
    recording_id: str,
    name: str | None = None,
    include_kinds: list[str] | None = None,
    exclude_seqs: list[int] | None = None,
    target_mode: str | None = None,
) -> dict[str, Any]:
    """读录制事件 -> convert -> schemas.validate_script -> 建脚本（source=mirror/physical）。"""
    recording_id = str(recording_id)
    with session_scope() as db:
        recorder = db.get(Recording, recording_id)
        if recorder is None:
            raise ApiError("NOT_FOUND", "recording not found", {"id": recording_id})
        recording_name = recorder.name or ""
        recording_mode = str(recorder.mode or "web")
        rows = list(
            db.scalars(
                select(RecordingEvent)
                .where(RecordingEvent.recording_id == recording_id)
                .order_by(RecordingEvent.seq)
            )
        )
        events = [
            {
                "seq": row.seq,
                "kind": row.kind,
                "payload": row.payload or {},
                "confidence": row.confidence,
                "created_at": row.created_at,
            }
            for row in rows
        ]

    script_name = (str(name).strip() if name else "") or recording_name or "录制脚本"
    mode = str(target_mode or "").strip().lower()
    if mode not in convert.TARGET_MODES:
        try:
            mode = str(config.get("recording.target_mode") or convert.TARGET_MODE_POINT).strip().lower()
        except Exception:  # pragma: no cover
            mode = convert.TARGET_MODE_POINT
    if mode not in convert.TARGET_MODES:
        mode = convert.TARGET_MODE_POINT
    script, warnings = convert.events_to_script(
        events,
        script_name,
        include_kinds=include_kinds,
        exclude_seqs=exclude_seqs,
        recording_id=recording_id,
        target_mode=mode,
    )
    if not script.get("steps"):
        raise ApiError(
            "INVALID_ARGUMENT",
            "没有可转换的录制事件",
            {"recording_id": recording_id, "warnings": warnings},
        )
    validation = validate_script(script)
    if not validation.get("valid"):
        raise ApiError(
            "VALIDATION_ERROR",
            "录制转换脚本校验失败",
            {"errors": validation.get("errors", []), "warnings": warnings},
        )

    script_id = f"rec_{uuid.uuid4().hex[:12]}"
    timestamp = now_iso()
    script["notes"] = script.get("notes") or f"由录制 {recording_id} 转换"
    # Recording.mode=web -> source=mirror；physical -> source=physical。
    # origin_recording_id 是软关联（无 FK），删除录制后仍保留来源信息。
    source = "physical" if recording_mode == "physical" else "mirror"
    with session_scope() as db:
        db.add(
            Script(
                id=script_id,
                name=script_name,
                notes=script["notes"],
                schema_version=1,
                data=script,
                source=source,
                origin_recording_id=recording_id,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        recorder = db.get(Recording, recording_id)
        if recorder is not None:
            recorder.script_id = script_id
    logger.info("recording %s -> script %s (%s steps, %s warnings)", recording_id, script_id, len(script["steps"]), len(warnings))
    return {
        "id": script_id,
        "script_id": script_id,
        "name": script_name,
        "notes": script["notes"],
        "schema_version": 1,
        "data": script,
        "source": source,
        "origin_recording_id": recording_id,
        "created_at": timestamp,
        "updated_at": timestamp,
        "warnings": warnings,
    }


# ================================================================ 小工具
def sanitize_serial(serial: str) -> str:
    return _SERIAL_RE.sub("_", str(serial or "")) or "device"
