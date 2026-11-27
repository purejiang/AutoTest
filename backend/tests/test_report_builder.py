"""报告生成 + 报告 REST：zip 内容、junit、体积上限、下载 Range。"""

from __future__ import annotations

import json
import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config
from app.android.driver import FakeDriver
from app.api import reports as reports_api
from app.db import session_scope
from app.errors import ApiError, install_error_handlers
from app.models import Artifact, Report, Run, StepResult, now_iso
from app.report.builder import build_report

RUN_ID = 11


@pytest.fixture()
def report_env(tmp_path, temp_db):
    config.reset_to_defaults()
    data_dir = tmp_path / "data"
    config.set_file_layer({"runtime.data_dir": str(data_dir)})
    yield data_dir
    config.reset_to_defaults()


@pytest.fixture()
def report_app(report_env):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(reports_api.router)
    return TestClient(app)


def _seed_run(
    tmp_path: Path,
    *,
    run_id: int = RUN_ID,
    failed_screenshot: bool = True,
    video_bytes: int = 0,
) -> dict[str, Any]:
    screenshot_path = tmp_path / "failed.png"
    screenshot_path.write_bytes(FakeDriver().screenshot())
    video_path = tmp_path / "screen.mp4"
    if video_bytes:
        video_path.write_bytes(os.urandom(video_bytes))
    with session_scope() as db:
        db.add(
            Run(
                id=run_id,
                script_id="sc1",
                script_name="登录测试",
                device_serial="fake-1",
                status="failed",
                started_at=now_iso(),
                ended_at=now_iso(),
                total_steps=3,
                summary={"duration_ms": 1234, "passed_steps": 1, "failed_steps": 1, "skipped_steps": 1},
            )
        )
        db.add(
            StepResult(
                run_id=run_id,
                step_id="s1",
                step_name="打开应用",
                step_type="app",
                step_index=1,
                status="passed",
                duration_ms=100,
                attempts=1,
            )
        )
        db.add(
            StepResult(
                run_id=run_id,
                step_id="s2",
                step_name="点击登录",
                step_type="click",
                step_index=2,
                status="failed",
                duration_ms=800,
                attempts=2,
                error={"code": "TARGET_NOT_FOUND", "message": "找不到登录按钮", "traceback": "Traceback: x"},
            )
        )
        db.add(
            StepResult(
                run_id=run_id,
                step_id="s3",
                step_name="输入账号",
                step_type="input",
                step_index=3,
                status="skipped",
                duration_ms=0,
            )
        )
        db.flush()
        failed_step = db.scalars(
            select(StepResult).where(StepResult.run_id == run_id, StepResult.step_id == "s2")
        ).first()
        assert failed_step is not None
        if failed_screenshot:
            db.add(
                Artifact(
                    run_id=run_id,
                    step_result_id=failed_step.id,
                    kind="screenshot",
                    name="失败截图",
                    path=str(screenshot_path),
                    size=screenshot_path.stat().st_size,
                )
            )
        if video_bytes:
            db.add(
                Artifact(
                    run_id=run_id,
                    step_result_id=None,
                    kind="video",
                    name="运行录屏",
                    path=str(video_path),
                    size=video_path.stat().st_size,
                )
            )
    return {"run_id": run_id, "screenshot": screenshot_path, "video": video_path}


# ---------------------------------------------------------------- builder
def test_build_report_zip_contents_and_db_row(report_env, tmp_path) -> None:
    seed = _seed_run(tmp_path)
    result = build_report(seed["run_id"])

    path = Path(result["path"])
    assert path.is_file()
    assert path.parent == config.reports_dir()
    assert result["report_id"].startswith("rep_")
    assert result["size"] == path.stat().st_size > 0
    assert result["url"] == f"/api/reports/{result['report_id']}/download"

    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert "index.html" in names
        assert "junit.xml" in names
        assert "steps.json" in names
        assert any(name.startswith("screenshots/") for name in names)
        html = archive.read("index.html").decode("utf-8")
        assert "登录测试" in html
        assert "点击登录" in html
        assert "找不到登录按钮" in html
        assert "screenshots/" in html  # 失败截图缩略图
        junit = archive.read("junit.xml").decode("utf-8")
        assert junit.count("<testcase") == 3
        assert "<failure" in junit
        assert "<skipped" in junit
        steps = json.loads(archive.read("steps.json").decode("utf-8"))
        assert [item["step_id"] for item in steps] == ["s1", "s2", "s3"]
        assert steps[1]["status"] == "failed"
        assert steps[1]["screenshots"]

    with session_scope() as db:
        report = db.get(Report, result["report_id"])
        assert report is not None
        assert report.run_id == seed["run_id"]
        assert report.status == "ready"
        assert report.size == result["size"]
        assert report.summary["passed_steps"] == 1
        assert report.summary["failed_steps"] == 1
        assert report.summary["skipped_steps"] == 1
        assert report.summary["duration_ms"] == 1234


def test_build_report_includes_step_logcat_and_domains(report_env, tmp_path) -> None:
    seed = _seed_run(tmp_path)
    log_path = tmp_path / "step2.log"
    log_path.write_text("06-12 10:00:00.000 I/App: before\n06-12 10:00:01.000 E/App: FATAL EXCEPTION\n", encoding="utf-8")
    domains_path = tmp_path / "step2.jsonl"
    domains_path.write_text(
        json.dumps(
            {"ts": "2026-06-12T10:00:01+00:00", "host": "api.example.com", "port": 443, "scheme": "https"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    run_log_path = tmp_path / "run.log"
    run_log_path.write_text("06-12 09:59:00.000 I/App: run start\n", encoding="utf-8")
    with session_scope() as db:
        failed = db.scalars(
            select(StepResult).where(StepResult.run_id == seed["run_id"], StepResult.step_id == "s2")
        ).one()
        db.add(
            Artifact(
                run_id=seed["run_id"],
                step_result_id=failed.id,
                kind="logcat",
                name="s2-fail-logcat",
                path=str(log_path),
                size=log_path.stat().st_size,
                created_at=now_iso(),
            )
        )
        db.add(
            Artifact(
                run_id=seed["run_id"],
                step_result_id=failed.id,
                kind="network",
                name="s2-fail-domains",
                path=str(domains_path),
                size=domains_path.stat().st_size,
                created_at=now_iso(),
            )
        )
        db.add(
            Artifact(
                run_id=seed["run_id"],
                step_result_id=None,
                kind="logcat",
                name="run-logcat",
                path=str(run_log_path),
                size=run_log_path.stat().st_size,
                created_at=now_iso(),
            )
        )

    result = build_report(seed["run_id"])
    with zipfile.ZipFile(Path(result["path"])) as archive:
        names = archive.namelist()
        evidence = [name for name in names if name.startswith("evidence/")]
        assert any(name.endswith(".log") for name in evidence)
        assert any(name.endswith(".jsonl") for name in evidence)
        assert any(name.startswith("logs/") and name.endswith(".log") for name in names)
        html = archive.read("index.html").decode("utf-8")
        assert "FATAL EXCEPTION" in html
        assert "api.example.com" in html
        assert "失败现场" in html
        junit = archive.read("junit.xml").decode("utf-8")
        assert "<system-out>" in junit
        assert "FATAL EXCEPTION" in junit
        steps = json.loads(archive.read("steps.json").decode("utf-8"))
        failed_step = next(item for item in steps if item["step_id"] == "s2")
        assert {item["kind"] for item in failed_step["evidence"]} == {"logcat", "network"}
        assert failed_step["evidence"][0]["preview"]


def test_build_report_timeline_groups_run_logcat_by_step(report_env, tmp_path) -> None:
    seed = _seed_run(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)

    def iso(value: datetime) -> str:
        return value.astimezone(UTC).isoformat(timespec="milliseconds")

    def log_line(value: datetime, message: str) -> str:
        local = value.astimezone()
        return f"{local.strftime('%m-%d %H:%M:%S')}.{local.microsecond // 1000:03d}  1  2 I App: {message}"

    log_path = tmp_path / "run-timeline.log"
    log_path.write_text(
        "\n".join(
            [
                log_line(now + timedelta(seconds=1), "step one"),
                log_line(now + timedelta(seconds=3), "step two"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with session_scope() as db:
        run = db.get(Run, seed["run_id"])
        assert run is not None
        run.started_at = iso(now)
        run.ended_at = iso(now + timedelta(seconds=5))
        steps = list(
            db.scalars(select(StepResult).where(StepResult.run_id == seed["run_id"]).order_by(StepResult.step_index))
        )
        steps[0].started_at = iso(now)
        steps[0].ended_at = iso(now + timedelta(seconds=2))
        steps[1].started_at = iso(now + timedelta(seconds=2))
        steps[1].ended_at = iso(now + timedelta(seconds=4))
        steps[2].started_at = iso(now + timedelta(seconds=4))
        steps[2].ended_at = iso(now + timedelta(seconds=5))
        db.add(
            Artifact(
                run_id=seed["run_id"],
                step_result_id=None,
                kind="logcat",
                name="run-logcat",
                path=str(log_path),
                size=log_path.stat().st_size,
                created_at=now_iso(),
            )
        )

    result = build_report(seed["run_id"])
    with zipfile.ZipFile(Path(result["path"])) as archive:
        html = archive.read("index.html").decode("utf-8")
        assert "节点时间线" in html
        assert "合并时间线" in html
        assert "step one" in html
        assert "step two" in html
        steps = json.loads(archive.read("steps.json").decode("utf-8"))
        assert steps[0]["timeline"]["logcat"][0]["text"] == "step one"
        assert steps[1]["timeline"]["logcat"][0]["text"] == "step two"


def test_build_report_video_excluded_by_default_but_includable(report_env, tmp_path) -> None:
    seed = _seed_run(tmp_path, video_bytes=2048)
    excluded = build_report(seed["run_id"], include_video=False)
    with zipfile.ZipFile(excluded["path"]) as archive:
        assert not any(name.startswith("videos/") for name in archive.namelist())

    included = build_report(seed["run_id"], include_video=True)
    with zipfile.ZipFile(included["path"]) as archive:
        names = archive.namelist()
        assert any(name.startswith("videos/") for name in names)
        assert "index.html" in names


def test_build_report_size_limit_deletes_file_and_raises(report_env, tmp_path) -> None:
    seed = _seed_run(tmp_path, run_id=12, failed_screenshot=False, video_bytes=2 * 1024 * 1024)
    config.set_file_layer(
        {"runtime.data_dir": str(config.get("runtime.data_dir")), "retention.report_max_mb": 1}
    )
    with pytest.raises(ApiError) as excinfo:
        build_report(seed["run_id"], include_video=True)
    error = excinfo.value
    assert error.code == "INVALID_ARGUMENT"
    assert "上限" in error.message
    assert list(config.reports_dir().glob("*.zip")) == []
    with session_scope() as db:
        assert db.scalars(select(Report).where(Report.run_id == seed["run_id"])).first() is None


def test_build_report_missing_run_raises_not_found(report_env) -> None:
    with pytest.raises(ApiError) as excinfo:
        build_report(999)
    assert excinfo.value.code == "NOT_FOUND"


def test_build_report_without_steps(report_env) -> None:
    with session_scope() as db:
        db.add(Run(id=13, script_name="空运行", device_serial="fake-1", status="succeeded", total_steps=0))
    result = build_report(13)
    with zipfile.ZipFile(result["path"]) as archive:
        junit = archive.read("junit.xml").decode("utf-8")
        assert 'tests="0"' in junit
        assert "index.html" in archive.namelist()


# ---------------------------------------------------------------- REST
def test_report_api_create_list_download_range_delete(report_app, report_env, tmp_path) -> None:
    seed = _seed_run(tmp_path)
    response = report_app.post(f"/api/runs/{seed['run_id']}/report")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["run_id"] == seed["run_id"]
    assert payload["status"] == "ready"
    assert payload["url"] == f"/api/reports/{payload['id']}/download"
    assert payload["summary"]["total_steps"] == 3

    listing = report_app.get(f"/api/reports?run_id={seed['run_id']}").json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == payload["id"]

    download = report_app.get(payload["url"])
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    assert download.content[:2] == b"PK"
    path = Path(payload["path"])
    assert download.content == path.read_bytes()

    partial = report_app.get(payload["url"], headers={"Range": "bytes=0-9"})
    assert partial.status_code == 206
    assert len(partial.content) == 10
    assert partial.headers["content-range"].startswith("bytes 0-9/")

    assert report_app.delete(f"/api/reports/{payload['id']}").json() == {
        "deleted": True,
        "id": payload["id"],
    }
    assert not path.exists()
    assert report_app.get(payload["url"]).status_code == 404


def test_report_api_include_video_flag(report_app, report_env, tmp_path) -> None:
    seed = _seed_run(tmp_path, run_id=14, video_bytes=2048)
    response = report_app.post(f"/api/runs/{seed['run_id']}/report", json={"include_video": True})
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(response.json()["path"]) as archive:
        assert any(name.startswith("videos/") for name in archive.namelist())


def test_report_api_missing_run(report_app, report_env) -> None:
    response = report_app.post("/api/runs/999/report")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
