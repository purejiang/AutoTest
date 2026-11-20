"""报告 REST（契约 §7.5）：生成 / 列表 / 下载（支持 Range）/ 删除。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from .. import config
from ..api_schemas import ReportCreateRequest, items_response
from ..db import session_scope
from ..errors import not_found
from ..logs import logger
from ..models import Report
from ..report.builder import build_report

router = APIRouter(tags=["reports"])


def _report_dict(report: Report) -> dict[str, Any]:
    return {
        "id": report.id,
        "report_id": report.id,
        "run_id": report.run_id,
        "status": report.status,
        "path": report.path,
        "size": report.size,
        "summary": report.summary or {},
        "created_at": report.created_at,
        "url": f"/api/reports/{report.id}/download",
    }


def _get_report_row(report_id: str) -> Report:
    with session_scope() as db:
        report = db.get(Report, str(report_id))
        if report is None:
            raise not_found("report", report_id)
        return report


@router.post("/api/runs/{run_id}/report")
def create_report(run_id: int, request: ReportCreateRequest | None = None) -> dict[str, Any]:
    if request is not None and request.include_video is not None:
        include_video = bool(request.include_video)
    else:
        # 未显式指定时尊重设置项（默认 true = 报告排除视频）
        include_video = not bool(config.get("retention.report_exclude_video"))
    result = build_report(run_id, include_video=include_video)
    report = _get_report_row(result["report_id"])
    payload = _report_dict(report)
    payload.update(
        {
            "report_id": result["report_id"],
            "path": result["path"],
            "size": result["size"],
            "url": result["url"],
            "summary": result.get("summary", payload["summary"]),
        }
    )
    return payload


@router.get("/api/reports")
def list_reports(
    run_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    with session_scope() as db:
        condition = Report.run_id == int(run_id) if run_id is not None else None
        count_statement = select(func.count()).select_from(Report)
        list_statement = select(Report).order_by(Report.created_at.desc(), Report.id.desc())
        if condition is not None:
            count_statement = count_statement.where(condition)
            list_statement = list_statement.where(condition)
        total = int(db.scalar(count_statement) or 0)
        rows = list(db.scalars(list_statement.limit(limit).offset(offset)))
    return items_response([_report_dict(row) for row in rows], total=total)


@router.get("/api/reports/{report_id}/download")
def download_report(report_id: str) -> FileResponse:
    report = _get_report_row(report_id)
    path = Path(str(report.path or ""))
    if not path.is_file():
        raise not_found("report file", report_id)
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"autotest-report-{report.run_id}-{report.id}.zip",
    )


@router.delete("/api/reports/{report_id}")
def delete_report(report_id: str) -> dict[str, Any]:
    report = _get_report_row(report_id)
    path = Path(str(report.path or ""))
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover - 文件系统异常
        logger.warning("report file delete failed: %s (%s)", path, exc)
    with session_scope() as db:
        row = db.get(Report, str(report_id))
        if row is not None:
            db.delete(row)
    logger.info("report deleted: %s", report_id)
    return {"deleted": True, "id": str(report_id)}
