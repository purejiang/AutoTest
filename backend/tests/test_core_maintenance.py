"""维护统计与清理（契约 §7.5 maintenance 口径，本模块归 be-core）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import tests.test_core_support as support
from app import config, db, models

core_client = support.core_client


def _old_iso(days: int = 10) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="milliseconds")


def _insert_report(run_id: int, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PK" + b"r" * 64)
    with db.session_scope() as session:
        session.add(
            models.Report(
                id=f"rep-{run_id}",
                run_id=run_id,
                status="ready",
                path=str(path),
                size=path.stat().st_size,
            )
        )


def _insert_artifact(run_id: int, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 128)
    with db.session_scope() as session:
        session.add(
            models.Artifact(
                run_id=run_id,
                kind="screenshot",
                name=path.name,
                path=str(path),
                size=path.stat().st_size,
            )
        )
        session.add(
            models.StepResult(
                run_id=run_id, step_id="s1", step_name="step", step_type="click", status="passed"
            )
        )
        session.add(models.RunEvent(run_id=run_id, seq=1, type="run_started", payload={}))


def test_stats_shape(core_client) -> None:
    support.insert_run(50, status="succeeded", created_at=_old_iso(1))
    _insert_artifact(50, config.artifacts_dir() / "stats.png")
    response = core_client.get("/api/maintenance/stats")
    assert response.status_code == 200
    body = response.json()
    assert {"db_size", "artifacts_size", "runs", "artifacts", "oldest_run_at", "retention_days"} <= set(body)
    assert body["runs"] == 1
    assert body["artifacts"] == 1
    assert body["artifacts_size"] >= 128
    assert body["retention_days"] == 30
    assert body["oldest_run_at"]


def test_cleanup_dry_run_then_real(core_client) -> None:
    support.insert_run(100, status="succeeded", created_at=_old_iso(10))
    artifact = config.artifacts_dir() / "old.png"
    _insert_artifact(100, artifact)
    report = config.reports_dir() / "old-report.zip"
    _insert_report(100, report)
    expected_freed = 128 + report.stat().st_size

    dry = core_client.post("/api/maintenance/cleanup", json={"retention_days": 1, "dry_run": True})
    assert dry.status_code == 200, dry.text
    dry_body = dry.json()
    assert dry_body["dry_run"] is True
    assert dry_body["runs_matched"] == 1
    assert dry_body["runs_deleted"] == 0
    assert dry_body["artifacts_deleted"] == 0
    assert dry_body["reports_matched"] == 1
    assert dry_body["reports_deleted"] == 0
    assert dry_body["freed_bytes"] == expected_freed
    assert artifact.exists()
    assert report.exists()

    real = core_client.post("/api/maintenance/cleanup", json={"retention_days": 1})
    assert real.status_code == 200, real.text
    body = real.json()
    assert body["dry_run"] is False
    assert body["runs_deleted"] == 1
    assert body["artifacts_deleted"] == 1
    assert body["reports_deleted"] == 1
    assert body["freed_bytes"] == expected_freed
    assert not artifact.exists()
    assert not report.exists()

    stats = core_client.get("/api/maintenance/stats").json()
    assert stats["runs"] == 0
    assert stats["artifacts"] == 0


def test_cleanup_keeps_recent_and_active_runs(core_client) -> None:
    support.insert_run(200, status="succeeded", created_at=_old_iso(1))  # 未过期
    support.insert_run(201, status="running", created_at=_old_iso(30))  # 活跃
    response = core_client.post("/api/maintenance/cleanup", json={"retention_days": 7})
    assert response.status_code == 200
    body = response.json()
    assert body["runs_matched"] == 0
    assert body["runs_deleted"] == 0
    stats = core_client.get("/api/maintenance/stats").json()
    assert stats["runs"] == 2


def test_cleanup_never_deletes_outside_data_dir(core_client, tmp_path) -> None:
    support.insert_run(300, status="failed", created_at=_old_iso(10))
    outside = tmp_path / "outside.bin"
    _insert_artifact(300, outside)
    outside_report = tmp_path / "outside-report.zip"
    _insert_report(300, outside_report)
    response = core_client.post("/api/maintenance/cleanup", json={"retention_days": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["runs_deleted"] == 1
    assert body["files_deleted"] == 0
    assert body["freed_bytes"] == 0
    assert outside.exists(), "data_dir 之外的文件绝不能被清理"
    assert outside_report.exists(), "data_dir 之外的报告文件绝不能被清理"


def test_cleanup_uses_configuration_retention_when_omitted(core_client) -> None:
    support.insert_run(400, status="succeeded", created_at=_old_iso(40))
    response = core_client.post("/api/maintenance/cleanup")
    assert response.status_code == 200
    body = response.json()
    assert body["retention_days"] == config.get("retention.days")
    assert body["runs_deleted"] == 1


def test_cleanup_request_validation(core_client) -> None:
    response = core_client.post("/api/maintenance/cleanup", json={"retention_days": 0})
    assert response.status_code == 422
    assert json.loads(response.text)["error"]["code"] == "VALIDATION_ERROR"
