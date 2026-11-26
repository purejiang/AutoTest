"""运行 REST 端点（契约 §7.3）。

对外额外暴露一个**普通函数** `create_run()`（不依赖 FastAPI Depends），供 be-core 的
`POST /api/scripts/{id}/run` 快捷运行直接复用，保证快照/占用/录制冲突逻辑只有一份。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Query
from sqlalchemy import delete, func, select

from .. import config
from ..api_schemas import RunBulkDeleteRequest, RunCreateRequest, RunDebugRequest
from ..db import session_scope
from ..errors import ApiError
from ..events import RunEventBus
from ..logs import logger
from ..models import Artifact, Run, RunEvent, Script, StepResult
from ..runner import controls, engine
from ..runner.engine import TERMINAL_STATUSES
from ..schemas import validate_script

router = APIRouter(prefix="/api/runs", tags=["runs"])


# ================================================================ 普通函数入口
def create_run(
    script_id: str,
    serial: str | None = None,
    variables: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """创建并启动一次运行，返回 Run 资源 dict（GET /api/runs/{id} 的简洁形状）。

    步骤：脚本存在性/结构校验 → 录制冲突 409 → serial 解析 → Run 行(queued+快照) →
    acquire(serial, "run", str(run.id))（冲突 409 DEVICE_BUSY）→ engine.start_run。
    """
    script = _load_script(script_id)
    validation = validate_script(script["data"])
    if not validation.get("valid"):
        raise ApiError(
            "VALIDATION_ERROR",
            f"script validation failed: {script_id}",
            {"errors": validation.get("errors", [])},
        )
    resolved_serial = _resolve_serial(serial)
    if _is_recording(resolved_serial):
        raise ApiError(
            "CONFLICT",
            f"device {resolved_serial} is recording",
            {"serial": resolved_serial},
        )
    snapshot = dict(script["data"])
    run_id = _insert_run(
        script_id=script_id,
        script_name=str(snapshot.get("name") or script["name"]),
        snapshot=snapshot,
        serial=resolved_serial,
        variables=dict(variables or {}),
        debug=dict(debug or {}),
    )
    try:
        engine.acquire(resolved_serial, "run", str(run_id))
    except ApiError:
        _delete_run_rows(run_id)
        raise
    except Exception as exc:  # 占用接口异常统一映射为 DEVICE_BUSY
        _delete_run_rows(run_id)
        raise ApiError(
            "DEVICE_BUSY",
            f"device busy: {resolved_serial}",
            {"serial": resolved_serial, "reason": str(exc)},
        ) from exc
    engine.start_run(run_id)
    created = get_run(run_id) if _run_exists(run_id) else None
    if created is None:  # pragma: no cover - 极端情况下 run 行被并发删除
        raise ApiError("INTERNAL", "run created but not readable", {"run_id": run_id})
    return created


def create_run_from_request(payload: RunCreateRequest) -> dict[str, Any]:
    debug = payload.debug.model_dump() if payload.debug is not None else None
    return create_run(
        script_id=payload.script_id,
        serial=payload.serial,
        variables=dict(payload.variables or {}),
        debug=debug,
    )


# ================================================================ 端点
@router.post("")
def create_run_endpoint(payload: RunCreateRequest) -> dict[str, Any]:
    return create_run_from_request(payload)


@router.get("")
def list_runs(
    status: str | None = None,
    script_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    with session_scope() as session:
        stmt = select(Run)
        count_stmt = select(func.count()).select_from(Run)
        if status:
            stmt = stmt.where(Run.status == status)
            count_stmt = count_stmt.where(Run.status == status)
        if script_id:
            stmt = stmt.where(Run.script_id == script_id)
            count_stmt = count_stmt.where(Run.script_id == script_id)
        stmt = stmt.order_by(Run.id.desc()).limit(int(limit)).offset(int(offset))
        rows = session.execute(stmt).scalars().all()
        total = session.execute(count_stmt).scalar() or 0
        return {"items": [_run_dict(row) for row in rows], "total": int(total)}


@router.get("/{run_id}")
def get_run(run_id: int) -> dict[str, Any]:
    with session_scope() as session:
        run = session.get(Run, int(run_id))
        if run is None:
            raise ApiError("NOT_FOUND", f"run not found: {run_id}", {"run_id": run_id})
        steps = (
            session.execute(
                select(StepResult)
                .where(StepResult.run_id == int(run_id))
                .order_by(StepResult.id)
            )
            .scalars()
            .all()
        )
        artifacts = (
            session.execute(
                select(Artifact).where(Artifact.run_id == int(run_id)).order_by(Artifact.id)
            )
            .scalars()
            .all()
        )
        return {
            **_run_dict(run),
            "steps": [_step_dict(row) for row in steps],
            "artifacts": [_artifact_dict(row) for row in artifacts],
        }


@router.delete("/{run_id}")
def delete_run(run_id: int) -> dict[str, Any]:
    run = _get_run_or_404(run_id)
    if run["status"] not in TERMINAL_STATUSES:
        try:
            controls.request_stop(run_id)
        except ApiError:
            pass
    with session_scope() as session:
        artifacts = (
            session.execute(select(Artifact).where(Artifact.run_id == int(run_id)))
            .scalars()
            .all()
        )
        paths = [str(row.path) for row in artifacts]
        session.execute(delete(Artifact).where(Artifact.run_id == int(run_id)))
        session.execute(delete(StepResult).where(StepResult.run_id == int(run_id)))
        session.execute(delete(RunEvent).where(RunEvent.run_id == int(run_id)))
        session.execute(delete(Run).where(Run.id == int(run_id)))
    for path in paths:
        _unlink_artifact(path)
    run_dir = config.artifacts_dir() / f"run_{int(run_id)}"
    if run_dir.is_dir():
        shutil.rmtree(run_dir, ignore_errors=True)
    RunEventBus.drop_subscribers(run_id)
    controls.cleanup(run_id)
    return {"deleted": True, "run_id": int(run_id)}


@router.post("/bulk-delete")
def bulk_delete_runs(payload: RunBulkDeleteRequest) -> dict[str, Any]:
    """批量删除终态运行：去重、空列表 400、进行中不自动 stop（CONFLICT）。"""
    ids = list(dict.fromkeys(int(run_id) for run_id in payload.ids))
    if not ids:
        raise ApiError("INVALID_ARGUMENT", "ids must not be empty", {"ids": []})
    deleted = 0
    failed: list[dict[str, Any]] = []
    for run_id in ids:
        try:
            run = _get_run_or_404(run_id)
            if run["status"] not in TERMINAL_STATUSES:
                raise ApiError(
                    "CONFLICT",
                    f"run {run_id} is {run['status']}, only terminal runs can be deleted",
                    {"id": run_id, "status": run["status"]},
                )
            delete_run(run_id)
        except ApiError as exc:
            failed.append({"id": run_id, "code": exc.code, "message": exc.message})
        except Exception as exc:  # noqa: BLE001 - 单条失败不能中断整批
            logger.exception("bulk delete run failed: %s", run_id)
            failed.append(
                {"id": run_id, "code": "INTERNAL", "message": f"{type(exc).__name__}: {exc}"}
            )
        else:
            deleted += 1
    return {"deleted": deleted, "failed": failed}


@router.get("/{run_id}/events")
def list_run_events(
    run_id: int,
    since_seq: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=2000),
) -> dict[str, Any]:
    _get_run_or_404(run_id)
    items = RunEventBus.replay(run_id, since_seq, limit=limit)
    return {
        "items": items,
        "total": len(items),
        "last_seq": RunEventBus.last_seq(run_id),
    }


# ---------------------------------------------------------------- 控制
@router.post("/{run_id}/pause")
def pause_run(run_id: int) -> dict[str, Any]:
    _get_run_or_404(run_id)
    state = controls.request_pause(run_id)
    if state is None:
        raise ApiError("CONFLICT", "run is not active", {"run_id": int(run_id)})
    _set_status(run_id, "paused")
    return state


@router.post("/{run_id}/resume-check")
def resume_check(run_id: int, payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    run = _get_run_or_404(run_id)
    _reject_cold_resume(payload)
    state = controls.run_control_state(run_id)
    if run["status"] != "paused" or state is None or not state.get("paused"):
        raise ApiError(
            "CONFLICT",
            "run is not paused",
            {"run_id": int(run_id), "status": run["status"]},
        )
    device_alive = False
    foreground: dict[str, Any] | None = None
    try:
        driver = engine.get_driver(run["device_serial"])
        device_alive = bool(driver.alive())
        foreground = driver.current_app()
    except Exception:
        device_alive = False

    current_step_id = state.get("current_step_id") or run.get("current_step_id")
    current_step = _snapshot_step(run_id, current_step_id)
    rerun_safety = str((current_step or {}).get("rerun_safety") or "safe")
    destructive = current_step is not None and rerun_safety in ("destructive", "stateful")
    warnings: list[str] = list(state.get("resume_warnings") or [])
    if not device_alive:
        warnings.append("设备已离线，恢复后可能失败")
    if destructive:
        warnings.append(
            f"当前节点 {current_step_id} 有副作用（{rerun_safety}），热恢复可能重复执行"
        )
    return {
        "run_id": int(run_id),
        "status": run["status"],
        "resume_kind": "hot",
        "can_resume": True,
        "device_alive": device_alive,
        "foreground_snapshot": foreground,
        "destructive_current_node": destructive,
        "current_step_id": current_step_id,
        "warnings": warnings,
    }


@router.post("/{run_id}/resume")
def resume_run(run_id: int, payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    _get_run_or_404(run_id)
    _reject_cold_resume(payload)
    state = controls.request_resume(run_id)
    if state is None:
        raise ApiError("CONFLICT", "run is not active", {"run_id": int(run_id)})
    _set_status(run_id, "running")
    return {
        **state,
        "run_id": int(run_id),
        "warnings": list(state.get("resume_warnings") or []),
    }


@router.post("/{run_id}/stop")
def stop_run(run_id: int) -> dict[str, Any]:
    _get_run_or_404(run_id)
    state = controls.request_stop(run_id)
    if state is None:
        raise ApiError("CONFLICT", "run is not active", {"run_id": int(run_id)})
    _set_status(run_id, "stopping")
    return {**state, "run_id": int(run_id), "status": "stopping"}


@router.post("/{run_id}/debug")
def debug_run(run_id: int, payload: RunDebugRequest) -> dict[str, Any]:
    run = _get_run_or_404(run_id)
    if payload.action == "run_from":
        if not payload.step_id:
            raise ApiError("INVALID_ARGUMENT", "run_from requires step_id", {})
        if _snapshot_step(run_id, payload.step_id) is None:
            raise ApiError(
                "INVALID_ARGUMENT",
                f"step not found in run snapshot: {payload.step_id}",
                {"step_id": payload.step_id},
            )
    state = controls.request_debug(run_id, payload.action, payload.step_id)
    if state is None:
        raise ApiError("CONFLICT", "run is not active", {"run_id": int(run_id)})
    if payload.action == "run_from" and run["status"] == "paused":
        _set_status(run_id, "running")
    return state


# ================================================================ 内部工具
def _load_script(script_id: str) -> dict[str, Any]:
    with session_scope() as session:
        row = session.get(Script, str(script_id))
        if row is None:
            raise ApiError("NOT_FOUND", f"script not found: {script_id}", {"script_id": script_id})
        return {"id": row.id, "name": row.name, "data": dict(row.data or {})}


def _resolve_serial(serial: str | None) -> str:
    text = str(serial).strip() if serial is not None else ""
    if text and text.lower() != "auto":
        return text
    try:
        driver = engine.get_driver(None)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(
            "DEVICE_UNAVAILABLE", f"failed to resolve device: {exc}", {"serial": serial}
        ) from exc
    resolved = getattr(driver, "serial", None)
    if not resolved:
        raise ApiError("DEVICE_UNAVAILABLE", "no online device", {"serial": serial})
    return str(resolved)


def _is_recording(serial: str) -> bool:
    try:
        from ..recorder.hooks import is_recording
    except Exception:  # be-report 尚未落地
        return False
    try:
        return bool(is_recording(serial))
    except Exception:  # pragma: no cover - 录制模块异常不影响运行创建
        return False


def _insert_run(
    script_id: str,
    script_name: str,
    snapshot: dict[str, Any],
    serial: str,
    variables: dict[str, Any],
    debug: dict[str, Any],
) -> int:
    with session_scope() as session:
        run = Run(
            script_id=script_id,
            script_name=script_name,
            script_snapshot=snapshot,
            device_serial=serial,
            variables=dict(variables),
            debug=dict(debug),
            status="queued",
            step_index=0,
            total_steps=len(snapshot.get("steps") or []),
        )
        session.add(run)
        session.flush()
        return int(run.id)


def _delete_run_rows(run_id: int) -> None:
    with session_scope() as session:
        session.execute(delete(Run).where(Run.id == int(run_id)))


def _run_exists(run_id: int) -> bool:
    with session_scope() as session:
        return session.get(Run, int(run_id)) is not None


def _get_run_or_404(run_id: int) -> dict[str, Any]:
    with session_scope() as session:
        run = session.get(Run, int(run_id))
        if run is None:
            raise ApiError("NOT_FOUND", f"run not found: {run_id}", {"run_id": int(run_id)})
        return _run_dict(run)


def _set_status(run_id: int, status: str) -> None:
    with session_scope() as session:
        run = session.get(Run, int(run_id))
        if run is not None:
            run.status = status


def _snapshot_step(run_id: int, step_id: str | None) -> dict[str, Any] | None:
    if not step_id:
        return None
    with session_scope() as session:
        run = session.get(Run, int(run_id))
        if run is None or not run.script_snapshot:
            return None
        for step in run.script_snapshot.get("steps") or []:
            if step.get("id") == step_id:
                return dict(step)
    return None


def _reject_cold_resume(payload: dict[str, Any] | None) -> None:
    body = payload or {}
    resume_kind = str(body.get("resume_kind") or "hot")
    if resume_kind != "hot":
        raise ApiError(
            "UNSUPPORTED",
            "cold resume is not supported in v1",
            {"resume_kind": resume_kind},
        )


def _unlink_artifact(path: str) -> None:
    try:
        target = Path(path)
        if target.is_file():
            target.unlink()
    except OSError:
        logger.warning("failed to remove artifact file: %s", path)


def _variables_of(run: Run) -> tuple[dict[str, str], dict[str, str]]:
    """返回 (本次覆盖 variables, 生效 effective_variables)。

    生效值 = 运行快照里的脚本 variables + 本次请求覆盖；只有覆盖项才是“这次运行改了什么”，
    前端用它回显运行参数，避免把脚本默认值误当成覆盖。
    """
    snapshot = run.script_snapshot if isinstance(run.script_snapshot, dict) else {}
    raw_defaults = snapshot.get("variables")
    defaults: dict[str, str] = {}
    if isinstance(raw_defaults, dict):
        defaults = {str(key): str(value) for key, value in raw_defaults.items()}
    overrides = {str(key): str(value) for key, value in (run.variables or {}).items()}
    return overrides, {**defaults, **overrides}


def _run_dict(run: Run) -> dict[str, Any]:
    overrides, effective = _variables_of(run)
    return {
        "id": int(run.id),
        "script_id": run.script_id,
        "script_name": run.script_name,
        "device_serial": run.device_serial,
        "status": run.status,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "current_step_id": run.current_step_id,
        "step_index": run.step_index,
        "total_steps": run.total_steps,
        "error": run.error,
        "summary": run.summary,
        "created_at": run.created_at,
        "debug": run.debug,
        "variables": overrides,
        "effective_variables": effective,
    }


def _step_dict(row: StepResult) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "run_id": int(row.run_id),
        "step_id": row.step_id,
        "step_name": row.step_name,
        "step_type": row.step_type,
        "step_index": row.step_index,
        "status": row.status,
        "attempts": row.attempts,
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "duration_ms": row.duration_ms,
        "error": row.error,
        "output": row.output,
    }


def _artifact_url(path: str) -> str | None:
    """产物直链：/artifacts/<相对 artifacts_dir 的路径>（main.py 已挂载静态目录）。"""
    return config.artifact_url(path)


def _artifact_dict(row: Artifact) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "run_id": row.run_id,
        "step_result_id": row.step_result_id,
        "kind": row.kind,
        "name": row.name,
        "path": row.path,
        "url": _artifact_url(row.path),
        "size": row.size,
        "created_at": row.created_at,
    }
