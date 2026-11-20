"""运行报告生成（契约 §7.5 / §14.5）。

产物为 zip：`index.html`（汇总 + 步骤表 + 失败截图缩略图）、`junit.xml`
（每 step 一个 testcase，整体一个 testsuite）、`steps.json`、截图文件（可选视频）。
视频默认排除（`retention.report_exclude_video` 由 API 层决定默认值）；
zip 超过 `retention.report_max_mb` 时删文件并抛 `ApiError("INVALID_ARGUMENT")`。
报告落 `config.reports_dir()`，并 insert `reports` 行。
"""

from __future__ import annotations

import json
import re
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select

from .. import config
from ..db import session_scope
from ..errors import ApiError, not_found
from ..logs import logger
from ..models import Artifact, Report, Run, StepResult, now_iso

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(default_for_string=True, default=True),
    trim_blocks=True,
    lstrip_blocks=True,
)

_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff_.-]+")
_VIDEO_KINDS = {"video", "screenrecord"}
_FAILED_STATUSES = {"failed", "error"}


# ================================================================ 小工具
def _safe_filename(name: str, fallback: str) -> str:
    cleaned = _UNSAFE_NAME_RE.sub("_", str(name or "").strip())[:80].strip("._")
    return cleaned or fallback


def _iso_seconds(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _run_duration_ms(run: Run, steps: list[StepResult]) -> int:
    if isinstance(run.summary, dict) and isinstance(run.summary.get("duration_ms"), (int, float)):
        return int(run.summary["duration_ms"])
    if run.started_at and run.ended_at:
        delta = _iso_seconds(run.ended_at) - _iso_seconds(run.started_at)
        if delta > 0:
            return int(delta * 1000)
    return int(sum(int(step.duration_ms or 0) for step in steps))


def _step_summary(steps: list[StepResult], duration_ms: int) -> dict[str, Any]:
    def count(status: str) -> int:
        return sum(1 for step in steps if step.status == status)

    failed = count("failed")
    errors = count("error")
    return {
        "total_steps": len(steps),
        "passed_steps": count("passed"),
        "failed_steps": failed,
        "skipped_steps": count("skipped"),
        "error_steps": errors,
        "failed_total": failed + errors,
        "duration_ms": duration_ms,
    }


def _resolve_artifact_path(artifact: Artifact) -> Path | None:
    raw = Path(str(artifact.path or ""))
    candidates = [raw]
    if not raw.is_absolute():
        candidates.append(config.artifacts_dir() / raw)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:  # pragma: no cover - 非法路径
            continue
    return None


def _artifact_extension(path: Path, default: str = ".png") -> str:
    suffix = path.suffix.lower()
    return suffix if suffix and len(suffix) <= 6 else default


def _error_message(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message") or error.get("msg")
        if message:
            code = error.get("code")
            return f"[{code}] {message}" if code else str(message)
        return json.dumps(error, ensure_ascii=False)[:500]
    return str(error or "")


def _error_detail(error: Any) -> str:
    if isinstance(error, dict):
        detail = error.get("traceback") or error.get("detail") or error.get("stack")
        if detail:
            return str(detail)
    return _error_message(error)


def _run_view(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "script_id": run.script_id,
        "script_name": run.script_name,
        "device_serial": run.device_serial,
        "status": run.status,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "error": run.error,
        "summary": run.summary,
        "total_steps": run.total_steps,
        "created_at": run.created_at,
    }


# ================================================================ junit.xml
def _junit_xml(run: Run, steps: list[StepResult], summary: dict[str, Any]) -> bytes:
    suite = ET.Element(
        "testsuite",
        {
            "name": run.script_name or f"run-{run.id}",
            "tests": str(len(steps)),
            "failures": str(summary.get("failed_steps", 0)),
            "errors": str(summary.get("error_steps", 0)),
            "skipped": str(summary.get("skipped_steps", 0)),
            "time": f"{int(summary.get('duration_ms', 0)) / 1000:.3f}",
            "timestamp": run.started_at or run.created_at or "",
        },
    )
    for step in steps:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": step.step_type or "autotest",
                "name": step.step_name or step.step_id or f"step-{step.step_index}",
                "time": f"{int(step.duration_ms or 0) / 1000:.3f}",
            },
        )
        if step.status in _FAILED_STATUSES:
            node_name = "failure" if step.status == "failed" else "error"
            node = ET.SubElement(case, node_name, {"message": _error_message(step.error)})
            node.text = _error_detail(step.error)
        elif step.status == "skipped":
            ET.SubElement(case, "skipped", {"message": _error_message(step.error) or "skipped"})
    ET.indent(suite, space="  ")
    return ET.tostring(suite, encoding="utf-8", xml_declaration=True)


# ================================================================ steps.json
def _steps_payload(
    run: Run,
    steps: list[StepResult],
    screenshot_map: dict[int, list[str]],
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for step in steps:
        payload.append(
            {
                "run_id": run.id,
                "step_id": step.step_id,
                "step_name": step.step_name,
                "step_type": step.step_type,
                "step_index": step.step_index,
                "status": step.status,
                "attempts": step.attempts,
                "started_at": step.started_at,
                "ended_at": step.ended_at,
                "duration_ms": step.duration_ms,
                "error": step.error,
                "output": step.output,
                "screenshots": screenshot_map.get(step.id, []),
            }
        )
    return payload


# ================================================================ 报告主流程
def build_report(run_id: int, include_video: bool = False) -> dict[str, Any]:
    """生成 zip 报告并落库；返回 {"report_id","path","size","url"}（另含 summary 扩展）。"""
    run_id = int(run_id)
    with session_scope() as db:
        run = db.get(Run, run_id)
        if run is None:
            raise not_found("run", run_id)
        steps = list(
            db.scalars(
                select(StepResult)
                .where(StepResult.run_id == run_id)
                .order_by(StepResult.step_index, StepResult.id)
            )
        )
        artifacts = list(db.scalars(select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.id)))

    duration_ms = _run_duration_ms(run, steps)
    summary = _step_summary(steps, duration_ms)

    screenshot_entries: list[tuple[Artifact, Path, str]] = []
    video_entries: list[tuple[Artifact, Path, str]] = []
    for artifact in artifacts:
        if artifact.kind == "screenshot":
            source = _resolve_artifact_path(artifact)
            if source is None:
                continue
            fallback = f"artifact_{artifact.id}"
            filename = (
                f"{len(screenshot_entries) + 1:03d}_"
                f"{_safe_filename(artifact.name, fallback)}{_artifact_extension(source)}"
            )
            screenshot_entries.append((artifact, source, f"screenshots/{filename}"))
        elif include_video and artifact.kind in _VIDEO_KINDS:
            source = _resolve_artifact_path(artifact)
            if source is None:
                continue
            fallback = f"artifact_{artifact.id}"
            filename = (
                f"{len(video_entries) + 1:03d}_"
                f"{_safe_filename(artifact.name, fallback)}{_artifact_extension(source, '.mp4')}"
            )
            video_entries.append((artifact, source, f"videos/{filename}"))

    screenshot_map: dict[int, list[str]] = {}
    for artifact, _source, arcname in screenshot_entries:
        if artifact.step_result_id is not None:
            screenshot_map.setdefault(int(artifact.step_result_id), []).append(arcname)

    steps_view: list[dict[str, Any]] = []
    for step in steps:
        steps_view.append(
            {
                "index": step.step_index,
                "step_id": step.step_id,
                "name": step.step_name or step.step_id,
                "type": step.step_type,
                "status": step.status,
                "duration_ms": step.duration_ms,
                "attempts": step.attempts,
                "error": _error_message(step.error),
                "output": step.output,
                "screenshots": screenshot_map.get(step.id, []),
            }
        )
    failed_shots = [
        {"step_name": item["name"], "file": file_name, "message": item["error"]}
        for item in steps_view
        if item["status"] in _FAILED_STATUSES
        for file_name in item["screenshots"]
    ]

    generated_at = now_iso()
    html = _ENV.get_template("report.html").render(
        run=_run_view(run),
        summary=summary,
        steps=steps_view,
        failed_shots=failed_shots,
        videos=[
            {"name": artifact.name or source.name, "file": arcname}
            for artifact, source, arcname in video_entries
        ],
        generated_at=generated_at,
        include_video=bool(include_video),
    )
    junit = _junit_xml(run, steps, summary)
    steps_json = json.dumps(_steps_payload(run, steps, screenshot_map), ensure_ascii=False, indent=2)

    report_id = f"rep_{uuid.uuid4().hex[:12]}"
    reports_dir = config.reports_dir()
    path = reports_dir / f"report_{run_id}_{report_id}.zip"
    try:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("index.html", html)
            archive.writestr("junit.xml", junit)
            archive.writestr("steps.json", steps_json)
            for _artifact, source, arcname in screenshot_entries:
                archive.write(source, arcname=arcname)
            for _artifact, source, arcname in video_entries:
                archive.write(source, arcname=arcname)
        size = path.stat().st_size
    except OSError as exc:  # pragma: no cover - 磁盘异常
        path.unlink(missing_ok=True)
        raise ApiError("INTERNAL", "报告写入失败", {"error": str(exc)}) from exc

    max_mb = int(config.get("retention.report_max_mb") or 500)
    max_bytes = max_mb * 1024 * 1024
    if size > max_bytes:
        path.unlink(missing_ok=True)
        raise ApiError(
            "INVALID_ARGUMENT",
            f"报告超过 {max_mb} MB 上限",
            {"size": size, "max_bytes": max_bytes, "report_max_mb": max_mb},
        )

    with session_scope() as db:
        db.add(
            Report(
                id=report_id,
                run_id=run_id,
                status="ready",
                path=str(path),
                size=size,
                summary=summary,
                created_at=generated_at,
            )
        )
    logger.info("report built: run=%s id=%s size=%s path=%s", run_id, report_id, size, path)
    return {
        "report_id": report_id,
        "path": str(path),
        "size": size,
        "url": f"/api/reports/{report_id}/download",
        "summary": summary,
    }
