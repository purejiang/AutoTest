"""v0.2 数据库 schema guard：旧库明确报错，绝不自动改/删数据。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

from app import db


def _make_v01_database(path) -> None:
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE scripts ("
                "id VARCHAR(64) PRIMARY KEY, "
                "name VARCHAR(200) NOT NULL, "
                "notes TEXT, "
                "schema_version INTEGER, "
                "data JSON NOT NULL, "
                "source VARCHAR(20), "
                "created_at VARCHAR(40), "
                "updated_at VARCHAR(40))"
            )
        )
        conn.execute(
            text(
                "INSERT INTO scripts (id, name, notes, schema_version, data, source) "
                "VALUES ('old', '旧脚本', '', 1, '{}', 'recorded')"
            )
        )
    engine.dispose()


def test_old_database_without_origin_column_is_rejected_with_actionable_message(tmp_path) -> None:
    old_path = tmp_path / "old.db"
    _make_v01_database(old_path)

    try:
        with pytest.raises(db.IncompatibleDatabaseError, match="origin_recording_id") as excinfo:
            db.reset_engine_for_tests(old_path)

        message = str(excinfo.value)
        assert "v0.2" in message
        assert "备份" in message
        assert "~/.autotest/data" in message
        assert "移走或删除" in message
        # 绝不自动删除旧数据：文件与旧行仍在
        assert old_path.exists()
        check = create_engine(f"sqlite:///{old_path}")
        with check.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM scripts")).scalar() == 1
        check.dispose()
    finally:
        # 恢复干净的测试引擎，避免污染后续用例
        db.reset_engine_for_tests(tmp_path / "fresh.db")


def test_new_database_has_origin_recording_column(tmp_path) -> None:
    db.reset_engine_for_tests(tmp_path / "new.db")
    columns = {column["name"] for column in inspect(db.get_engine()).get_columns("scripts")}
    assert "origin_recording_id" in columns
