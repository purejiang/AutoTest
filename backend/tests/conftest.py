"""共享测试夹具：独立 DB + FakeDriver。"""
from __future__ import annotations

import pytest

from app import db as appdb


@pytest.fixture(autouse=True)
def isolated_diag_dir(tmp_path, monkeypatch):
    """诊断 JSONL 一律写到 tmp，测试绝不污染用户真实数据目录。"""
    from app import diagnostics

    target = tmp_path / "diag"
    target.mkdir(exist_ok=True)
    monkeypatch.setattr(diagnostics, "diag_dir", lambda: target)
    yield target


@pytest.fixture()
def temp_db(tmp_path):
    appdb.reset_engine_for_tests(tmp_path / "test.db")
    yield
    appdb.reset_engine_for_tests  # noqa: B018 - 下一测试自行重置
