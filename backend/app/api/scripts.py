"""脚本 API（契约 §3 / §7.2）：CRUD、校验、复制、导入导出、快捷运行。

- 业务错误一律 `ApiError`（统一信封），不在端点里手搓 JSONResponse；
- 导入支持 multipart（file 字段）与 JSON body `{data}` 两种（契约 §7.2）；
- 快捷运行 `POST /api/scripts/{id}/run` 转调 be-runner 的 `app.api.runs.create_run`，
  引擎未就绪时按契约返回 503 并说明（不重复实现 runs.py 的占用/快照逻辑）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..api_schemas import (
    DuplicateRequest,
    ScriptCreateRequest,
    ScriptUpdateRequest,
    ScriptValidateRequest,
)
from ..deps import get_db
from ..errors import ApiError
from ..logs import logger
from ..services import script_service

router = APIRouter(prefix="/api/scripts", tags=["scripts"])


class ScriptRunRequest(BaseModel):
    """快捷运行请求体（§7.2 `{serial,...}`；等价于 POST /api/runs 的子集）。"""

    serial: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    debug: dict[str, Any] | None = None


# ---------------------------------------------------------------- 查询
@router.get("")
def list_scripts(
    query: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    return script_service.list_scripts(session, query=query, limit=limit, offset=offset)


@router.get("/{script_id}")
def get_script(script_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    return script_service.get_script(session, script_id)


# ---------------------------------------------------------------- 写入
@router.post("")
def create_script(payload: ScriptCreateRequest, session: Session = Depends(get_db)) -> dict[str, Any]:
    return script_service.create_script(
        session, name=payload.name, notes=payload.notes, data=payload.data, source="manual"
    )


@router.put("/{script_id}")
def update_script(
    script_id: str, payload: ScriptUpdateRequest, session: Session = Depends(get_db)
) -> dict[str, Any]:
    return script_service.update_script(
        session, script_id, name=payload.name, notes=payload.notes, data=payload.data
    )


@router.delete("/{script_id}")
def delete_script(script_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    script_service.delete_script(session, script_id)
    return {"ok": True, "id": script_id}


@router.post("/{script_id}/duplicate")
def duplicate_script(
    script_id: str,
    payload: DuplicateRequest | None = Body(default=None),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    name = payload.name if payload is not None else None
    return script_service.duplicate_script(session, script_id, name=name)


# ---------------------------------------------------------------- 校验 / 导入导出
@router.post("/validate")
def validate_script(payload: ScriptValidateRequest, session: Session = Depends(get_db)) -> dict[str, Any]:
    """结构 + 语义校验：返回 {valid, errors, warnings}（不写库）。"""
    return script_service.validate_data(session, payload.data)


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", (name or "").strip()).strip(" .")
    cleaned = cleaned.replace(" ", "_")[:80]
    return cleaned or "script"


@router.get("/{script_id}/export")
def export_script(script_id: str, session: Session = Depends(get_db)) -> Response:
    exported = script_service.export_script(session, script_id)
    payload = json.dumps(exported["data"], ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"{_safe_filename(str(exported['name']))}.json"
    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "script.json"
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": disposition},
    )


def _parse_import_json(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError("VALIDATION_ERROR", f"invalid script json: {exc}", {"errors": []}) from exc
    if not isinstance(data, dict):
        raise ApiError("INVALID_ARGUMENT", "imported script must be a JSON object")
    return data


async def _read_import_payload(request: Request) -> tuple[dict[str, Any], str | None, str | None]:
    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type or "x-www-form-urlencoded" in content_type:
        try:
            form = await request.form()
        except Exception as exc:
            raise ApiError("INVALID_ARGUMENT", f"invalid multipart body: {exc}") from exc
        upload = form.get("file") or form.get("data")
        if upload is None:
            raise ApiError("INVALID_ARGUMENT", "multipart import requires a 'file' field")
        raw = await upload.read() if hasattr(upload, "read") else str(upload).encode("utf-8")
        name = form.get("name")
        notes = form.get("notes")
        data = _parse_import_json(raw)
        return data, (str(name) if name else None), (str(notes) if notes else None)
    raw = await request.body()
    if not raw:
        raise ApiError("INVALID_ARGUMENT", "import requires a JSON body or a multipart file")
    body = _parse_import_json(raw)
    if isinstance(body.get("data"), dict):
        name = body.get("name")
        notes = body.get("notes")
        return body["data"], (str(name) if name else None), (str(notes) if notes else None)
    return body, None, None


@router.post("/import")
async def import_script(request: Request, session: Session = Depends(get_db)) -> dict[str, Any]:
    """导入脚本：multipart 文件（field=file）或 JSON body `{data}`。"""
    data, name, notes = await _read_import_payload(request)
    return script_service.import_script(session, data, name=name, notes=notes)


# ---------------------------------------------------------------- 快捷运行
def _runner_create_run() -> Callable[..., dict[str, Any]] | None:
    """探测 be-runner 的普通函数 `app.api.runs.create_run`（不依赖 FastAPI Depends）。"""
    try:
        from ..runner import engine  # noqa: F401,PLC0415 - 仅探测引擎是否就绪
    except ImportError as exc:
        logger.info("runner engine not available yet: %s", exc)
        return None
    except Exception as exc:  # 引擎半成品/缺依赖
        logger.warning("runner engine failed to import: %s", exc)
        return None
    try:
        from . import runs as runs_api  # noqa: PLC0415 - be-runner 模块
    except ImportError as exc:
        logger.warning("app.api.runs not available yet: %s", exc)
        return None
    creator = getattr(runs_api, "create_run", None)
    return creator if callable(creator) else None


@router.post("/{script_id}/run")
def run_script(
    script_id: str,
    payload: ScriptRunRequest | None = Body(default=None),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """快捷运行 = POST /api/runs（脚本存在性 -> runner create_run -> 返回 run 资源）。"""
    script_service.get_script(session, script_id)  # 不存在 -> NOT_FOUND
    creator = _runner_create_run()
    if creator is None:
        raise ApiError(
            "INTERNAL",
            "run engine is not available yet (app.runner.engine missing); use POST /api/runs once backend runner lands",
            {"reason": "runner_not_ready", "script_id": script_id},
            status_code=503,
        )
    body = payload or ScriptRunRequest()
    result = creator(
        script_id,
        serial=body.serial,
        variables=body.variables or None,
        debug=body.debug,
    )
    return result if isinstance(result, dict) else {"ok": True, "result": result}


__all__ = ["ScriptRunRequest", "router"]
