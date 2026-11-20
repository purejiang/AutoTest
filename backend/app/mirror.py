"""镜像会话与坐标逆映射（契约 §6.2 / §10 / §14.3）。

- 每个 serial 一个 ``MirrorSession``（模块级 registry + 锁），持有 scrcpy server 进程、
  读取线程、geometry/SPS 缓存；
- 向多个 WS 订阅者（``asyncio.Queue``）广播二进制帧；**背压**：队列满 → 丢帧到下一 IDR，
  先投 ``flush`` 控制消息再补 IDR（并异步请求编码器出 IDR）；``stats()`` 暴露 fps/丢帧；
- 断线自动重启（≤3 次/60s，超限发 error 但保持 WS 连接）；
- 归一化坐标（相对视频帧）→ 设备当前方向像素：``map_normalized`` 纯函数，
  反算 crop（自然方向标定）→ rotation（0/90/180/270）→ 当前显示坐标。

线程模型：读线程 daemon；订阅者队列只在各自 event loop 线程里增删（``call_soon_threadsafe``），
停止时关闭 socket 让阻塞读退出，并 join 读线程。
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .android import scrcpy
from .errors import ApiError
from .logs import logger

# 订阅者队列默认长度（≈ 1s@30fps 的一半，超过即认为消费慢）
DEFAULT_SUBSCRIBER_QUEUE = 8
# 控制消息与视频帧分两条通道：控制消息（pong/flush/config/error）优先且不参与丢帧背压
WAKE_SENTINEL = object()
# 断线重启节流：60s 内最多 3 次
RESTART_WINDOW_S = 60.0
MAX_RESTARTS_PER_WINDOW = 3
# 重启前等待（给进程清理/手机反应时间）
RESTART_DELAY_S = 0.1

Rotation = int  # 0 / 90 / 180 / 270（顺时针）


# ---------------------------------------------------------------- geometry
def normalize_rotation(rotation: int) -> Rotation:
    """接受 0/1/2/3（圈数）或 0/90/180/270（度），统一成角度。"""
    value = int(rotation)
    if value in (0, 1, 2, 3):
        value *= 90
    value %= 360
    if value % 90 != 0:
        raise ValueError(f"rotation must be a multiple of 90, got {rotation}")
    return value


def parse_crop(value: str) -> tuple[int, int, int, int] | None:
    """scrcpy ``--crop=w:h:x:y`` → (x, y, w, h)；空串 → None。"""
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 4:
        raise ValueError(f"crop must be w:h:x:y, got {value!r}")
    try:
        width, height, x, y = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError(f"crop must be integers: {value!r}") from exc
    if width <= 0 or height <= 0 or x < 0 or y < 0:
        raise ValueError(f"invalid crop: {value!r}")
    return (x, y, width, height)


def _aspect_matches(a_w: int, a_h: int, b_w: int, b_h: int, tolerance: float = 0.06) -> bool:
    if a_w <= 0 or a_h <= 0 or b_w <= 0 or b_h <= 0:
        return False
    return abs((a_w / a_h) - (b_w / b_h)) <= tolerance * (b_w / b_h)


def infer_rotation(stream_w: int, stream_h: int, natural_w: int, natural_h: int) -> Rotation:
    """由编码尺寸与设备自然尺寸推断当前旋转（视频已被 server 转正）。

    - stream 宽高比 ≈ 自然宽高比 → 0°
    - stream 宽高比 ≈ 自然宽高比倒数 → 90°（横向画面）
    - 无法判断（如 crop 过）→ 0°
    """
    if _aspect_matches(stream_w, stream_h, natural_w, natural_h):
        return 0
    if _aspect_matches(stream_w, stream_h, natural_h, natural_w):
        return 90
    return 0


@dataclass(frozen=True)
class MirrorGeometry:
    """视频几何 + 设备几何的完整描述（供坐标逆映射）。

    - ``stream_width/height``：解码帧像素（已含 rotation、crop、max_size 缩放）；
    - ``natural_width/height``：设备**自然方向**屏幕像素（rotation=0 时的 wm size）；
    - ``rotation``：当前显示方向相对自然方向的顺时针旋转（0/90/180/270）；
    - ``crop``：scrcpy crop，自然方向 ``(x, y, w, h)``；
    - ``max_size``：仅记录用于展示（逆映射用比例，不依赖它）。
    """

    stream_width: int = 0
    stream_height: int = 0
    natural_width: int = 0
    natural_height: int = 0
    rotation: Rotation = 0
    crop: tuple[int, int, int, int] | None = None
    max_size: int = 0

    # ---- 当前方向显示尺寸
    @property
    def display_size(self) -> tuple[int, int]:
        rotation = normalize_rotation(self.rotation)
        if not self.natural_width or not self.natural_height:
            return self.stream_width, self.stream_height
        if rotation in (90, 270):
            return self.natural_height, self.natural_width
        return self.natural_width, self.natural_height

    # ---- crop 在当前方向的矩形
    def crop_rect(self) -> tuple[int, int, int, int]:
        """返回当前显示方向下的 crop 矩形 ``(x, y, w, h)``（无 crop 则为整屏）。"""
        disp_w, disp_h = self.display_size
        if self.crop is None:
            return (0, 0, disp_w, disp_h)
        cx, cy, cw, ch = self.crop
        rotation = normalize_rotation(self.rotation)
        natural_w, natural_h = self.natural_width, self.natural_height
        if rotation == 0:
            return (cx, cy, cw, ch)
        if rotation == 90:  # 自然 (x,y) → 显示 (H-1-y, x)
            return (natural_h - (cy + ch), cx, ch, cw)
        if rotation == 180:  # 自然 (x,y) → 显示 (W-1-x, H-1-y)
            return (natural_w - (cx + cw), natural_h - (cy + ch), cw, ch)
        # rotation == 270: 自然 (x,y) → 显示 (y, W-1-x)
        return (cy, natural_w - (cx + cw), ch, cw)

    # ---- 缩放比（stream / crop 显示矩形）
    def scale(self) -> float:
        _cx, _cy, cw, ch = self.crop_rect()
        if cw <= 0 or ch <= 0:
            return 1.0
        return self.stream_width / float(cw)

    def to_dict(self, *, video: scrcpy.VideoGeometry | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "width": self.stream_width,
            "height": self.stream_height,
            "rotation": self.rotation,
            "display_width": self.display_size[0],
            "display_height": self.display_size[1],
            "crop": list(self.crop) if self.crop else [],
            "max_size": self.max_size,
        }
        if video is not None:
            data.update(video.to_dict())
        return data


def map_normalized(nx: float, ny: float, geometry: MirrorGeometry) -> tuple[int, int]:
    """归一化（相对视频帧 0..1）→ 设备**当前方向**像素。

    公式（``crop_rect`` 已把自然方向 crop 旋到当前显示方向）::

        x = crop_x + nx * crop_w
        y = crop_y + ny * crop_h
        clamp 到 [0, display_w-1] × [0, display_h-1]

    视频帧本身已被 server 转正并按 max_size 等比缩放，所以比例映射与 max_size 无关；
    rotation 只影响 crop 矩形在显示坐标系里的位置（以及 display 尺寸）。
    """
    if not isinstance(nx, (int, float)) or not isinstance(ny, (int, float)):
        raise ValueError("nx/ny must be numbers")
    if not math.isfinite(float(nx)) or not math.isfinite(float(ny)):
        raise ValueError("nx/ny must be finite")
    disp_w, disp_h = geometry.display_size
    if disp_w <= 0 or disp_h <= 0:
        raise ValueError("mirror geometry is not ready")
    cx, cy, cw, ch = geometry.crop_rect()
    fx = min(max(float(nx), 0.0), 1.0)
    fy = min(max(float(ny), 0.0), 1.0)
    x = int(round(cx + fx * cw))
    y = int(round(cy + fy * ch))
    return (min(max(x, 0), disp_w - 1), min(max(y, 0), disp_h - 1))


# ---------------------------------------------------------------- 订阅者
@dataclass
class Subscriber:
    """一个 WS 消费者。

    - ``queue``：只放二进制视频帧（参与背压丢帧）；
    - ``controls``：控制消息（dict）优先发送，不参与丢帧；
    两者都只在所属 event loop 线程里操作。
    """

    id: int
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue
    controls: deque = field(default_factory=deque)
    dropping: bool = False
    dropped: int = 0
    sent: int = 0
    awaiting_keyframe: bool = True  # 新订阅者从下一个 IDR 开始收（保证解码器可启动）


class MirrorSession:
    """单 serial 的镜像会话（server + 读线程 + 广播）。"""

    def __init__(
        self,
        serial: str,
        cfg: scrcpy.ServerConfig | None = None,
        *,
        starter: Callable[[scrcpy.ServerConfig], Any] | None = None,
        size_probe: Callable[[], tuple[int, int]] | None = None,
    ) -> None:
        self.serial = serial
        self.cfg = cfg or scrcpy.ServerConfig.from_settings()
        self.session_id = uuid.uuid4().hex
        self._starter = starter or (lambda server_cfg: scrcpy.start_server(serial, server_cfg))
        self._size_probe = size_probe
        self._lock = threading.RLock()
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._subscribers: dict[int, Subscriber] = {}
        self._next_subscriber_id = 1
        self._state = "idle"  # idle|running|error|stopped
        self._error: str | None = None
        self._error_code: str | None = None
        self._restarts = 0
        self._restart_times: list[float] = []
        self._started_at = time.monotonic()
        self._frames = 0
        self._dropped = 0
        self._frame_times: deque[float] = deque(maxlen=600)
        self._video_geometry = scrcpy.VideoGeometry()
        self._last_sps: bytes | None = None
        self._pending_config: bytes = b""
        self._last_keyframe_request: float = 0.0
        self._keyframe_requests_throttled: int = 0  # 等待拼到下一视频帧前的 in-band SPS/PPS
        self._codec_config: bytes = b""  # 最近一次 CONFIG 包（SPS/PPS），新订阅者首帧补发
        self._geometry = MirrorGeometry(max_size=int(self.cfg.max_size))
        self._device_size: tuple[int, int] = (0, 0)
        self._crop = parse_crop(self.cfg.crop)

    # ------------------------------------------------------------ 状态
    @property
    def state(self) -> str:
        return self._state

    @property
    def active(self) -> bool:
        return self._state == "running"

    @property
    def geometry(self) -> MirrorGeometry:
        return self._geometry

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = self._state
            error = self._error
            error_code = self._error_code
            restarts = self._restarts
            subscribers = len(self._subscribers)
            uptime = time.monotonic() - self._started_at if state in ("running", "error") else 0.0
        data: dict[str, Any] = {
            "serial": self.serial,
            "session_id": self.session_id,
            "state": state,
            "error": error,
            "error_code": error_code,
            "width": self._geometry.stream_width,
            "height": self._geometry.stream_height,
            "rotation": self._geometry.rotation,
            "codec": self._video_geometry.codec,
            "max_size": self.cfg.max_size,
            "max_fps": self.cfg.max_fps,
            "uptime_s": round(uptime, 1),
            "restarts": restarts,
            "subscribers": subscribers,
        }
        data.update(self.stats())
        return data

    def stats(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            while self._frame_times and now - self._frame_times[0] > 1.0:
                self._frame_times.popleft()
            fps = float(len(self._frame_times))
            return {
                "fps": round(fps, 1),
                "frames": self._frames,
                "dropped": self._dropped,
                "uptime_s": round(now - self._started_at, 1) if self._state == "running" else 0.0,
            }

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        with self._lock:
            if self._state == "running":
                return
            self._stop_event.clear()
            self._state = "starting"
            self._error = None
            self._error_code = None
        try:
            server = self._starter(self.cfg)
        except ApiError:
            with self._lock:
                self._state = "error"
            raise
        except Exception as exc:  # noqa: BLE001 - 统一成 SCRCPY_FAILED
            with self._lock:
                self._state = "error"
                self._error = f"{type(exc).__name__}: {exc}"
            raise ApiError("SCRCPY_FAILED", f"scrcpy server 启动失败: {exc}", {"serial": self.serial}) from exc
        self._probe_device_size()
        self._adopt_server(server)
        with self._lock:
            self._state = "running"
            self._started_at = time.monotonic()
        thread = threading.Thread(target=self._run, name=f"mirror-{self.serial}", daemon=True)
        self._thread = thread
        thread.start()
        logger.info("mirror session started: serial=%s session=%s", self.serial, self.session_id)

    def stop(self) -> None:
        with self._lock:
            if self._state in ("stopped",):
                return
            self._state = "stopped"
            self._stop_event.set()
            server = self._server
            self._server = None
            subscribers = list(self._subscribers.values())
        if server is not None:
            try:
                server.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("mirror server stop failed: %s", exc)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None
        for sub in subscribers:
            self._send_sentinel(sub)
        logger.info("mirror session stopped: serial=%s session=%s", self.serial, self.session_id)

    # ------------------------------------------------------------ 内部：启动/重启
    def _probe_device_size(self) -> None:
        size = (0, 0)
        try:
            if self._size_probe is not None:
                size = tuple(int(v) for v in self._size_probe())  # type: ignore[assignment]
            else:
                size = _probe_wm_size(self.serial)
        except Exception as exc:  # noqa: BLE001 - 探测失败不影响镜像（退化为 stream 尺寸）
            logger.debug("probe device size for %s failed: %s", self.serial, exc)
        if size[0] > 0 and size[1] > 0:
            self._device_size = (size[0], size[1])
        else:
            self._device_size = (self._geometry.stream_width, self._geometry.stream_height)

    def _adopt_server(self, server: Any) -> None:
        self._server = server
        video_geometry = getattr(server, "geometry", None)
        if isinstance(video_geometry, scrcpy.VideoGeometry):
            self._video_geometry = video_geometry
        # 启动自检阶段已消费掉的 CONFIG 包（SPS/PPS）：缓存下来拼到首帧前（in-band 参数集）
        reader = getattr(server, "reader", None)
        seed_config = getattr(reader, "codec_config", b"") if reader is not None else b""
        if seed_config:
            self._codec_config = seed_config
            self._pending_config = seed_config
        first_frame = getattr(server, "first_frame", None)
        if first_frame:
            self._ingest(*first_frame, getattr(reader, "last_info", None))

    def _try_restart(self, reason: str) -> bool:
        gave_up = False
        message = ""
        with self._lock:
            if self._stop_event.is_set() or self._state != "running":
                return False
            now = time.monotonic()
            self._restart_times = [t for t in self._restart_times if now - t < RESTART_WINDOW_S]
            if len(self._restart_times) >= MAX_RESTARTS_PER_WINDOW:
                message = f"镜像断线且自动重启超过 {MAX_RESTARTS_PER_WINDOW} 次/{int(RESTART_WINDOW_S)}s：{reason}"
                self._state = "error"
                self._error = message
                self._error_code = "SCRCPY_FAILED"
                subscribers = list(self._subscribers.values())
                gave_up = True
            else:
                self._restart_times.append(now)
                subscribers = []
        if gave_up:
            logger.error("mirror session %s giving up restarting: %s", self.serial, reason)
            for sub in subscribers:
                self._deliver_control(sub, {"kind": "error", "code": "SCRCPY_FAILED", "message": message})
            return False
        logger.warning("mirror session %s disconnected (%s), restarting", self.serial, reason)
        time.sleep(RESTART_DELAY_S if self._starter is not None else 0)
        old = self._server
        if old is not None and hasattr(old, "stop"):
            try:
                old.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("stop disconnected server failed: %s", exc)
        try:
            server = self._starter(self.cfg)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                if self._state == "running":
                    self._state = "error"
                    self._error = f"重启失败: {type(exc).__name__}: {exc}"
                    self._error_code = getattr(exc, "code", "SCRCPY_FAILED")
                    subscribers = list(self._subscribers.values())
                else:
                    subscribers = []
            for sub in subscribers:
                self._deliver_control(
                    sub,
                    {"kind": "error", "code": "SCRCPY_FAILED", "message": f"镜像重启失败: {exc}"},
                )
            return False
        with self._lock:
            self._restarts += 1
            self._server = server
        self._adopt_server(server)
        self._broadcast_control({"kind": "flush", "reason": "reconnect"})
        self._broadcast_control({"kind": "config", **self._hello_fields()})
        return True

    # ------------------------------------------------------------ 内部：读循环
    def _run(self) -> None:
        while not self._stop_event.is_set():
            server = self._server
            if server is None:
                return
            try:
                if getattr(server, "first_frame", None) and self._frames == 0:
                    self._ingest(*server.first_frame)
                while not self._stop_event.is_set():
                    pts_us, payload = server.reader.read_frame()
                    self._ingest(pts_us, payload)
            except scrcpy.ScrcpyDisconnected as exc:
                if not self._try_restart(f"socket 关闭: {exc}"):
                    return
            except (TimeoutError, OSError, scrcpy.ScrcpyProtocolError) as exc:
                if not self._try_restart(f"读帧失败: {type(exc).__name__}: {exc}"):
                    return
            except Exception as exc:  # noqa: BLE001 - 线程内异常不能静默退出
                logger.exception("mirror reader crashed for %s", self.serial)
                with self._lock:
                    self._state = "error"
                    self._error = f"{type(exc).__name__}: {exc}"
                    self._error_code = "INTERNAL"
                    subscribers = list(self._subscribers.values())
                for sub in subscribers:
                    self._deliver_control(
                        sub, {"kind": "error", "code": "INTERNAL", "message": f"镜像读线程异常: {exc}"}
                    )
                return

    def _ingest(self, pts_us: int, payload: bytes, info: scrcpy.FrameInfo | None = None) -> None:
        """一帧进入会话：更新 geometry/SPS、统计、广播（线程安全）。

        v5 的 config 包（SPS/PPS，``info.config=True``）只用于几何/参数：
        更新 sps/pps → 广播 ``config`` 控制消息，**不**当视频帧转发给前端。
        """
        if info is None:
            info = getattr(getattr(self._server, "reader", None), "last_info", None)
        nals = scrcpy.split_nals(payload)
        keyframe = bool(info.keyframe) if info is not None else scrcpy.is_keyframe(nals)
        video_geometry = getattr(self._server, "geometry", None)
        if isinstance(video_geometry, scrcpy.VideoGeometry):
            self._video_geometry = video_geometry
        geometry_changed = self._maybe_update_geometry(self._video_geometry)
        if info is not None and info.session_meta:
            # v5 session meta：尺寸/旋转变化；不发帧。
            # 注意：几何没变时**不要**再广播 config —— 否则「config→前端请求关键帧→编码器重置→
            # 新 session meta→config」会形成 5Hz 自我循环，表现为镜像疯狂闪烁。
            if geometry_changed:
                logger.info("mirror geometry changed by session meta: %s", self._hello_fields())
            return
        if info is not None and info.config:
            # v5 CONFIG 包 = SPS/PPS（in-band）：缓存并拼到下一个视频帧前面再下发前端。
            # 同样不广播 config（参数集随下一帧 in-band 下发，前端无需重配）。
            if payload:
                self._codec_config = payload
                self._pending_config = payload
            return
        if self._pending_config:
            payload = self._pending_config + payload
            self._pending_config = b""
        binary = scrcpy.pack_frame(pts_us, payload)
        now = time.monotonic()
        with self._lock:
            self._frames += 1
            self._frame_times.append(now)
            subscribers = list(self._subscribers.values())
        for sub in subscribers:
            self._deliver_frame(sub, binary, keyframe)

    def _maybe_update_geometry(self, video_geometry: scrcpy.VideoGeometry) -> bool:
        """按最新视频几何更新（旋转推断），返回是否广播了 config。"""
        if video_geometry.width <= 0 or video_geometry.height <= 0:
            return False
        current = self._geometry
        sps_changed = bool(video_geometry.sps) and video_geometry.sps != self._last_sps
        if video_geometry.sps:
            self._last_sps = video_geometry.sps
        if (
            current.stream_width == video_geometry.width
            and current.stream_height == video_geometry.height
            and current.rotation
            == infer_rotation(video_geometry.width, video_geometry.height, *self._natural_size())
        ):
            # 尺寸/旋转未变化，仅 codec/SPS 可能更新
            self._geometry = MirrorGeometry(
                stream_width=current.stream_width,
                stream_height=current.stream_height,
                natural_width=current.natural_width,
                natural_height=current.natural_height,
                rotation=current.rotation,
                crop=current.crop,
                max_size=current.max_size,
            )
            if sps_changed and self._state in ("running", "starting"):
                self._broadcast_control({"kind": "config", **self._hello_fields()})
                return True
            return False
        natural_w, natural_h = self._natural_size()
        rotation = self._infer_rotation_dynamic(video_geometry.width, video_geometry.height, natural_w, natural_h)
        geometry = MirrorGeometry(
            stream_width=video_geometry.width,
            stream_height=video_geometry.height,
            natural_width=natural_w,
            natural_height=natural_h,
            rotation=rotation,
            crop=self._crop,
            max_size=int(self.cfg.max_size),
        )
        changed = (
            geometry.stream_width != current.stream_width
            or geometry.stream_height != current.stream_height
            or geometry.rotation != current.rotation
            or geometry.natural_width != current.natural_width
            or sps_changed
        )
        self._geometry = geometry
        if changed and self._state in ("running", "starting"):
            logger.info(
                "mirror geometry changed: %dx%d rotation=%s (natural %dx%d)",
                geometry.stream_width,
                geometry.stream_height,
                geometry.rotation,
                geometry.natural_width,
                geometry.natural_height,
            )
            self._broadcast_control({"kind": "config", **self._hello_fields()})
            return True
        return False

    def _natural_size(self) -> tuple[int, int]:
        if self._device_size[0] > 0 and self._device_size[1] > 0:
            return self._device_size
        return (self._geometry.stream_width, self._geometry.stream_height)

    def _infer_rotation_dynamic(self, width: int, height: int, natural_w: int, natural_h: int) -> int:
        """优先用「相邻两次流尺寸是否交换」判断 90°/270°，否则退回宽高比推断。"""
        current = self._geometry
        if current.stream_width and current.stream_height:
            same = (width, height) == (current.stream_width, current.stream_height)
            if same:
                return current.rotation
            swapped = (width, height) == (current.stream_height, current.stream_width)
            if swapped and width != height:
                return (current.rotation + 90) % 360
        return infer_rotation(width, height, natural_w, natural_h)

    # ------------------------------------------------------------ 订阅/广播
    def subscribe(self, loop: asyncio.AbstractEventLoop | None = None, maxsize: int = DEFAULT_SUBSCRIBER_QUEUE) -> Subscriber:
        loop = loop or asyncio.get_running_loop()
        sub = Subscriber(id=0, loop=loop, queue=asyncio.Queue(maxsize=maxsize))
        with self._lock:
            sub.id = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._subscribers[sub.id] = sub
            running = self._state == "running"
        if running:
            # 新订阅者：先单播当前参数集（只给它，避免其它客户端被 config 打扰），
            # 再请求一次 IDR（节流，多标签页/重连不会把编码器刷爆）。
            self._deliver_control(sub, {"kind": "config", **self._hello_fields()})
            self.request_keyframe()
        return sub

    def unsubscribe(self, sub: Subscriber | None) -> None:
        if sub is None:
            return
        with self._lock:
            self._subscribers.pop(sub.id, None)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def _deliver_frame(self, sub: Subscriber, binary: bytes, keyframe: bool) -> None:
        try:
            sub.loop.call_soon_threadsafe(self._deliver_frame_in_loop, sub, binary, keyframe)
        except RuntimeError:  # event loop 已关闭
            self.unsubscribe(sub)

    def _deliver_frame_in_loop(self, sub: Subscriber, binary: bytes, keyframe: bool) -> None:
        queue = sub.queue
        if sub.awaiting_keyframe:
            if not keyframe:
                # 新订阅者先等 IDR，避免 P 帧引用未知参考帧
                sub.dropped += 1
                with self._lock:
                    self._dropped += 1
                return
            sub.awaiting_keyframe = False
            if self._codec_config and not binary[12:].startswith(self._codec_config):
                # 首帧补 in-band SPS/PPS（前端 Annex B 直解需要参数集；避免重复拼接）
                pts_us, _size = scrcpy.unpack_frame_header(binary[:12])
                binary = scrcpy.pack_frame(pts_us, self._codec_config + binary[12:])
        if sub.dropping and not keyframe:
            sub.dropped += 1
            with self._lock:
                self._dropped += 1
            return
        try:
            queue.put_nowait(binary)
            sub.dropping = False
            sub.sent += 1
            return
        except asyncio.QueueFull:
            pass
        # 队列满：非 IDR → 进入丢帧模式；IDR → 清积压、flush、补 IDR（整 GOP 从 IDR 重来）
        with self._lock:
            self._dropped += 1
        sub.dropped += 1
        if keyframe:
            # 清积压旧帧（丢弃到 IDR 边界）；flush 走控制通道（先于 IDR 送达），不占帧队列
            drained = 0
            while True:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if isinstance(item, (bytes, bytearray)):
                    drained += 1
            with self._lock:
                self._dropped += drained
            sub.dropped += drained
            sub.controls.append({"kind": "flush", "reason": "keyframe_drop"})
            try:
                queue.put_nowait(binary)
            except asyncio.QueueFull:  # pragma: no cover - 极端竞态
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                queue.put_nowait(binary)
            sub.dropping = False
            sub.sent += 1
        else:
            sub.dropping = True
            self.request_keyframe()

    def _deliver_control(self, sub: Subscriber, message: dict[str, Any]) -> None:
        try:
            sub.loop.call_soon_threadsafe(self._deliver_control_in_loop, sub, message)
        except RuntimeError:
            self.unsubscribe(sub)

    def _deliver_control_in_loop(self, sub: Subscriber, message: dict[str, Any] | None) -> None:
        sub.controls.append(message)
        while len(sub.controls) > 64:  # 控制消息积压上限（极端慢消费者）
            sub.controls.popleft()
        try:
            sub.queue.put_nowait(WAKE_SENTINEL)  # 唤醒正在等帧的发送循环
        except asyncio.QueueFull:
            pass  # 队列非空时发送循环不会睡死

    def push_control(self, sub: Subscriber, message: dict[str, Any]) -> None:
        """从订阅者所属 event loop 线程投递控制消息（与 _deliver_control 的区别：不跨线程）。"""
        self._deliver_control_in_loop(sub, message)

    def _broadcast_control(self, message: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers.values())
        for sub in subscribers:
            self._deliver_control(sub, dict(message))

    def _send_sentinel(self, sub: Subscriber) -> None:
        try:
            sub.loop.call_soon_threadsafe(self._deliver_control_in_loop, sub, None)  # type: ignore[arg-type]
        except RuntimeError:
            pass

    # ------------------------------------------------------------ 对外能力
    _KEYFRAME_MIN_INTERVAL_S = 0.8  # 关键帧请求最小间隔：防止调用方循环触发编码器重置

    def request_keyframe(self, force: bool = False) -> bool:
        server = self._server
        if server is None:
            return False
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_keyframe_request < self._KEYFRAME_MIN_INTERVAL_S:
                self._keyframe_requests_throttled += 1
                return False
            self._last_keyframe_request = now
        try:
            return bool(server.request_keyframe())
        except Exception as exc:  # noqa: BLE001
            logger.debug("request_keyframe failed: %s", exc)
            return False

    def map_normalized(self, nx: float, ny: float) -> tuple[int, int]:
        geometry = self._geometry
        if geometry.stream_width <= 0 or geometry.stream_height <= 0:
            raise ApiError("SCRCPY_FAILED", "镜像尚未就绪（无视频几何信息）", {"serial": self.serial})
        try:
            return map_normalized(nx, ny, geometry)
        except ValueError as exc:
            raise ApiError("INVALID_ARGUMENT", f"非法归一化坐标: {exc}", {"nx": nx, "ny": ny}) from exc

    def hello_payload(self) -> dict[str, Any]:
        return {"kind": "hello", "serial": self.serial, **self._hello_fields()}

    def _hello_fields(self) -> dict[str, Any]:
        geometry = self._geometry
        video = self._video_geometry
        return {
            "session_id": self.session_id,
            "width": geometry.stream_width,
            "height": geometry.stream_height,
            "rotation": geometry.rotation,
            "codec": video.codec,
            "sps_b64": _b64(video.sps),
            "pps_b64": _b64(video.pps),
            "max_size": self.cfg.max_size,
            "max_fps": self.cfg.max_fps,
        }


def _b64(data: bytes | None) -> str:
    import base64

    return base64.b64encode(data).decode("ascii") if data else ""


# ---------------------------------------------------------------- 设备尺寸探测
def _probe_wm_size(serial: str) -> tuple[int, int]:
    """``wm size`` → (Physical 宽, 高)，拿不到返回 (0, 0)；不抛异常。"""
    import re

    try:
        ops = scrcpy.AdbOps(serial)
        output = ops.shell_text("wm size", timeout=5.0)
    except Exception:  # noqa: BLE001 - 无 adb/无设备
        return (0, 0)
    matches = re.findall(r"(?:Override|Physical) size:\s*(\d+)x(\d+)", output or "")
    if not matches:
        return (0, 0)
    width, height = matches[-1]  # Override 优先
    return int(width), int(height)


# ---------------------------------------------------------------- 模块级 registry（§14.3）
_SESSIONS: dict[str, MirrorSession] = {}
_SESSIONS_LOCK = threading.RLock()


def get_session(serial: str) -> MirrorSession | None:
    with _SESSIONS_LOCK:
        return _SESSIONS.get(serial)


def list_sessions() -> dict[str, MirrorSession]:
    with _SESSIONS_LOCK:
        return dict(_SESSIONS)


def start_session(
    serial: str,
    cfg: scrcpy.ServerConfig | None = None,
    *,
    ops: scrcpy.AdbOps | None = None,
    starter: Callable[[scrcpy.ServerConfig], Any] | None = None,
    size_probe: Callable[[], tuple[int, int]] | None = None,
) -> MirrorSession:
    """启动（或复用）某 serial 的镜像会话；无设备 → DEVICE_UNAVAILABLE，启动失败 → SCRCPY_FAILED。"""
    serial = str(serial or "").strip()
    if not serial:
        raise ApiError("INVALID_ARGUMENT", "serial 不能为空")
    with _SESSIONS_LOCK:
        existing = _SESSIONS.get(serial)
        if existing is not None and existing.active:
            return existing
        if existing is not None:
            existing.stop()
            _SESSIONS.pop(serial, None)
        session = MirrorSession(serial, cfg, starter=starter, size_probe=size_probe)
        _SESSIONS[serial] = session
    try:
        if starter is None:
            scrcpy.ensure_device_online(serial, ops)
        session.start()
    except Exception:
        with _SESSIONS_LOCK:
            _SESSIONS.pop(serial, None)
        session.stop()
        raise
    return session


def stop_session(serial: str) -> dict[str, Any]:
    """停止并移除会话（幂等）。"""
    serial = str(serial or "").strip()
    with _SESSIONS_LOCK:
        session = _SESSIONS.pop(serial, None)
    if session is not None:
        session.stop()
    return {"active": False, "serial": serial}


def mirror_status(serial: str) -> dict[str, Any]:
    """契约 §14.3：``{active,width,height,rotation,...}``，无会话时 active=False。"""
    serial = str(serial or "").strip()
    session = get_session(serial)
    if session is None:
        return {"active": False, "serial": serial, "state": "idle"}
    data = session.status()
    data["active"] = session.active
    return data


def map_normalized_to_device(serial: str, nx: float, ny: float) -> tuple[int, int] | None:
    """契约 §14.3：无活跃镜像返回 None；否则返回设备当前方向像素坐标。"""
    session = get_session(str(serial or "").strip())
    if session is None or not session.active:
        return None
    return session.map_normalized(nx, ny)


def shutdown_all() -> None:
    """测试/退出时清理所有会话（只动本进程的 session）。"""
    with _SESSIONS_LOCK:
        sessions = list(_SESSIONS.values())
        _SESSIONS.clear()
    for session in sessions:
        try:
            session.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("shutdown mirror session %s failed: %s", session.serial, exc)
