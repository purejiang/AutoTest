"""物理触摸坐标映射：ABS 线性标定 + 旋转时间线（契约 §0 / §8）。

物理录制链路：``getevent`` 拿到的是**触摸屏原生方向**的归一化坐标（0..1）；
用户期望注入的是**当前显示方向**的屏幕像素坐标。本模块负责：

1. ``calibrate(raw_points, screen_points)``：最小二乘拟合 ``screen = a * raw + b``（逐轴独立，
   含 offset/scale；1 个点只能定 offset，≥2 个点定 scale）；
2. ``add_rotation(rotation, at_ts)``：记录旋转时间线（录制过程中设备可能旋转）；
3. ``apply(nx, ny, at_ts)``：raw 归一化 → 标定 → 按 ``at_ts`` 的旋转角转成当前显示方向
   归一化 → 乘以当前显示像素尺寸（自然方向 w/h 在 rot 90/270 时交换）→ 像素坐标。

旋转角接受 0/1/2/3（圈数）或 0/90/180/270（度）。``at_ts`` 建议与 ``getevent`` 时间戳同钟
（``time.monotonic()``）；不传时用 ``time.monotonic()``。

标定持久化：``config.data_dir()/touchmap_<serial>.json``（``to_dict``/``from_dict`` 可恢复）。
纯函数计算 + 文件 IO，无 adb/真机依赖。
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config
from ..errors import ApiError
from ..logs import logger

MAP_VERSION = 1


# ---------------------------------------------------------------- 旋转数学
def rotate_normalized(nx: float, ny: float, rotation: int) -> tuple[float, float]:
    """把**自然方向**归一化坐标旋转到当前显示方向（顺时针 rotation 度）。"""
    angle = normalize_rotation(rotation)
    if angle == 0:
        return nx, ny
    if angle == 90:
        return 1.0 - ny, nx
    if angle == 180:
        return 1.0 - nx, 1.0 - ny
    return ny, 1.0 - nx  # 270


def normalize_rotation(rotation: int) -> int:
    """0/1/2/3（圈数）或 0/90/180/270（度）→ 0/90/180/270。"""
    value = int(rotation)
    if value in (0, 1, 2, 3):
        value *= 90
    value %= 360
    if value % 90 != 0:
        raise ValueError(f"rotation must be 0/90/180/270 (or 0..3), got {rotation}")
    return value


# ---------------------------------------------------------------- 标定
@dataclass
class Calibration:
    """逐轴线性映射 ``screen = a * raw + b``（raw/screen 均为 0..1 归一化）。"""

    ax: float = 1.0
    bx: float = 0.0
    ay: float = 1.0
    by: float = 0.0

    def apply(self, nx: float, ny: float) -> tuple[float, float]:
        return self.ax * nx + self.bx, self.ay * ny + self.by

    def to_dict(self) -> dict[str, float]:
        return {"ax": self.ax, "bx": self.bx, "ay": self.ay, "by": self.by}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Calibration:
        data = data or {}
        return cls(
            ax=float(data.get("ax", 1.0)),
            bx=float(data.get("bx", 0.0)),
            ay=float(data.get("ay", 1.0)),
            by=float(data.get("by", 0.0)),
        )

    @property
    def is_identity(self) -> bool:
        return (self.ax, self.bx, self.ay, self.by) == (1.0, 0.0, 1.0, 0.0)


def _fit_axis(raw: list[float], target: list[float]) -> tuple[float, float]:
    """一维最小二乘 y = a*x + b；1 点或退化时 a=1（只给 offset）。"""
    if len(raw) == 1 or len(raw) < 2:
        return 1.0, target[0] - raw[0]
    mean_x = sum(raw) / len(raw)
    mean_y = sum(target) / len(target)
    var = sum((x - mean_x) ** 2 for x in raw)
    if var <= 1e-12:
        return 1.0, mean_y - mean_x
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(raw, target, strict=True))
    a = cov / var
    return a, mean_y - a * mean_x


# ---------------------------------------------------------------- TouchMapper
def _safe_name(serial: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(serial or "")) or "default"


def default_touchmap_path(serial: str) -> Path:
    return config.data_dir() / f"touchmap_{_safe_name(serial)}.json"


@dataclass
class TouchMapper:
    """物理触摸坐标映射器（可持久化标定 + 旋转时间线）。"""

    serial: str = ""
    screen_width: int = 0
    screen_height: int = 0
    rotation_timeline: list[tuple[float, int]] = field(default_factory=list)
    calibration: Calibration = field(default_factory=Calibration)
    updated_at: str = ""
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # ---- 屏幕尺寸
    def set_screen_size(self, width: int, height: int) -> None:
        """宽高为**自然方向**屏幕像素（wm size 的 Physical size）。"""
        if int(width) <= 0 or int(height) <= 0:
            raise ApiError("INVALID_ARGUMENT", f"非法屏幕尺寸: {width}x{height}")
        with self._lock:
            self.screen_width = int(width)
            self.screen_height = int(height)

    def display_size(self, rotation: int | None = None) -> tuple[int, int]:
        angle = normalize_rotation(rotation if rotation is not None else self.rotation_at())
        if self.screen_width <= 0 or self.screen_height <= 0:
            return (0, 0)
        if angle in (90, 270):
            return (self.screen_height, self.screen_width)
        return (self.screen_width, self.screen_height)

    # ---- 旋转时间线
    def add_rotation(self, rotation: int, at_ts: float) -> None:
        angle = normalize_rotation(rotation)
        timestamp = float(at_ts)
        with self._lock:
            timeline = [(ts, deg) for ts, deg in self.rotation_timeline if abs(ts - timestamp) > 1e-9]
            timeline.append((timestamp, angle))
            timeline.sort(key=lambda item: item[0])
            self.rotation_timeline = timeline

    def rotation_at(self, at_ts: float | None = None) -> int:
        with self._lock:
            if not self.rotation_timeline:
                return 0
            timestamp = time.monotonic() if at_ts is None else float(at_ts)
            angle = 0
            for ts, deg in self.rotation_timeline:
                if ts <= timestamp:
                    angle = deg
                else:
                    break
            return angle

    # ---- 标定
    def calibrate(self, raw_points: Iterable[tuple[float, float]], screen_points: Iterable[tuple[float, float]]) -> Calibration:
        """最小二乘标定 raw→screen（均为 0..1 归一化）；1 点只定 offset。"""
        raw = [(float(x), float(y)) for x, y in raw_points]
        target = [(float(x), float(y)) for x, y in screen_points]
        if not raw or not target:
            raise ApiError("INVALID_ARGUMENT", "标定至少需要 1 组点")
        if len(raw) != len(target):
            raise ApiError(
                "INVALID_ARGUMENT",
                f"标定点数量不匹配: raw={len(raw)} screen={len(target)}",
            )
        for x, y in raw + target:
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ApiError("INVALID_ARGUMENT", "标定点包含非有限数值")
        ax, bx = _fit_axis([p[0] for p in raw], [p[0] for p in target])
        ay, by = _fit_axis([p[1] for p in raw], [p[1] for p in target])
        calibration = Calibration(ax=ax, bx=bx, ay=ay, by=by)
        with self._lock:
            self.calibration = calibration
        logger.info(
            "touchmap calibrated for %s: x=%.4f*nx%+.4f y=%.4f*ny%+.4f (points=%d)",
            self.serial,
            ax,
            bx,
            ay,
            by,
            len(raw),
        )
        return calibration

    # ---- 应用
    def apply(self, nx: float, ny: float, at_ts: float | None = None) -> tuple[int, int]:
        """触摸屏归一化坐标 → 当前显示方向像素坐标（考虑标定 + 旋转时间线）。"""
        if not (math.isfinite(float(nx)) and math.isfinite(float(ny))):
            raise ApiError("INVALID_ARGUMENT", "非法触摸坐标（非有限数值）", {"nx": nx, "ny": ny})
        if self.screen_width <= 0 or self.screen_height <= 0:
            raise ApiError(
                "INVALID_ARGUMENT",
                "TouchMapper 屏幕尺寸未知，先 set_screen_size(w, h) 或从设备信息加载",
                {"serial": self.serial},
            )
        angle = self.rotation_at(at_ts)
        raw_x = min(max(float(nx), 0.0), 1.0)
        raw_y = min(max(float(ny), 0.0), 1.0)
        calibrated_x, calibrated_y = self.calibration.apply(raw_x, raw_y)
        calibrated_x = min(max(calibrated_x, 0.0), 1.0)
        calibrated_y = min(max(calibrated_y, 0.0), 1.0)
        display_x, display_y = rotate_normalized(calibrated_x, calibrated_y, angle)
        width, height = self.display_size(angle)
        px = int(round(display_x * (width - 1)))
        py = int(round(display_y * (height - 1)))
        return (min(max(px, 0), width - 1), min(max(py, 0), height - 1))

    # ---- 序列化
    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "version": MAP_VERSION,
                "serial": self.serial,
                "screen_width": self.screen_width,
                "screen_height": self.screen_height,
                "rotation_timeline": [[ts, deg] for ts, deg in self.rotation_timeline],
                "calibration": self.calibration.to_dict(),
                "updated_at": self.updated_at,
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None, *, serial: str = "") -> TouchMapper:
        data = data or {}
        timeline: list[tuple[float, int]] = []
        for item in data.get("rotation_timeline") or []:
            try:
                ts, deg = float(item[0]), normalize_rotation(int(item[1]))
            except (TypeError, ValueError, IndexError):
                continue
            timeline.append((ts, deg))
        timeline.sort(key=lambda item: item[0])
        mapper = cls(
            serial=str(data.get("serial") or serial),
            screen_width=int(data.get("screen_width") or 0),
            screen_height=int(data.get("screen_height") or 0),
            rotation_timeline=timeline,
            calibration=Calibration.from_dict(data.get("calibration")),
            updated_at=str(data.get("updated_at") or ""),
        )
        return mapper

    # ---- 持久化
    def save(self, path: Path | None = None) -> Path:
        target = Path(path) if path is not None else default_touchmap_path(self.serial)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        payload = self.to_dict()
        tmp = target.with_name(target.name + ".part")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(target)
        logger.info("touchmap saved for %s -> %s", self.serial, target)
        return target

    @classmethod
    def load(cls, serial: str, path: Path | None = None) -> TouchMapper:
        """读标定文件；不存在时返回空 mapper（屏幕尺寸 0，调用方需 set/探测）。"""
        target = Path(path) if path is not None else default_touchmap_path(serial)
        if not target.exists():
            return cls(serial=serial)
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("touchmap file %s unreadable (%s), using identity", target, exc)
            return cls(serial=serial)
        mapper = cls.from_dict(data, serial=serial)
        mapper.serial = serial or mapper.serial
        return mapper


# ---------------------------------------------------------------- 模块级便捷函数
def load_touchmap(serial: str, path: Path | None = None) -> TouchMapper:
    return TouchMapper.load(serial, path)


def save_touchmap(mapper: TouchMapper, path: Path | None = None) -> Path:
    return mapper.save(path)


def touchmap_path(serial: str) -> Path:
    return default_touchmap_path(serial)
