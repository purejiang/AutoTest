"""构建元信息 + 前端目录覆盖（热更）测试：能确认"现在跑的是哪份代码"。"""

from __future__ import annotations

import pytest

from app import build_info as build_info_module
from app import main as main_module


@pytest.fixture(autouse=True)
def _clear_cache():
    build_info_module.build_info.cache_clear()
    yield
    build_info_module.build_info.cache_clear()


def test_build_info_from_env(monkeypatch):
    monkeypatch.setenv("AUTOTEST_GIT_COMMIT", "abc1234")
    monkeypatch.setenv("AUTOTEST_GIT_DIRTY", "1")
    monkeypatch.setenv("AUTOTEST_BUILD_TIME", "2026-01-01T00:00:00Z")
    info = build_info_module.build_info()
    assert info == {"commit": "abc1234", "dirty": True, "built_at": "2026-01-01T00:00:00Z"}


def test_build_info_dirty_env_false(monkeypatch):
    monkeypatch.setenv("AUTOTEST_GIT_COMMIT", "abc1234")
    monkeypatch.setenv("AUTOTEST_GIT_DIRTY", "false")
    assert build_info_module.build_info()["dirty"] is False


def test_health_includes_build(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app import config, db

    monkeypatch.setenv("AUTOTEST_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AUTOTEST_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AUTOTEST_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("AUTOTEST_GIT_COMMIT", "deadbee")
    config.reset_to_defaults()
    config.ensure_dirs()
    db.reset_engine_for_tests(tmp_path / "health.db")

    from app.main import app

    with TestClient(app) as client:
        payload = client.get("/api/health").json()
    assert payload["version"]
    assert payload["build"]["commit"] == "deadbee"
    config.reset_to_defaults()


def test_frontend_dir_override(monkeypatch, tmp_path):
    """指向含 index.html 的目录 → 用它（热更）；否则回落内置 frontend/dist。"""
    hotfix = tmp_path / "hotfix-dist"
    hotfix.mkdir()
    (hotfix / "index.html").write_text("<html>hotfix</html>", encoding="utf-8")
    monkeypatch.setenv("AUTOTEST_FRONTEND_DIR", str(hotfix))
    assert main_module.frontend_dist_dir() == hotfix

    empty = tmp_path / "empty-dist"
    empty.mkdir()
    monkeypatch.setenv("AUTOTEST_FRONTEND_DIR", str(empty))
    # 没有 index.html → 回落内置目录（不抛错，也不静默用坏目录）
    assert main_module.frontend_dist_dir() == main_module.FRONTEND_DIST

    monkeypatch.delenv("AUTOTEST_FRONTEND_DIR", raising=False)
    assert main_module.frontend_dist_dir() == main_module.FRONTEND_DIST
