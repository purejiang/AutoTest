"""脚本/节点/目标 的 Pydantic 契约（契约 §3）。

本模块是后端内部唯一的脚本结构定义，前端对应 `frontend/src/types/api.ts`。
语义校验（id 唯一、goto 目标、sub_script 存在性）在 `validate_script()`。
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Union

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
    def _check(self) -> "Target":
        if self.by == "point":
            if self.x is None or self.y is None:
                raise ValueError("point target requires x and y")
            if self.x < 0 or self.y < 0:
                raise ValueError("point coordinates must be >= 0")
        else:
            if not self.value:
                raise ValueError(f"target by={self.by} requires value")
        return self


class When(StrictModel):
    target: Target
    mode: Literal["require", "optional"] = "require"
    timeout_ms: int = Field(default=3000, ge=100, le=600_000)
    state: Literal["visible", "gone"] = "visible"


class Retry(StrictModel):
    count: int = Field(default=1, ge=0, le=20)
    interval_ms: int = Field(default=1000, ge=0, le=60_000)


class OnFail(StrictModel):
    action: Literal["abort", "continue", "goto"] = "abort"
    target_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "OnFail":
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


class _FromToParams(StrictModel):
    from_: Target = Field(alias="from")
    to: Target
    duration_ms: int = Field(default=300, ge=50, le=10_000)
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SwipeParams(_FromToParams):
    duration_ms: int = Field(default=300, ge=50, le=10_000)


class DragParams(_FromToParams):
    duration_ms: int = Field(default=1000, ge=100, le=10_000)


class InputParams(StrictModel):
    text: str
    target: Target | None = None
    clear_first: bool = False


class KeyParams(StrictModel):
    key: KeyName | None = None
    keycode: int | None = None

    @model_validator(mode="after")
    def _check(self) -> "KeyParams":
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
    def _check(self) -> "WaitParams":
        if self.mode == "sleep":
            if self.ms is None:
                raise ValueError("sleep wait requires ms")
        elif self.target is None:
            raise ValueError("until wait requires target")
        return self


class AppParams(StrictModel):
    action: Literal["launch", "stop", "clear", "restart"]
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
    def _check(self) -> "AssertParams":
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
    def _check_id(self) -> "_StepBase":
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
    Union[
        ClickStep,
        LongClickStep,
        SwipeStep,
        DragStep,
        InputStep,
        KeyStep,
        WaitStep,
        AppStep,
        ShellStep,
        AssertStep,
        SubScriptStep,
        ScreenshotStep,
    ],
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
    on_fail: Literal["abort", "continue"] = "abort"
    logcat: bool = False
    retry: Retry | None = None
    screenshot_on_fail: bool = True


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

    def step_ids(self) -> set[str]:
        return {step.id for step in self.steps}


# ---------------------------------------------------------------- 语义校验


def validate_script(
    data: Any,
    known_script_ids: set[str] | None = None,
) -> dict[str, Any]:
    """返回 {valid, errors[], warnings[], script?}。errors 项：{path, code, message}。"""
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    try:
        script = ScriptData.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            path = ".".join(str(part) for part in err.get("loc", ()))
            errors.append(
                {
                    "path": f"script.{path}" if path else "script",
                    "code": err.get("type", ""),
                    "message": err.get("msg", ""),
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
            "on_fail": "abort",
            "logcat": False,
            "retry": None,
            "screenshot_on_fail": True,
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
