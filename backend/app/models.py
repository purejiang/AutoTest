"""ORM 模型（契约 §2 一一对应）。时间统一 ISO8601 UTC 文本。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Script(Base):
    __tablename__ = "scripts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # v0.2 四值：manual | mirror | physical | imported（旧 source=recorded 不再支持）
    source: Mapped[str] = mapped_column(String(20), default="manual")
    # 软关联来源录制（无 FK；录制删除后保留该字段，便于溯源）
    origin_recording_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_status", "status"), Index("ix_runs_script", "script_id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    script_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    script_name: Mapped[str] = mapped_column(String(200), default="")
    script_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    device_serial: Mapped[str] = mapped_column(String(120), default="")
    variables: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    debug: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ended_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    current_step_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    step_index: Mapped[int] = mapped_column(Integer, default=0)
    total_steps: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class StepResult(Base):
    __tablename__ = "step_results"
    __table_args__ = (Index("ix_step_results_run", "run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    step_id: Mapped[str] = mapped_column(String(64), default="")
    step_name: Mapped[str] = mapped_column(String(200), default="")
    step_type: Mapped[str] = mapped_column(String(30), default="")
    step_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="passed")
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ended_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_run_events_run_seq"),
        Index("ix_run_events_run", "run_id"),
    )

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_run", "run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=True)
    step_result_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(30), default="screenshot")
    name: Mapped[str] = mapped_column(String(200), default="")
    path: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    mode: Mapped[str] = mapped_column(String(20), default="web")  # web | physical
    serial: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(20), default="recording")
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ended_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    script_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class RecordingEvent(Base):
    __tablename__ = "recording_events"
    __table_args__ = (
        UniqueConstraint("recording_id", "seq", name="uq_recording_events_seq"),
        Index("ix_recording_events_rec", "recording_id"),
    )

    recording_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[str] = mapped_column(String(10), default="high")
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (Index("ix_reports_run", "run_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ready")
    path: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[str] = mapped_column(String(40), default=now_iso)
