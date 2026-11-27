"""脚本/节点/目标 的 Pydantic 契约（契约 §3）。

本模块是后端内部唯一的脚本结构定义，前端对应 `frontend/src/types/api.ts`。
语义校验（id 唯一、goto 目标、sub_script 存在性）在 `validate_script()`。
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# ---------------------------------------------------------------- 基础


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LooseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


STEP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

NodeType = Literal[
    "click",
    "long_click",
    "swipe",
    "drag",
    "input",
    "key",
    "wait",
    "app",
    "shell",
    "assert",
    "sub_script",
    "screenshot",
]
NODE_TYPES: tuple[str, ...] = (
    "click",
    "long_click",
    "swipe",
    "drag",
    "input",
    "key",
    "wait",
    "app",
    "shell",
    "assert",
    "sub_script",
    "screenshot",
)

TargetBy = Literal["text", "text_contains", "resource_id", "content_desc", "class_name", "xpath", "point"]


class Target(StrictModel):
    by: TargetBy
    value: str | None = None
    index: int | None = Field(default=0, ge=0)
    x: int | None = None
    y: int | None = None

    @model_validator(mode="after")
    def _check(self) -> Target:
        if self.by == "point":
            if self.x is None or self.y is None:
                raise ValueError("point target requires x and y")
            if self.x < 0 or self.y < 0:
                raise ValueError("point coordinates must be >= 0")
        else:
            if not self.value:
                raise ValueError(f"target by={self.by} requires value")
        return self


# ---------------------------------------------------------------- 条件（when）
# v0.2 条件树只有一个结构：when = {mode, timeout_ms, poll_interval_ms, condition}。
# condition 是带 type 判别字段的递归联合；v0.2 实现 target/app/time/group/not，
# variable/device 留给后续版本（新增 type 即可，不破坏现有脚本）。

CONDITION_MAX_DEPTH = 4
_CONDITION_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class TargetCondition(StrictModel):
    type: Literal["target"]
    target: Target
    state: Literal["visible", "gone"] = "visible"

    @model_validator(mode="after")
    def _check_target(self) -> TargetCondition:
        if self.target.by == "point":
            raise ValueError("point target cannot be used as a visibility condition")
        return self


class AppCondition(StrictModel):
    type: Literal["app"]
    package: str = Field(min_length=1)
    activity: str | None = None
    state: Literal["foreground", "background"] = "foreground"


class TimeCondition(StrictModel):
    type: Literal["time"]
    start: str  # HH:MM（宿主本地时区）
    end: str  # HH:MM；start > end 表示跨午夜
    days: list[Annotated[int, Field(ge=1, le=7)]] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def _check(self) -> TimeCondition:
        for field_name in ("start", "end"):
            if not _CONDITION_TIME_RE.match(getattr(self, field_name)):
                raise ValueError(f"{field_name} must be HH:MM")
        if len(set(self.days)) != len(self.days):
            raise ValueError("days must not contain duplicates")
        return self


class GroupCondition(StrictModel):
    type: Literal["group"]
    op: Literal["and", "or"]
    items: list[Condition] = Field(min_length=1, max_length=50)


class NotCondition(StrictModel):
    type: Literal["not"]
    item: Condition


Condition = Annotated[
    TargetCondition | AppCondition | TimeCondition | GroupCondition | NotCondition,
    Field(discriminator="type"),
]
GroupCondition.model_rebuild()
NotCondition.model_rebuild()


def _condition_field(condition: Any, name: str, default: Any = None) -> Any:
    if isinstance(condition, dict):
        return condition.get(name, default)
    return getattr(condition, name, default)


def condition_depth(condition: Any) -> int:
    """group/not 的嵌套层数（叶子=0）。"""
    kind = str(_condition_field(condition, "type", "") or "")
    if kind == "group":
        children = _condition_field(condition, "items", []) or []
        if not children:
            return 1
        return 1 + max(condition_depth(child) for child in children)
    if kind == "not":
        return 1 + condition_depth(_condition_field(condition, "item"))
    return 0


class When(StrictModel):
    mode: Literal["require", "optional"] = "require"
    timeout_ms: int = Field(default=3000, ge=0, le=600_000)
    poll_interval_ms: int = Field(default=350, ge=50, le=5_000)
    condition: Condition

    @model_validator(mode="after")
    def _check_depth(self) -> When:
        depth = condition_depth(self.condition)
        if depth > CONDITION_MAX_DEPTH:
            raise ValueError(
                f"condition nesting depth {depth} exceeds max {CONDITION_MAX_DEPTH}"
            )
        return self


class Retry(StrictModel):
    """重试策略：count 是额外重试次数（count=1 表示最多执行 2 次）。

    - 节点 ``retry=null``：继承脚本默认重试；``retry={count:0}``：显式不重试；
    - 脚本 ``settings.retry=null``：默认不重试；``count=0`` 没有独立语义，前端归一到 null。
    """

    count: int = Field(default=1, ge=0, le=20)
    interval_ms: int = Field(default=1000, ge=0, le=60_000)


class OnFail(StrictModel):
    action: Literal["abort", "continue", "goto"] = "abort"
    target_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> OnFail:
        if self.action == "goto" and not self.target_id:
            raise ValueError("on_fail.goto requires target_id")
        return self


# ---------------------------------------------------------------- 12 类 params
KeyName = Literal[
    "back",
    "home",
    "recents",
    "enter",
    "delete",
    "volume_up",
    "volume_down",
    "power",
    "menu",
    "notification",
]


class ClickParams(StrictModel):
    target: Target


class LongClickParams(StrictModel):
    target: Target
    duration_ms: int = Field(default=800, ge=100, le=10_000)


class GestureSample(StrictModel):
    """录制轨迹采样点：x,y 设备像素，dt_ms 相对手势开始的毫秒偏移。"""

    x: int
    y: int
    dt_ms: int = Field(ge=0, le=60_000)


class _FromToParams(StrictModel):
    from_: Target = Field(alias="from")
    to: Target
    duration_ms: int = Field(default=300, ge=50, le=10_000)
    # 录制轨迹（可选）：有则按真实轨迹回放（弧形/变速滑动与录制一致），无则两点直线 swipe
    path: list[GestureSample] | None = None
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SwipeParams(_FromToParams):
    duration_ms: int = Field(default=300, ge=50, le=10_000)


class DragParams(_FromToParams):
    duration_ms: int = Field(default=1000, ge=100, le=10_000)


class InputParams(StrictModel):
    text: str
    target: Target | None = None
    clear_first: bool = False
    # 输入完收起输入法：横屏/游戏内 IME 会盖住下面的按钮，点击会落到键盘上导致提交无效。
    hide_keyboard: bool = False


class KeyParams(StrictModel):
    key: KeyName | None = None
    keycode: int | None = None

    @model_validator(mode="after")
    def _check(self) -> KeyParams:
        if self.key is None and self.keycode is None:
            raise ValueError("key or keycode required")
        return self


class WaitParams(StrictModel):
    mode: Literal["sleep", "until"] = "sleep"
    ms: int | None = Field(default=None, ge=0, le=600_000)
    target: Target | None = None
    state: Literal["visible", "gone"] = "visible"
    timeout_ms: int = Field(default=5000, ge=100, le=600_000)
    interval_ms: int = Field(default=350, ge=100, le=5000)

    @model_validator(mode="after")
    def _check(self) -> WaitParams:
        if self.mode == "sleep":
            if self.ms is None:
                raise ValueError("sleep wait requires ms")
        elif self.target is None:
            raise ValueError("until wait requires target")
        return self


class AppParams(StrictModel):
    action: Literal["launch", "stop", "clear", "restart", "grant"]
    package: str | None = None
    activity: str | None = None


class ShellParams(StrictModel):
    command: str = Field(min_length=1)
    timeout_ms: int = Field(default=10_000, ge=100, le=600_000)
    check_exit_code: bool = True


class AssertParams(StrictModel):
    kind: Literal["exists", "not_exists", "text_contains", "foreground"]
    target: Target | None = None
    value: str | None = None

    @model_validator(mode="after")
    def _check(self) -> AssertParams:
        if self.kind in ("exists", "not_exists", "text_contains") and self.target is None:
            raise ValueError(f"assert kind={self.kind} requires target")
        if self.kind in ("text_contains", "foreground") and not self.value:
            raise ValueError(f"assert kind={self.kind} requires value")
        return self


class SubScriptParams(StrictModel):
    script_id: str
    variables: dict[str, str] = Field(default_factory=dict)


class ScreenshotParams(StrictModel):
    name: str | None = None


# ---------------------------------------------------------------- 节点联合


class _StepBase(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = ""
    description: str = ""
    enabled: bool = True
    when: When | None = None
    timeout_ms: int | None = Field(default=None, ge=100, le=600_000)
    delay_before_ms: int = Field(default=0, ge=0, le=600_000)
    delay_after_ms: int = Field(default=0, ge=0, le=600_000)
    retry: Retry | None = None
    on_fail: OnFail | None = None
    screenshot_on_fail: bool = True
    rerun_safety: Literal["safe", "destructive", "stateful"] = "safe"
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_id(self) -> _StepBase:
        if not STEP_ID_RE.match(self.id):
            raise ValueError("step id must match [A-Za-z0-9_-]{1,64}")
        return self


class ClickStep(_StepBase):
    type: Literal["click"]
    params: ClickParams


class LongClickStep(_StepBase):
    type: Literal["long_click"]
    params: LongClickParams


class SwipeStep(_StepBase):
    type: Literal["swipe"]
    params: SwipeParams


class DragStep(_StepBase):
    type: Literal["drag"]
    params: DragParams


class InputStep(_StepBase):
    type: Literal["input"]
    params: InputParams


class KeyStep(_StepBase):
    type: Literal["key"]
    params: KeyParams


class WaitStep(_StepBase):
    type: Literal["wait"]
    params: WaitParams


class AppStep(_StepBase):
    type: Literal["app"]
    params: AppParams


class ShellStep(_StepBase):
    type: Literal["shell"]
    params: ShellParams


class AssertStep(_StepBase):
    type: Literal["assert"]
    params: AssertParams


class SubScriptStep(_StepBase):
    type: Literal["sub_script"]
    params: SubScriptParams


class ScreenshotStep(_StepBase):
    type: Literal["screenshot"]
    params: ScreenshotParams


Step = Annotated[
    ClickStep | LongClickStep | SwipeStep | DragStep | InputStep | KeyStep | WaitStep | AppStep | ShellStep | AssertStep | SubScriptStep | ScreenshotStep,
    Field(discriminator="type"),
]

STEP_MODELS: dict[str, type[_StepBase]] = {
    "click": ClickStep,
    "long_click": LongClickStep,
    "swipe": SwipeStep,
    "drag": DragStep,
    "input": InputStep,
    "key": KeyStep,
    "wait": WaitStep,
    "app": AppStep,
    "shell": ShellStep,
    "assert": AssertStep,
    "sub_script": SubScriptStep,
    "screenshot": ScreenshotStep,
}


class ScriptSettings(LooseModel):
    default_timeout_ms: int = Field(default=8000, ge=100, le=600_000)
    # 全节点默认停顿：节点自身 delay_*_ms=0（未单独设置）时使用，>0 即节点自定义。
    # 默认 0 = 不额外停顿；录制转换生成的脚本同样写 0，保留录制还原的真实间隔。
    default_delay_before_ms: int = Field(default=0, ge=0, le=600_000)
    default_delay_after_ms: int = Field(default=0, ge=0, le=600_000)
    on_fail: Literal["abort", "continue"] = "abort"
    logcat: bool = False
    # logcat 范围：device=整机；app=只保留脚本涉及应用（按运行时 PID 过滤）。
    logcat_scope: Literal["device", "app"] = "device"
    # 可选显式包名；留空时从 app 节点 params.package 推断。
    logcat_package: str = Field(default="", max_length=200)
    # 域名抓包：本次运行是否启用；还要求全局 capture.network.enabled=true。
    network_capture: bool = False
    retry: Retry | None = None
    screenshot_on_fail: bool = True
    # 新建节点默认重跑安全性：只作为新节点初值，不参与运行时继承。
    default_rerun_safety: Literal["safe", "destructive", "stateful"] = "safe"


class ScriptDefaults(LooseModel):
    retry: Retry | None = None


class ScriptData(LooseModel):
    schema_version: int = 1
    name: str = Field(min_length=1, max_length=200)
    notes: str = ""
    variables: dict[str, str] = Field(default_factory=dict)
    settings: ScriptSettings = Field(default_factory=ScriptSettings)
    defaults: ScriptDefaults = Field(default_factory=ScriptDefaults)
    steps: list[Step] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _fold_legacy_retry_defaults(self) -> "ScriptData":
        """把旧版脚本的 defaults.retry 折叠到 settings.retry，保持单一来源。

        旧版曾同时支持 defaults.retry 与 settings.retry，且引擎优先读 defaults.retry；
        编辑器只编辑 settings.retry，导致“脚本里看着没开、节点却继承了重试”。这里在
        解析时统一：settings.retry 已有值则保留，否则继承 defaults.retry，然后清空
        defaults.retry。存储层由前端 normalizeDraft 同步迁移，运行时这里兜底旧文件。
        """
        if self.defaults.retry is not None:
            if self.settings.retry is None:
                self.settings.retry = self.defaults.retry
            self.defaults.retry = None
        return self

    def step_ids(self) -> set[str]:
        return {step.id for step in self.steps}


# ---------------------------------------------------------------- 语义校验

# pydantic 会把 model_validator 里抛的 ValueError 包装成 "Value error, ..."，
# 这段前缀对用户没意义，展示前剥掉。
_PYDANTIC_MSG_PREFIXES = ("Value error, ", "Assertion failed, ")


def _strip_pydantic_prefix(message: str) -> str:
    for prefix in _PYDANTIC_MSG_PREFIXES:
        if message.startswith(prefix):
            return message[len(prefix) :]
    return message


def _error_subject(data: Any, loc: tuple[Any, ...]) -> str:
    """把「第几个节点」翻译成节点 id/名称，让报错能直接定位到节点（无则返回空串）。"""
    if not loc or loc[0] != "steps" or len(loc) < 2:
        return ""
    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list):
        return ""
    try:
        index = int(loc[1])
        step = steps[index]
    except (IndexError, TypeError, ValueError):
        return ""
    if not isinstance(step, dict):
        return ""
    step_id = str(step.get("id") or "").strip()
    name = str(step.get("name") or "").strip()
    if step_id and name:
        return f"节点 {step_id}（{name}）: "
    if step_id:
        return f"节点 {step_id}: "
    return f"第 {index + 1} 个节点: "


def validate_script(
    data: Any,
    known_script_ids: set[str] | None = None,
) -> dict[str, Any]:
    """返回 {valid, errors[], warnings[], script?}。errors 项：{path, code, message}。

    message 会带上节点 id/名称（如「节点 s3（点击1）: target by=text requires value」），
    因为前端 toast 只展示 message、不展示 path，只给 "target by=text requires value"
    用户根本不知道是哪个节点。
    """
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    try:
        script = ScriptData.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            loc = tuple(err.get("loc", ()))
            path = ".".join(str(part) for part in loc)
            message = _strip_pydantic_prefix(str(err.get("msg", "")))
            errors.append(
                {
                    "path": f"script.{path}" if path else "script",
                    "code": err.get("type", ""),
                    "message": f"{_error_subject(data, loc)}{message}",
                }
            )
        return {"valid": False, "errors": errors, "warnings": warnings}

    seen: set[str] = set()
    duplicated: set[str] = set()
    for step in script.steps:
        if step.id in seen:
            duplicated.add(step.id)
        seen.add(step.id)
    for dup in sorted(duplicated):
        errors.append({"path": f"steps[{dup}].id", "code": "duplicate_step_id", "message": f"节点 id 重复: {dup}"})

    for index, step in enumerate(script.steps):
        if step.on_fail and step.on_fail.action == "goto":
            target = step.on_fail.target_id or ""
            if target not in seen:
                errors.append(
                    {
                        "path": f"steps[{index}].on_fail.target_id",
                        "code": "goto_target_missing",
                        "message": f"goto 目标节点不存在: {target}",
                    }
                )
            elif target in duplicated:
                errors.append(
                    {
                        "path": f"steps[{index}].on_fail.target_id",
                        "code": "goto_target_ambiguous",
                        "message": f"goto 目标节点 id 重复，无法唯一定位: {target}",
                    }
                )

    for index, step in enumerate(script.steps):
        if step.type != "sub_script":
            continue
        sid = step.params.script_id
        if known_script_ids is not None and sid not in known_script_ids:
            warnings.append(
                {
                    "path": f"steps[{index}].params.script_id",
                    "code": "sub_script_missing",
                    "message": f"子脚本不存在（运行时会失败）: {sid}",
                }
            )
    return {"valid": not errors, "errors": errors, "warnings": warnings, "script": script}


def parse_script(data: Any) -> ScriptData:
    return ScriptData.model_validate(data)


def empty_script(name: str = "未命名脚本") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "name": name,
        "notes": "",
        "variables": {},
        "settings": {
            "default_timeout_ms": 8000,
            "default_delay_before_ms": 0,
            "default_delay_after_ms": 0,
            "on_fail": "abort",
            "logcat": False,
            "logcat_scope": "device",
            "logcat_package": "",
            "network_capture": False,
            "retry": None,
            "screenshot_on_fail": True,
            "default_rerun_safety": "safe",
        },
        "defaults": {"retry": None},
        "steps": [
            {
                "id": "s1",
                "name": "点击元素",
                "type": "click",
                "description": "",
                "enabled": True,
                "when": None,
                "timeout_ms": None,
                "delay_before_ms": 0,
                "delay_after_ms": 0,
                "retry": None,
                "on_fail": None,
                "screenshot_on_fail": True,
                "rerun_safety": "safe",
                "tags": [],
                "params": {"target": {"by": "text", "value": "登录", "index": 0}},
            }
        ],
    }
