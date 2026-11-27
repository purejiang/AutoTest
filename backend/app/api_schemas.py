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


# ---------------------------------------------------------------- 响应体（OpenAPI / 生成前端类型用）
class OutModel(BaseModel):
    """响应体基类：模型声明字段用于 OpenAPI 与校验，未声明字段原样透传。

    extra="allow" 是刻意的：端点返回的是手写 dict，模型短期落后时不能让字段被静默丢弃。
    """

    model_config = ConfigDict(extra="allow")


class RunOut(OutModel):
    id: int
    script_id: str | None = None
    script_name: str = ""
    device_serial: str = ""
    status: str
    started_at: str | None = None
    ended_at: str | None = None
    current_step_id: str | None = None
    step_index: int = 0
    total_steps: int = 0
    error: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    created_at: str = ""
    debug: dict[str, Any] | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    effective_variables: dict[str, str] = Field(default_factory=dict)


class StepResultOut(OutModel):
    id: int
    run_id: int
    step_id: str = ""
    step_name: str = ""
    step_type: str = ""
    step_index: int = 0
    status: str = "passed"
    attempts: int = 1
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int = 0
    error: dict[str, Any] | None = None
    output: dict[str, Any] | None = None


class ArtifactOut(OutModel):
    id: int
    run_id: int | None = None
    step_result_id: int | None = None
    kind: str = "screenshot"
    name: str = ""
    path: str
    url: str | None = None
    size: int = 0
    created_at: str = ""


class RunListOut(OutModel):
    items: list[RunOut] = Field(default_factory=list)
    total: int = 0


class RunDetailOut(RunOut):
    steps: list[StepResultOut] = Field(default_factory=list)
    artifacts: list[ArtifactOut] = Field(default_factory=list)


class SettingEntryOut(OutModel):
    key: str
    type: str
    title: str
    desc: str | None = None
    min: int | float | None = None
    max: int | float | None = None
    options: list[dict[str, Any]] | None = None
    default: Any = None
    layer: str | None = None
    writable: bool | None = None
    env: str | None = None


class SettingsOut(OutModel):
    # 字段名沿用接口契约的 "schema"；Pydantic 会提示与 BaseModel.schema 同名，这里显式抑制。
    effective: dict[str, Any] = Field(default_factory=dict)
    sources: dict[str, str] = Field(default_factory=dict)
    schema_: list[SettingEntryOut] = Field(
        default_factory=list, alias="schema", serialization_alias="schema"
    )
    config_file: str = ""
    restart_required: list[str] | None = None

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class ScriptOut(OutModel):
    id: str
    name: str = ""
    notes: str = ""
    source: str = "manual"
    origin_recording_id: str | None = None
    schema_version: int = 1
    data: dict[str, Any] = Field(default_factory=dict)
    step_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    last_run: dict[str, Any] | None = None


class ScriptListOut(OutModel):
    items: list[ScriptOut] = Field(default_factory=list)
    total: int = 0


class RecordingOut(OutModel):
    id: str
    name: str = ""
    mode: str = "web"
    serial: str = ""
    status: str = ""
    started_at: str | None = None
    ended_at: str | None = None
    event_count: int = 0
    script_id: str | None = None
    script_name: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class RecordingListOut(OutModel):
    items: list[RecordingOut] = Field(default_factory=list)
    total: int = 0


class RunEventOut(OutModel):
    seq: int
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


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
    max_size: int | None = Field(default=None, ge=480, le=4096)
    max_fps: int | None = Field(default=None, ge=0, le=240)  # 0 = 不限
    video_bit_rate: int | None = Field(default=None, ge=1_000_000, le=32_000_000)
    crop: str | None = None


class MirrorDiagSample(BaseModel):
    """浏览器侧镜像诊断采样（每 5s 一条，落盘 JSONL 用于事后分析）。

    - 字段都取自前端已经算好的值；``session_id`` 用来把同一次镜像会话的样本归组；
    - **允许未知字段**：埋点/遥测要向前兼容——前端先更新（多带 display_hz/phase）而
      后端还没重启时，不能因为 ``extra="forbid"`` 把整条样本 422 掉（本轮就踩了这个坑：
      新前端 + 旧后端 → 全部 422 → 日志里一条客户端样本都没有，完全静默）。
    """

    model_config = ConfigDict(extra="ignore")

    session_id: str = ""
    width: int = Field(default=0, ge=0, le=16384)
    height: int = Field(default=0, ge=0, le=16384)
    render_fps: float = Field(default=0, ge=0, le=1000)
    server_fps: float | None = Field(default=None, ge=0, le=1000)
    draw_ms: float = Field(default=0, ge=0, le=10_000)
    queue_size: int = Field(default=0, ge=0, le=10_000)
    dropped: int = Field(default=0, ge=0)
    max_fps: int | None = Field(default=None, ge=0, le=1000)
    display_hz: float | None = Field(default=None, ge=1, le=1000)
    decoder_mode: str = ""
    phase: str = ""  # 页面状态：streaming / waiting-keyframe / reconnecting…
    awaiting_keyframe: bool = False
    hidden: bool = False


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


class ScriptBulkDeleteRequest(ApiModel):
    ids: list[str] = Field(max_length=200)


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


class RunBulkDeleteRequest(ApiModel):
    ids: list[int] = Field(max_length=200)


# ---------------------------------------------------------------- 录制
class RecordingCreateRequest(ApiModel):
    name: str | None = None
    serial: str
    mode: Literal["web", "physical"] = "web"


class RecordingBulkDeleteRequest(ApiModel):
    ids: list[str] = Field(max_length=200)


class RecordingEventBulkDeleteRequest(ApiModel):
    seqs: list[int] = Field(max_length=500)


class ToScriptRequest(ApiModel):
    name: str | None = None
    include_kinds: list[str] | None = None
    exclude_seqs: list[int] = Field(default_factory=list)
    # 定位方式：point=绝对坐标（默认）| element=元素优先
    target_mode: str | None = None


# ---------------------------------------------------------------- 报告/维护/设置
class ReportCreateRequest(ApiModel):
    include_video: bool | None = None


class CleanupRequest(ApiModel):
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    dry_run: bool = False


class SettingsUpdateRequest(ApiModel):
    values: dict[str, Any]
