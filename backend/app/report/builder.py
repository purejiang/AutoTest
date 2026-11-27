"""运行报告生成（契约 §7.5 / §14.5）。

产物为 zip：`index.html`（汇总 + 步骤表 + 节点时间线/合并时间线）、`junit.xml`
（每 step 一个 testcase，整体一个 testsuite；节点证据写 `<system-out>`）、`steps.json`、
截图 / 视频、`evidence/`（失败节点的 logcat 与域名清单）、`logs/` + `network/`（运行级采集）。
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
from ..models import Artifact, Report, Run, RunEvent, StepResult, now_iso
from .timeline import build_timeline

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(default_for_string=True, default=True),
    trim_blocks=True,
    lstrip_blocks=True,
)

_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff_.-]+")
_VIDEO_KINDS = {"video", "screenrecord"}
_EVIDENCE_KINDS = {"logcat", "network"}
_FAILED_STATUSES = {"failed", "error"}
_PREVIEW_LOG_LINES = 80
_PREVIEW_NETWORK_RECORDS = 50
# 报告里每个节点默认只放末尾固定行数；完整文件仍在 zip 内。
_STEP_LOG_PREVIEW_LINES = 80
_STEP_NETWORK_PREVIEW_RECORDS = 50


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


def _sub_script_meta(step: StepResult) -> dict[str, Any] | None:
    output = step.output if isinstance(step.output, dict) else {}
    meta = output.get("sub_script")
    if not isinstance(meta, dict):
        return None
    if not meta.get("parent_step_id"):
        return None
    return meta


def _ordered_steps(steps: list[StepResult]) -> list[tuple[StepResult, int, str | None]]:
    """父节点在前，子脚本内部节点紧跟父节点；旧运行无元数据时保持原顺序。"""
    by_parent: dict[str, list[StepResult]] = {}
    roots: list[StepResult] = []
    for step in steps:
        meta = _sub_script_meta(step)
        if meta is not None:
            by_parent.setdefault(str(meta["parent_step_id"]), []).append(step)
        else:
            roots.append(step)
    ordered: list[tuple[StepResult, int, str | None]] = []
    seen: set[int] = set()

    def visit(step: StepResult, depth: int, script_name: str | None) -> None:
        if int(step.id) in seen:
            return
        seen.add(int(step.id))
        ordered.append((step, depth, script_name))
        meta = _sub_script_meta(step)
        for child in by_parent.get(step.step_id, []):
            child_meta = _sub_script_meta(child) or {}
            visit(child, depth + 1, str(child_meta.get("script_name") or script_name or "") or None)

    for step in roots:
        visit(step, 0, None)
    for step in steps:
        if int(step.id) not in seen:
            meta = _sub_script_meta(step) or {}
            visit(step, int(meta.get("depth") or 1), str(meta.get("script_name") or "") or None)
    return ordered


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


def _display_time(value: str | None) -> str:
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _run_view(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "script_id": run.script_id,
        "script_name": run.script_name,
        "device_serial": run.device_serial,
        "status": run.status,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "started_at_local": _display_time(run.started_at),
        "ended_at_local": _display_time(run.ended_at),
        "error": _error_message(run.error),
        "summary": run.summary,
        "total_steps": run.total_steps,
        "created_at": run.created_at,
    }


def _read_logcat(path: Path) -> tuple[list[str], int]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:  # pragma: no cover - 读取失败不拖垮报告
        return [], 0
    return lines[-_PREVIEW_LOG_LINES:], len(lines)


def _read_network(path: Path) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append(item)
    except OSError:  # pragma: no cover
        return [], 0
    return records[-_PREVIEW_NETWORK_RECORDS:], len(records)


def _evidence_view(artifact: Artifact, source: Path, arcname: str) -> dict[str, Any]:
    """把 logcat / 域名抓包转成报告里可预览、可下载的证据条目。"""
    entry: dict[str, Any] = {
        "kind": artifact.kind,
        "name": artifact.name or source.name,
        "file": arcname,
        "size": int(artifact.size or 0),
    }
    if artifact.kind == "logcat":
        preview, total = _read_logcat(source)
        entry["lines"] = total
        entry["preview"] = preview
    else:
        preview, total = _read_network(source)
        entry["records"] = total
        entry["preview"] = preview
    return entry


# ================================================================ junit.xml
def _junit_xml(
    run: Run,
    steps: list[StepResult],
    summary: dict[str, Any],
    evidence_map: dict[int, list[dict[str, Any]]] | None = None,
) -> bytes:
    evidence_map = evidence_map or {}
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
        evidence = evidence_map.get(step.id)
        if evidence:
            lines: list[str] = []
            for item in evidence:
                if item["kind"] == "logcat":
                    lines.append(f"--- logcat（共 {item.get('lines', 0)} 行，末尾预览）---")
                    lines.extend(str(line) for line in item.get("preview", []))
                else:
                    lines.append(f"--- network domains（共 {item.get('records', 0)} 条，末尾预览）---")
                    for record in item.get("preview", []):
                        lines.append(
                            f"{record.get('ts', '')} {record.get('host', '')}:"
                            f"{record.get('port', '')} {record.get('scheme', '')}".strip()
                        )
            if lines:
                ET.SubElement(case, "system-out").text = "\n".join(lines)
    ET.indent(suite, space="  ")
    return ET.tostring(suite, encoding="utf-8", xml_declaration=True)


# ================================================================ steps.json
def _steps_payload(
    run: Run,
    steps: list[StepResult],
    screenshot_map: dict[int, list[str]],
    evidence_map: dict[int, list[dict[str, Any]]],
    timeline_map: dict[int, dict[str, list[dict[str, Any]]]] | None = None,
) -> list[dict[str, Any]]:
    timeline_map = timeline_map or {}
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
                "started_at_local": _display_time(step.started_at),
                "ended_at_local": _display_time(step.ended_at),
                "duration_ms": step.duration_ms,
                "error": step.error,
                "output": step.output,
                "sub_script": (step.output or {}).get("sub_script") if isinstance(step.output, dict) else None,
                "depth": int((((step.output or {}).get("sub_script") or {}).get("depth") or 0) if isinstance(step.output, dict) else 0),
                "screenshots": screenshot_map.get(step.id, []),
                "evidence": evidence_map.get(step.id, []),
                "timeline": {
                    "logcat": timeline_map.get(int(step.id), {}).get("logcat", []),
                    "network": timeline_map.get(int(step.id), {}).get("network", []),
                    "events": timeline_map.get(int(step.id), {}).get("event", []),
                },
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
        run_events = list(
            db.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq))
        )

    duration_ms = _run_duration_ms(run, steps)
    summary = _step_summary(steps, duration_ms)

    step_by_result_id = {int(step.id): step for step in steps}
    screenshot_entries: list[tuple[Artifact, Path, str]] = []
    video_entries: list[tuple[Artifact, Path, str]] = []
    evidence_files: dict[int, list[tuple[Path, str]]] = {}
    evidence_map: dict[int, list[dict[str, Any]]] = {}
    run_evidence_files: list[tuple[Path, str]] = []
    run_evidence: list[dict[str, Any]] = []
    timeline_logcat_paths: list[Path] = []
    timeline_network_paths: list[Path] = []
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
        elif artifact.kind in _EVIDENCE_KINDS:
            source = _resolve_artifact_path(artifact)
            if source is None:
                continue
            default_ext = ".log" if artifact.kind == "logcat" else ".jsonl"
            fallback = f"artifact_{artifact.id}"
            filename = (
                f"{_safe_filename(artifact.name, fallback)}_{artifact.id}"
                f"{_artifact_extension(source, default_ext)}"
            )
            step = (
                step_by_result_id.get(int(artifact.step_result_id))
                if artifact.step_result_id is not None
                else None
            )
            if step is not None:
                arcname = f"evidence/{step.step_index:03d}_{_safe_filename(step.step_id, 'step')}_{filename}"
                evidence_files.setdefault(step.id, []).append((source, arcname))
                evidence_map.setdefault(step.id, []).append(
                    _evidence_view(artifact, source, arcname)
                )
            else:
                folder = "logs" if artifact.kind == "logcat" else "network"
                arcname = f"{folder}/{filename}"
                run_evidence_files.append((source, arcname))
                run_evidence.append(_evidence_view(artifact, source, arcname))
                if artifact.kind == "logcat":
                    timeline_logcat_paths.append(source)
                else:
                    timeline_network_paths.append(source)

    screenshot_map: dict[int, list[str]] = {}
    for artifact, _source, arcname in screenshot_entries:
        if artifact.step_result_id is not None:
            screenshot_map.setdefault(int(artifact.step_result_id), []).append(arcname)

    timeline = build_timeline(
        steps,
        timeline_logcat_paths,
        timeline_network_paths,
        run.started_at,
        run.ended_at,
        run_events,
    )
    timeline_steps = timeline["steps"]

    steps_view: list[dict[str, Any]] = []
    for step, depth, sub_script_name in _ordered_steps(steps):
        step_timeline = timeline_steps.get(int(step.id), {})
        step_logcat = step_timeline.get("logcat", [])
        step_network = step_timeline.get("network", [])
        step_events = step_timeline.get("event", [])
        steps_view.append(
            {
                "index": step.step_index,
                "row_no": len(steps_view) + 1,
                "depth": depth,
                "sub_script_name": sub_script_name,
                "step_id": step.step_id,
                "name": step.step_name or step.step_id,
                "type": step.step_type,
                "status": step.status,
                "started_at": step.started_at,
                "ended_at": step.ended_at,
                "started_at_local": _display_time(step.started_at),
                "ended_at_local": _display_time(step.ended_at),
                "duration_ms": step.duration_ms,
                "attempts": step.attempts,
                "error": _error_message(step.error),
                "output": step.output,
                "sub_script": (step.output or {}).get("sub_script") if isinstance(step.output, dict) else None,
                "screenshots": screenshot_map.get(step.id, []),
                "evidence": evidence_map.get(step.id, []),
                "timeline_logcat": step_logcat[-_STEP_LOG_PREVIEW_LINES:],
                "timeline_logcat_count": len(step_logcat),
                "timeline_network": step_network[-_STEP_NETWORK_PREVIEW_RECORDS:],
                "timeline_network_count": len(step_network),
                "timeline_events": step_events[-50:],
                "timeline_events_count": len(step_events),
                "has_detail": bool(
                    step_logcat
                    or step_network
                    or step_events
                    or screenshot_map.get(step.id)
                    or evidence_map.get(step.id)
                    or step.error
                ),
            }
        )
    generated_at = now_iso()
    run_logcat_file = next(
        (item["file"] for item in run_evidence if item.get("kind") == "logcat"), None
    )
    run_network_file = next(
        (item["file"] for item in run_evidence if item.get("kind") == "network"), None
    )
    network_http_meta = any(
        (item.get("method") or item.get("path") or item.get("status") is not None)
        for step in steps_view
        for item in step.get("timeline_network", [])
    )
    html = _ENV.get_template("report.html").render(
        run=_run_view(run),
        summary=summary,
        steps=steps_view,
        videos=[
            {"name": artifact.name or source.name, "file": arcname}
            for artifact, source, arcname in video_entries
        ],
        generated_at=generated_at,
        generated_at_local=_display_time(generated_at),
        include_video=bool(include_video),
        run_evidence=run_evidence,
        run_logcat_file=run_logcat_file,
        run_network_file=run_network_file,
        network_http_meta=network_http_meta,
        timeline=timeline["timeline"][:500],
        timeline_total=len(timeline["timeline"]),
        timeline_counts=timeline["counts"],
    )
    junit = _junit_xml(run, steps, summary, evidence_map)
    steps_json = json.dumps(
        _steps_payload(run, steps, screenshot_map, evidence_map, timeline_steps),
        ensure_ascii=False,
        indent=2,
    )

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
            for entries in evidence_files.values():
                for source, arcname in entries:
                    archive.write(source, arcname=arcname)
            for source, arcname in run_evidence_files:
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
