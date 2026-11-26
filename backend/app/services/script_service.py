"""脚本服务（契约 §3 / §7.2）：文件化 CRUD + 校验 + 复制 + 导入导出。

- **脚本的真实存储是 JSON 文件**：`<scripts_dir>/<id>.json`，默认 `~/.autotest/scripts`；
  文件名就是 id，文件内容是 `{id,name,notes,source,origin_recording_id,schema_version,
  created_at,updated_at,data}`（`data` 就是契约里的脚本 JSON）；
- 数据库不再存脚本，只存运行记录（runs/step_results/...）；旧版 scripts 表由
  `migrate_db_scripts_to_files()` 一次性迁移；
- 写文件用「临时文件 + os.replace」原子替换；脚本 JSON 的唯一格式仍由 `app.schemas.ScriptData`
  定义，写入前一律 `schemas.validate_script()`（id 唯一、goto 目标等），失败抛 VALIDATION_ERROR；
- 文件名只允许 `[A-Za-z0-9_-]{1,64}`（防路径穿越）。
"""

from __future__ import annotations

import copy
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import config, db, schemas
from ..errors import ApiError, not_found
from ..logs import logger
from ..models import Recording, Run, Script, now_iso

MAX_LIMIT = 500
_SUFFIX = ".json"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ---------------------------------------------------------------- 文件存储
def _script_path(script_id: str) -> Path:
    sid = str(script_id)
    if not _ID_RE.match(sid):
        raise not_found("script", script_id)
    return config.scripts_dir() / f"{sid}{_SUFFIX}"


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("skip invalid script file %s: %s", path, exc)
        return None
    if not isinstance(raw, dict) or not raw.get("id"):
        logger.warning("skip script file without id: %s", path)
        return None
    return raw


def _write_record(record: dict[str, Any]) -> Path:
    path = _script_path(str(record["id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def _records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(config.scripts_dir().glob(f"*{_SUFFIX}")):
        record = _read_record(path)
        if record is not None:
            records.append(record)
    return records


def _record_sort_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(record.get("updated_at") or ""),
        str(record.get("created_at") or ""),
        str(record.get("id") or ""),
    )


def _record_to_dict(record: dict[str, Any], last_run: dict[str, Any] | None = None) -> dict[str, Any]:
    data = record.get("data") or {}
    return {
        "id": str(record["id"]),
        "name": str(record.get("name") or ""),
        "notes": str(record.get("notes") or ""),
        "source": str(record.get("source") or "manual"),
        "origin_recording_id": record.get("origin_recording_id"),
        "schema_version": int(record.get("schema_version") or 1),
        "data": data,
        "step_count": step_count(data),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "last_run": last_run,
    }


def read_record(script_id: str) -> dict[str, Any]:
    path = _script_path(script_id)
    record = _read_record(path) if path.exists() else None
    if record is None:
        raise not_found("script", script_id)
    return record


def read_script_data(script_id: str) -> dict[str, Any]:
    """给运行/子脚本用：只要 data（不存在 -> NOT_FOUND）。"""
    data = read_record(script_id).get("data")
    if not isinstance(data, dict):
        raise not_found("script", script_id)
    return copy.deepcopy(data)


def script_names(script_ids: set[str]) -> dict[str, str]:
    """批量取脚本名（给录制列表展示软关联用）。"""
    names: dict[str, str] = {}
    for record in _records():
        sid = str(record.get("id"))
        if sid in script_ids:
            names[sid] = str(record.get("name") or "")
    return names


# ---------------------------------------------------------------- 工具
def step_count(data: Any) -> int:
    if not isinstance(data, dict):
        return 0
    steps = data.get("steps")
    return len(steps) if isinstance(steps, list) else 0


def known_script_ids(session: Session | None = None) -> set[str]:
    return {str(record["id"]) for record in _records()}


def validate_data(session: Session | None, data: Any) -> dict[str, Any]:
    """结构 + 语义校验；返回 {valid, errors[], warnings[]}（可直接 JSON 返回）。"""
    result = schemas.validate_script(data, known_script_ids=known_script_ids(session))
    return {
        "valid": bool(result.get("valid")),
        "errors": result.get("errors") or [],
        "warnings": result.get("warnings") or [],
    }


def _ensure_valid(session: Session | None, data: dict[str, Any]) -> dict[str, Any]:
    result = validate_data(session, data)
    if not result["valid"]:
        raise ApiError("VALIDATION_ERROR", "script validation failed", {"errors": result["errors"]})
    for warning in result["warnings"]:
        logger.info("script validation warning: %s", warning.get("message"))
    return result


def _last_run_payload(row: Any) -> dict[str, Any]:
    """脚本列表/详情里的“最近一次运行”摘要（不含 steps/events）。"""
    return {
        "id": int(row.id),
        "status": row.status,
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "created_at": row.created_at,
        "device_serial": row.device_serial or "",
        "error": row.error,
        "summary": row.summary or None,
    }


def _last_runs(session: Session, script_ids: list[str]) -> dict[str, dict[str, Any]]:
    """一次查询取多个脚本的最新 Run，避免列表 N+1。"""
    ids = [str(script_id) for script_id in script_ids if script_id]
    if not ids:
        return {}
    ranked = (
        select(
            Run.id,
            Run.script_id,
            Run.status,
            Run.started_at,
            Run.ended_at,
            Run.created_at,
            Run.device_serial,
            Run.error,
            Run.summary,
            func.row_number()
            .over(partition_by=Run.script_id, order_by=Run.id.desc())
            .label("rn"),
        )
        .where(Run.script_id.in_(ids))
        .subquery()
    )
    rows = session.execute(select(ranked).where(ranked.c.rn == 1)).all()
    return {str(row.script_id): _last_run_payload(row) for row in rows}


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
    text = (query or "").strip().casefold()
    records = _records()
    if text:
        records = [
            record
            for record in records
            if text in str(record.get("name") or "").casefold()
            or text in str(record.get("notes") or "").casefold()
        ]
    records.sort(key=_record_sort_key, reverse=True)
    total = len(records)
    page = records[offset : offset + limit]
    last_runs = _last_runs(session, [str(record["id"]) for record in page])
    return {
        "items": [
            _record_to_dict(record, last_runs.get(str(record["id"])))
            for record in page
        ],
        "total": total,
    }


def get_script(session: Session, script_id: str) -> dict[str, Any]:
    record = read_record(script_id)
    return _record_to_dict(record, _last_runs(session, [str(record["id"])]).get(str(record["id"])))


def export_script(session: Session, script_id: str) -> dict[str, Any]:
    """导出脚本 JSON（唯一数据文件格式）；返回 {name, data}。"""
    record = read_record(script_id)
    return {
        "name": str(record.get("name") or ""),
        "data": copy.deepcopy(record.get("data") or {}),
    }


# ---------------------------------------------------------------- 写入
_DEFAULT_DELAY_KEYS = ("default_delay_before_ms", "default_delay_after_ms")


def _seed_default_delays(payload: dict[str, Any], *, force: bool = False) -> None:
    """新建脚本时把 App 级默认停顿（设置 → 执行 → 运行节奏）写成脚本初值。

    只影响「新建」：已显式带了这两个字段的 data（导入/模板）保持原样；
    运行时不做兜底，所以录制转换、复制出来的脚本不会被全局值改变节奏。
    """
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        settings = {}
        payload["settings"] = settings
    for key in _DEFAULT_DELAY_KEYS:
        # force：新建空脚本时模板里的 0 也要让位给全局初值（模板值不是用户显式设置）。
        if key in settings and not force:
            continue
        try:
            value = int(config.get(f"engine.{key}", 0) or 0)
        except (TypeError, ValueError):
            value = 0
        settings[key] = max(0, min(600_000, value))


def _save_record(
    *,
    data: dict[str, Any],
    name: str,
    notes: str = "",
    source: str = "manual",
    origin_recording_id: str | None = None,
    script_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    stamp = now_iso()
    record = {
        "id": script_id or _new_id(),
        "name": name,
        "notes": notes,
        "source": source,
        "origin_recording_id": origin_recording_id,
        "schema_version": int(data.get("schema_version") or 1),
        "created_at": created_at or stamp,
        "updated_at": stamp,
        "data": data,
    }
    _write_record(record)
    return record


def create_script(
    session: Session,
    name: str,
    notes: str = "",
    data: dict[str, Any] | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    payload = _normalize_payload(data, name=name, notes=notes)
    _seed_default_delays(payload, force=data is None)
    _ensure_valid(session, payload)
    record = _save_record(
        data=payload,
        name=str(payload["name"]),
        notes=str(payload.get("notes") or ""),
        source=source,
    )
    return _record_to_dict(record)


def create_script_record(
    session: Session | None,
    data: dict[str, Any],
    *,
    name: str,
    notes: str = "",
    source: str = "manual",
    origin_recording_id: str | None = None,
    script_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """录制转换用：带指定 id / 来源 / 来源录制的脚本落盘。"""
    payload = _normalize_payload(data, name=name, notes=notes)
    record = _save_record(
        data=payload,
        name=str(payload["name"]),
        notes=str(payload.get("notes") or ""),
        source=source,
        origin_recording_id=origin_recording_id,
        script_id=script_id,
        created_at=created_at,
    )
    return _record_to_dict(record)


def update_script(
    session: Session,
    script_id: str,
    name: str | None = None,
    notes: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = read_record(script_id)
    if data is not None and not isinstance(data, dict):
        raise ApiError("INVALID_ARGUMENT", "script data must be an object", {"type": type(data).__name__})
    payload = copy.deepcopy(data) if isinstance(data, dict) else copy.deepcopy(record.get("data") or {})
    target_name = (name or "").strip() or str(payload.get("name") or "").strip() or str(record.get("name") or "")
    payload["name"] = target_name
    if notes is None:
        payload.setdefault("notes", record.get("notes") or "")
    else:
        payload["notes"] = str(notes)
    payload.setdefault("schema_version", int(record.get("schema_version") or 1))
    _ensure_valid(session, payload)
    record["name"] = target_name
    record["notes"] = str(payload.get("notes") or "")
    record["schema_version"] = int(payload.get("schema_version") or 1)
    record["data"] = payload
    record["updated_at"] = now_iso()
    _write_record(record)
    return _record_to_dict(record, _last_runs(session, [str(record["id"])]).get(str(record["id"])))


def delete_script(session: Session, script_id: str) -> None:
    record = read_record(script_id)
    _script_path(str(record["id"])).unlink(missing_ok=True)
    # 录制只是软关联：删除脚本清空指向它的 recording.script_id，保留录制与事件本身
    session.execute(
        update(Recording)
        .where(Recording.script_id == str(script_id))
        .values(script_id=None)
    )
    session.flush()


def duplicate_script(session: Session, script_id: str, name: str | None = None) -> dict[str, Any]:
    record = read_record(script_id)
    new_name = (name or "").strip() or f"{record.get('name') or '脚本'} 副本"
    payload = copy.deepcopy(record.get("data") or {})
    payload["name"] = new_name
    _ensure_valid(session, payload)
    clone = _save_record(
        data=payload,
        name=new_name,
        notes=str(record.get("notes") or ""),
        source=str(record.get("source") or "manual"),
        origin_recording_id=record.get("origin_recording_id"),
    )
    return _record_to_dict(clone)


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
    record = _save_record(
        data=payload,
        name=str(payload["name"]),
        notes=str(payload.get("notes") or ""),
        source=source,
    )
    return _record_to_dict(record)


# ---------------------------------------------------------------- 迁移
def migrate_db_scripts_to_files() -> int:
    """一次性迁移：旧版脚本在 scripts 表里，v0.2.8 起以 JSON 文件为准。

    文件已存在（用户手改过）时不覆盖；全部写成功后再删 DB 行，失败则保留待下次重试。
    """
    with db.session_scope() as session:
        rows = session.execute(select(Script)).scalars().all()
        if not rows:
            return 0
        written = 0
        for row in rows:
            path = _script_path(row.id)
            if path.exists():
                continue
            _write_record(
                {
                    "id": row.id,
                    "name": row.name,
                    "notes": row.notes or "",
                    "source": row.source or "manual",
                    "origin_recording_id": row.origin_recording_id,
                    "schema_version": int(row.schema_version or 1),
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                    "data": copy.deepcopy(row.data or {}),
                }
            )
            written += 1
        for row in rows:
            session.delete(row)
        session.flush()
    logger.warning(
        "已把 %d 个脚本从数据库迁移到 %s（scripts 表已清空，脚本以 JSON 文件为准）",
        written,
        config.scripts_dir(),
    )
    return written


__all__ = [
    "MAX_LIMIT",
    "create_script",
    "create_script_record",
    "delete_script",
    "duplicate_script",
    "export_script",
    "get_script",
    "import_script",
    "known_script_ids",
    "list_scripts",
    "migrate_db_scripts_to_files",
    "read_record",
    "read_script_data",
    "script_names",
    "step_count",
    "update_script",
    "validate_data",
]
