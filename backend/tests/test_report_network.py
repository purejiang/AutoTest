"""网络抓包（可选能力）：未启用一律 UNSUPPORTED，启用后最小生命周期。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app import config
from app.capture import network
from app.db import session_scope
from app.errors import ApiError
from app.models import Artifact


@pytest.fixture()
def net_env(tmp_path, temp_db):
    config.reset_to_defaults()
    data_dir = tmp_path / "data"
    config.set_db_overrides({"runtime.data_dir": str(data_dir)})
    yield tmp_path
    network.shutdown_all()
    network._ACTIVE.clear()
    config.reset_to_defaults()


def _enable(tmp_path) -> None:
    config.set_db_overrides(
        {
            "runtime.data_dir": str(tmp_path / "data"),
            "capture.network.enabled": True,
        }
    )


def test_disabled_by_default_raises_unsupported(net_env) -> None:
    assert network.available() is False
    for action in (
        lambda: network.start("fake-1"),
        lambda: network.stop("fake-1"),
        lambda: network.status("fake-1"),
    ):
        with pytest.raises(ApiError) as excinfo:
            action()
        assert excinfo.value.code == "UNSUPPORTED"
        assert "网络抓包未启用" in excinfo.value.message


def test_enabled_but_missing_mitmproxy_raises_unsupported(net_env, monkeypatch) -> None:
    _enable(net_env)
    monkeypatch.setattr(network, "_mitmproxy_available", lambda: False)
    assert network.available() is False
    with pytest.raises(ApiError) as excinfo:
        network.start("fake-1")
    assert excinfo.value.code == "UNSUPPORTED"
    assert "mitmproxy" in excinfo.value.message


def test_enabled_capture_lifecycle_writes_artifact(net_env, monkeypatch) -> None:
    _enable(net_env)
    monkeypatch.setattr(network, "_mitmproxy_available", lambda: True)
    assert network.available() is True

    started = network.start("fake-net", run_id=None)
    assert started["status"] == "running"
    assert started["active"] is True
    assert started["path"] and started["path"].endswith(".mitm")
    assert network.status("fake-net")["active"] is True
    # 重复 start 幂等
    assert network.start("fake-net")["path"] == started["path"]

    stopped = network.stop("fake-net")
    assert stopped["status"] == "stopped"
    assert stopped["active"] is False
    assert network.status("fake-net")["active"] is False
    path = stopped["path"]
    assert path is not None
    assert (config.artifacts_dir() / "network").is_dir()

    with session_scope() as db:
        artifacts = list(db.scalars(select(Artifact).where(Artifact.kind == "network")))
        assert len(artifacts) == 1
        assert artifacts[0].path == path
        assert artifacts[0].size > 0

    with pytest.raises(ApiError) as excinfo:
        network.stop("fake-net")
    assert excinfo.value.code == "NOT_FOUND"
