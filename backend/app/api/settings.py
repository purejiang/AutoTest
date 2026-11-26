"""设置 / 健康检查 / 维护 API（契约 §7.6、§11，另有 §7.5 的 maintenance 汇总口径）。

- `GET/PUT/DELETE /api/settings*` 只操作 DB 层，effective 由 config 三级合并计算；
- `GET /api/health`：db 可写 + adb 路径/版本/在线设备数 + data_dir；
- `GET /api/maintenance/stats`、`POST /api/maintenance/cleanup`：按 retention.days 清理旧 run 与
  artifacts 文件（支持 dry_run，只删 data_dir 内的文件，绝不越界删除）；
- `/api/reports` 归 be-report，本文件不注册。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from .. import __version__, config, db, models
from ..api_schemas import CleanupRequest, SettingsUpdateRequest
from ..build_info import build_info
from ..deps import get_db
from ..logs import logger
from ..services import settings_service

router = APIRouter(prefix="/api", tags=["settings"])

_ACTIVE_RUN_STATUSES: tuple[str, ...] = ("queued", "running", "paused", "stopping")


# ---------------------------------------------------------------- 设置
@router.get("/settings")
def get_settings() -> dict[str, Any]:
    """{effective, sources, schema}（三级合并结果）。"""
    return settings_service.response()


@router.put("/settings")
def update_settings(
    payload: SettingsUpdateRequest, session: Session = Depends(get_db)
) -> dict[str, Any]:
    """values 中 null 表示删除该 key 的 DB 覆盖。"""
    return settings_service.update(session, payload.values)


@router.delete("/settings/{key}")
def delete_setting(key: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    return settings_service.delete(session, key)


@router.post("/settings/reset")
def reset_settings(session: Session = Depends(get_db)) -> dict[str, Any]:
    return settings_service.reset(session)


@router.post("/settings/reload")
def reload_settings() -> dict[str, Any]:
    """重新读取 config.json（手改文件后不用重启；启动项改动会进 restart_required）。"""
    return settings_service.reload()


@router.get("/settings/scrcpy/options")
def scrcpy_options() -> dict[str, Any]:
    """镜像参数白名单 + 当前 effective 值（渲染表单用）。"""
    return settings_service.scrcpy_options()


# ---------------------------------------------------------------- 健康检查
def _db_health() -> tuple[str, str | None]:
    try:
        with db.get_engine().connect() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS _autotest_health_probe (x INTEGER)"))
            conn.rollback()
        return "ok", None
    except Exception as exc:
        logger.warning("db health check failed", exc_info=True)
        return "error", str(exc)


def _data_dir_writable() -> tuple[bool, str | None]:
    try:
        directory = config.data_dir()
        probe = directory / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, None
    except OSError as exc:
        return False, str(exc)


def _adb_health() -> dict[str, Any]:
    info: dict[str, Any] = {"path": config.find_adb(), "version": "", "devices": 0}
    try:
        from ..android import adb  # noqa: PLC0415 - be-driver 模块可能尚未落地
    except ImportError:
        info["error"] = "adb module is not available yet"
        return info
    try:
        info["path"] = adb.adb_path() or info["path"]
        result = adb.run_adb(["version"], timeout_s=10)
        output = f"{result.stdout or ''}{result.stderr or ''}".strip()
        info["version"] = output.splitlines()[0].strip() if output else ""
        devices = adb.list_devices() or []
        info["devices"] = len([d for d in devices if str(d.get("state") or "").lower() == "device"])
    except Exception as exc:
        logger.warning("adb health check failed", exc_info=True)
        info["error"] = str(exc)
    return info


@router.get("/health")
def health() -> dict[str, Any]:
    db_state, db_error = _db_health()
    writable, write_error = _data_dir_writable()
    payload: dict[str, Any] = {
        "status": "ok" if db_state == "ok" and writable else "degraded",
        "version": __version__,
        "build": build_info(),
        "db": db_state,
        "adb": _adb_health(),
        "data_dir": str(config.data_dir()),
        "scripts_dir": str(config.scripts_dir()),
        "config_file": str(config.config_file_path()),
    }
    if db_error:
        payload["db_error"] = db_error
    if not writable:
        payload["data_dir_writable"] = False
        if write_error:
            payload["data_dir_error"] = write_error
    return payload


# ---------------------------------------------------------------- 维护
def _db_size() -> int:
    path = config.db_path()
    total = 0
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            if candidate.exists():
                total += candidate.stat().st_size
        except OSError:
            continue
    return total


def _dir_size(directory: Path) -> int:
    total = 0
    if not directory.exists():
        return 0
    for child in directory.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total


@router.get("/maintenance/stats")
def maintenance_stats(session: Session = Depends(get_db)) -> dict[str, Any]:
    runs = int(session.execute(select(func.count()).select_from(models.Run)).scalar_one())
    artifacts = int(session.execute(select(func.count()).select_from(models.Artifact)).scalar_one())
    oldest = session.execute(select(func.min(models.Run.created_at))).scalar_one()
    return {
        "db_size": _db_size(),
        "artifacts_size": _dir_size(config.artifacts_dir()),
        "runs": runs,
        "artifacts": artifacts,
        "oldest_run_at": oldest,
        "retention_days": int(config.get("retention.days") or 30),
    }


def _artifact_path(value: str, data_root: Path) -> Path | None:
    """只允许删除 data_dir 内的产物文件（防误删用户其它数据）。"""
    try:
        path = Path(str(value))
        if not path.is_absolute():
            path = data_root / path
        resolved = path.resolve()
        if not resolved.is_relative_to(data_root):
            logger.warning("skip artifact outside data dir: %s", resolved)
            return None
        return resolved
    except OSError:
        return None


def _cleanup_file(value: str, data_root: Path, dry_run: bool) -> tuple[int, bool]:
    """返回 (大小, 是否命中文件)；dry_run 时不删除但返回潜在释放字节。"""
    resolved = _artifact_path(value, data_root)
    if resolved is None or not resolved.is_file():
        return 0, False
    try:
        size = int(resolved.stat().st_size)
    except OSError:
        return 0, False
    if not dry_run:
        try:
            resolved.unlink(missing_ok=True)
        except OSError:
            logger.warning("failed to delete artifact file: %s", resolved, exc_info=True)
            return 0, False
    return size, True


@router.post("/maintenance/cleanup")
def maintenance_cleanup(
    payload: CleanupRequest | None = Body(default=None),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """按 retention.days 清理旧 run + artifacts 文件；dry_run=true 只统计。"""
    request = payload or CleanupRequest()
    days = int(request.retention_days or config.get("retention.days") or 30)
    dry_run = bool(request.dry_run)
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="milliseconds")
    runs = (
        session.execute(
            select(models.Run).where(
                models.Run.created_at < cutoff,
                models.Run.status.notin_(_ACTIVE_RUN_STATUSES),
            )
        )
        .scalars()
        .all()
    )
    run_ids = [run.id for run in runs]
    data_root = config.data_dir().resolve()
    artifacts_matched = 0
    reports_matched = 0
    files_deleted = 0
    freed_bytes = 0
    if run_ids:
        artifacts = (
            session.execute(select(models.Artifact).where(models.Artifact.run_id.in_(run_ids)))
            .scalars()
            .all()
        )
        artifacts_matched = len(artifacts)
        for artifact in artifacts:
            size, hit = _cleanup_file(artifact.path, data_root, dry_run)
            if hit:
                files_deleted += 1
                freed_bytes += size
        # 旧 run 关联的报告：行 + reports_dir 下 zip 一起清（仍只删 data_dir 内文件）
        reports = (
            session.execute(select(models.Report).where(models.Report.run_id.in_(run_ids)))
            .scalars()
            .all()
        )
        reports_matched = len(reports)
        for report in reports:
            size, hit = _cleanup_file(report.path, data_root, dry_run)
            if hit:
                files_deleted += 1
                freed_bytes += size
        if not dry_run:
            session.execute(delete(models.Artifact).where(models.Artifact.run_id.in_(run_ids)))
            session.execute(delete(models.Report).where(models.Report.run_id.in_(run_ids)))
            session.execute(delete(models.StepResult).where(models.StepResult.run_id.in_(run_ids)))
            session.execute(delete(models.RunEvent).where(models.RunEvent.run_id.in_(run_ids)))
            session.execute(delete(models.Run).where(models.Run.id.in_(run_ids)))
            session.flush()
    return {
        "dry_run": dry_run,
        "retention_days": days,
        "cutoff": cutoff,
        "runs_matched": len(run_ids),
        "runs_deleted": 0 if dry_run else len(run_ids),
        "artifacts_matched": artifacts_matched,
        "artifacts_deleted": 0 if dry_run else artifacts_matched,
        "reports_matched": reports_matched,
        "reports_deleted": 0 if dry_run else reports_matched,
        "files_deleted": files_deleted,
        "freed_bytes": freed_bytes,
    }


__all__ = ["router"]
