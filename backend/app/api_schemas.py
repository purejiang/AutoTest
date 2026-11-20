"""REST 请求体（契约 §7）。前端类型见 frontend/src/types/api.ts。

所有端点实现必须复用本文件的请求体，禁止各写一套。
响应体直接返回资源 dict / {"items","total"}；错误由 errors.py 统一。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def items_response(items: list[Any], total: int | None = None) -> dict[str, Any]:
    return {"items": items, "total": len(items) if total is None else total}


# ---------------------------------------------------------------- 设备手动控制
class PointRequest(ApiModel):
    """坐标：绝对设备像素 (x,y) 或镜像归一化 (nx,ny)，二选一。"""

    x: int | None = None
    y: int | None = None
    nx: float | None = None
    ny: float | None = None

    @model_validator(mode="after")
    def _check(self) -> PointRequest:
        has_abs = self.x is not None or self.y is not None
        has_norm = self.nx is not None or self.ny is not None
        if has_abs and has_norm:
            raise ValueError("use either x/y or nx/ny, not both")
        if has_abs and (self.x is None or self.y is None):
            raise ValueError("x and y must be provided together")
        if has_norm and (self.nx is None or self.ny is None):
            raise ValueError("nx and ny must be provided together")
        if has_norm and not (0.0 <= float(self.nx) <= 1.0 and 0.0 <= float(self.ny) <= 1.0):  # type: ignore[arg-type]
            raise ValueError("normalized coordinates must be within [0, 1]")
        if not has_abs and not has_norm:
            raise ValueError("coordinates required")
        return self


class LongPressRequest(PointRequest):
    duration_ms: int = Field(default=800, ge=100, le=10_000)


class SwipeRequest(ApiModel):
    x1: int | None = None
    y1: int | None = None
    x2: int | None = None
    y2: int | None = None
    nx1: float | None = None
    ny1: float | None = None
    nx2: float | None = None
    ny2: float | None = None
    duration_ms: int = Field(default=300, ge=50, le=10_000)

    @model_validator(mode="after")
    def _check(self) -> SwipeRequest:
        absolute = [self.x1, self.y1, self.x2, self.y2]
        normalized = [self.nx1, self.ny1, self.nx2, self.ny2]
        if any(v is not None for v in absolute) and any(v is not None for v in normalized):
            raise ValueError("use either absolute or normalized coordinates")
        if any(v is not None for v in absolute) and any(v is None for v in absolute):
            raise ValueError("x1,y1,x2,y2 must be provided together")
        if any(v is not None for v in normalized) and any(v is None for v in normalized):
            raise ValueError("nx1,ny1,nx2,ny2 must be provided together")
        if all(v is None for v in absolute) and all(v is None for v in normalized):
            raise ValueError("coordinates required")
        for v in (self.nx1, self.ny1, self.nx2, self.ny2):
            if v is not None and not 0.0 <= float(v) <= 1.0:
                raise ValueError("normalized coordinates must be within [0, 1]")
        return self


class TouchStreamRequest(ApiModel):
    """流式触摸（镜像拖动）：pointerdown/move/up 逐条下发，坐标相对视频帧归一化。"""

    action: Literal["down", "move", "up"]
    nx: float = Field(ge=0.0, le=1.0)
    ny: float = Field(ge=0.0, le=1.0)
    pointer_id: int = Field(default=0, ge=-3, le=16)


class KeyRequest(ApiModel):
    key: str | None = None
    keycode: int | None = None

    @model_validator(mode="after")
    def _check(self) -> KeyRequest:
        if self.key is None and self.keycode is None:
            raise ValueError("key or keycode required")
        return self


class TextRequest(ApiModel):
    text: str
    clear_first: bool = False


class AppRequest(ApiModel):
    action: Literal["launch", "stop", "clear", "restart"]
    package: str | None = None
    activity: str | None = None


class ShellRequest(ApiModel):
    command: str = Field(min_length=1)
    timeout_ms: int = Field(default=10_000, ge=100, le=600_000)


class ScreenRecordStartRequest(ApiModel):
    name: str | None = None
    bit_rate: int = Field(default=4_000_000, ge=1_000_000, le=20_000_000)
    time_limit_s: int = Field(default=180, ge=5, le=180)


class MirrorStartRequest(ApiModel):
    max_size: int | None = Field(default=None, ge=480, le=2560)
    max_fps: int | None = Field(default=None, ge=5, le=120)
    video_bit_rate: int | None = Field(default=None, ge=1_000_000, le=32_000_000)
    crop: str | None = None


class ConnectRequest(ApiModel):
    address: str


# ---------------------------------------------------------------- 脚本
class ScriptCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    notes: str = ""
    data: dict[str, Any] | None = None


class ScriptUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = None
    data: dict[str, Any] | None = None


class ScriptValidateRequest(BaseModel):
    data: dict[str, Any]


class DuplicateRequest(BaseModel):
    name: str | None = None


# ---------------------------------------------------------------- 运行
class DebugOptions(ApiModel):
    step_mode: bool = False
    breakpoints: list[str] = Field(default_factory=list)


class RunCreateRequest(BaseModel):
    script_id: str
    serial: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    debug: DebugOptions | None = None


class RunResumeRequest(ApiModel):
    ack: bool = True
    resume_kind: Literal["hot"] = "hot"


class RunDebugRequest(ApiModel):
    action: Literal["step", "continue", "run_from"]
    step_id: str | None = None


# ---------------------------------------------------------------- 录制
class RecordingCreateRequest(ApiModel):
    name: str | None = None
    serial: str
    mode: Literal["web", "physical"] = "web"


class ToScriptRequest(ApiModel):
    name: str | None = None
    include_kinds: list[str] | None = None
    exclude_seqs: list[int] = Field(default_factory=list)


# ---------------------------------------------------------------- 报告/维护/设置
class ReportCreateRequest(ApiModel):
    include_video: bool | None = None


class CleanupRequest(ApiModel):
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    dry_run: bool = False


class SettingsUpdateRequest(ApiModel):
    values: dict[str, Any]
