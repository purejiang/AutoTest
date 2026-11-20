"""共享测试夹具：独立 DB + FakeDriver。"""
from __future__ import annotations

import pytest

from app import db as appdb


@pytest.fixture()
def temp_db(tmp_path):
    appdb.reset_engine_for_tests(tmp_path / "test.db")
    yield
    appdb.reset_engine_for_tests  # noqa: B018 - 下一测试自行重置
