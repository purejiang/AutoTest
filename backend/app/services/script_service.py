"""脚本服务（契约 §3 / §7.2）：CRUD + 校验 + 复制 + 导入导出。

- 脚本 JSON 唯一格式由 `app.schemas.ScriptData` 定义；写库前一律 `schemas.validate_script()`
  做结构 + 语义校验（id 唯一、goto 目标），失败抛 VALIDATION_ERROR（details.errors 逐项）；
- id 用 uuid4 hex（64 字符内，适配 scripts.id 列）；
- 短事务：全部函数接收上层传入的 Session，不自己开事务（没有会话时由调用方用 db.session_scope 包）。
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from .. import schemas
from ..errors import ApiError, not_found
from ..logs import logger
from ..models import Recording, Script, now_iso

MAX_LIMIT = 500


# ---------------------------------------------------------------- 工具
def step_count(data: Any) -> int:
    if not isinstance(data, dict):
        return 0
    steps = data.get("steps")
    return len(steps) if isinstance(steps, list) else 0


def known_script_ids(session: Session) -> set[str]:
    return set(session.execute(select(Script.id)).scalars().all())


def validate_data(session: Session, data: Any) -> dict[str, Any]:
    """结构 + 语义校验；返回 {valid, errors[], warnings[]}（可直接 JSON 返回）。"""
    result = schemas.validate_script(data, known_script_ids=known_script_ids(session))
    return {
        "valid": bool(result.get("valid")),
        "errors": result.get("errors") or [],
        "warnings": result.get("warnings") or [],
    }


def _ensure_valid(session: Session, data: dict[str, Any]) -> dict[str, Any]:
    result = validate_data(session, data)
    if not result["valid"]:
        raise ApiError("VALIDATION_ERROR", "script validation failed", {"errors": result["errors"]})
    for warning in result["warnings"]:
        logger.info("script validation warning: %s", warning.get("message"))
    return result


def _row_to_dict(row: Script) -> dict[str, Any]:
    data = row.data or {}
    return {
        "id": row.id,
        "name": row.name,
        "notes": row.notes or "",
        "source": row.source,
        "origin_recording_id": row.origin_recording_id,
        "schema_version": int(row.schema_version or 1),
        "data": data,
        "step_count": step_count(data),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _normalize_payload(data: Any, *, name: str, notes: str | None = None) -> dict[str, Any]:
    if data is None:
        payload = schemas.empty_script(name or "未命名脚本")
    elif isinstance(data, dict):
        payload = copy.deepcopy(data)
    else:
        raise ApiError(
            "INVALID_ARGUMENT", "script data must be an object", {"type": type(data).__name__}
        )
    target_name = (name or "").strip() or str(payload.get("name") or "").strip() or "未命名脚本"
    payload["name"] = target_name
    if notes is not None and str(notes).strip():
        payload["notes"] = str(notes)
    payload.setdefault("notes", "")
    payload.setdefault("schema_version", 1)
    return payload


def _new_id() -> str:
    return uuid.uuid4().hex


def get_row(session: Session, script_id: str) -> Script:
    row = session.get(Script, str(script_id))
    if row is None:
        raise not_found("script", script_id)
    return row


# ---------------------------------------------------------------- 查询
def list_scripts(
    session: Session,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """GET /api/scripts：name/notes 模糊匹配，items 带 step_count，total 为命中总数。"""
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))
    conditions = []
    text = (query or "").strip()
    if text:
        conditions.append(or_(Script.name.icontains(text, autoescape=True), Script.notes.icontains(text, autoescape=True)))
    count_stmt = select(func.count()).select_from(Script)
    rows_stmt = select(Script)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        rows_stmt = rows_stmt.where(*conditions)
    total = int(session.execute(count_stmt).scalar_one())
    rows = (
        session.execute(
            rows_stmt.order_by(Script.updated_at.desc(), Script.created_at.desc()).limit(limit).offset(offset)
        )
        .scalars()
        .all()
    )
    return {"items": [_row_to_dict(row) for row in rows], "total": total}


def get_script(session: Session, script_id: str) -> dict[str, Any]:
    return _row_to_dict(get_row(session, script_id))


def export_script(session: Session, script_id: str) -> dict[str, Any]:
    """导出脚本 JSON（唯一数据文件格式）；返回 {name, data}。"""
    row = get_row(session, script_id)
    return {"name": row.name, "data": copy.deepcopy(row.data or {})}


# ---------------------------------------------------------------- 写入
def create_script(
    session: Session,
    name: str,
    notes: str = "",
    data: dict[str, Any] | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    payload = _normalize_payload(data, name=name, notes=notes)
    _ensure_valid(session, payload)
    stamp = now_iso()
    row = Script(
        id=_new_id(),
        name=str(payload["name"]),
        notes=str(payload.get("notes") or ""),
        schema_version=int(payload.get("schema_version") or 1),
        data=payload,
        source=source,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(row)
    session.flush()
    return _row_to_dict(row)


def update_script(
    session: Session,
    script_id: str,
    name: str | None = None,
    notes: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = get_row(session, script_id)
    payload = copy.deepcopy(data) if isinstance(data, dict) else copy.deepcopy(row.data or {})
    if data is not None and not isinstance(data, dict):
        raise ApiError("INVALID_ARGUMENT", "script data must be an object", {"type": type(data).__name__})
    target_name = (name or "").strip() or str(payload.get("name") or "").strip() or row.name
    payload["name"] = target_name
    if notes is None:
        payload.setdefault("notes", row.notes or "")
    else:
        payload["notes"] = str(notes)
    payload.setdefault("schema_version", row.schema_version or 1)
    _ensure_valid(session, payload)
    row.name = target_name
    row.notes = str(payload.get("notes") or "")
    row.schema_version = int(payload.get("schema_version") or 1)
    row.data = payload
    row.updated_at = now_iso()
    session.flush()
    return _row_to_dict(row)


def delete_script(session: Session, script_id: str) -> None:
    row = get_row(session, script_id)
    # 录制只是软关联：删除脚本清空指向它的 recording.script_id，保留录制与事件本身
    session.execute(
        update(Recording)
        .where(Recording.script_id == str(script_id))
        .values(script_id=None)
    )
    session.delete(row)
    session.flush()


def duplicate_script(session: Session, script_id: str, name: str | None = None) -> dict[str, Any]:
    row = get_row(session, script_id)
    new_name = (name or "").strip() or f"{row.name} 副本"
    payload = copy.deepcopy(row.data or {})
    payload["name"] = new_name
    _ensure_valid(session, payload)
    stamp = now_iso()
    clone = Script(
        id=_new_id(),
        name=new_name,
        notes=row.notes or "",
        schema_version=int(row.schema_version or 1),
        data=payload,
        source=row.source,
        origin_recording_id=row.origin_recording_id,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(clone)
    session.flush()
    return _row_to_dict(clone)


def import_script(
    session: Session,
    data: dict[str, Any],
    name: str | None = None,
    notes: str | None = None,
    source: str = "imported",
) -> dict[str, Any]:
    """导入：接受脚本 JSON dict（multipart 文件读取与解析在 API 层）。"""
    if not isinstance(data, dict):
        raise ApiError("INVALID_ARGUMENT", "imported script must be a JSON object")
    imported_name = (name or "").strip() or str(data.get("name") or "").strip() or "导入脚本"
    payload = _normalize_payload(data, name=imported_name, notes=notes)
    _ensure_valid(session, payload)
    stamp = now_iso()
    row = Script(
        id=_new_id(),
        name=str(payload["name"]),
        notes=str(payload.get("notes") or ""),
        schema_version=int(payload.get("schema_version") or 1),
        data=payload,
        source=source,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(row)
    session.flush()
    return _row_to_dict(row)


__all__ = [
    "MAX_LIMIT",
    "create_script",
    "delete_script",
    "duplicate_script",
    "export_script",
    "get_row",
    "get_script",
    "import_script",
    "known_script_ids",
    "list_scripts",
    "step_count",
    "update_script",
    "validate_data",
]
