"""be-core 测试公共夹具（被 test_core_*.py 导入；不依赖真机）。

每个用例：
- 独立 SQLite 文件（`db.reset_engine_for_tests(tmp_path/"test.db")`）；
- 独立 data_dir（文件层配置，通过 AUTOTEST_CONFIG 注入，测试 file<db 优先级）；
- 清空 device_registry 占用表 / fake 单例 / 录制挂钩注册表；
- 用 TestClient 触发 lifespan（建表 + 载入 DB settings）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import config, db, models
from app.api import devices as devices_api
from app.recorder import hooks
from app.services import device_registry

BASE_FILE_LAYER: dict[str, Any] = {
    "runtime": {"data_dir": ""},  # 由夹具填成 tmp_path/data
    "engine": {"poll_interval_ms": 300},  # 文件层值（DB 层覆盖测试用）
    "recording": {"click_dump": True},
}


class DummyRecorder:
    """实现 recorder.hooks.RecorderSessionLike 的假录制会话（断言挂钩行为）。"""

    def __init__(self, recording_id: str = "rec-1") -> None:
        self.recording_id = recording_id
        self.actions: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []
        self.notes: list[tuple[str, dict[str, Any]]] = []
        self.before_pointer_calls = 0

    def before_pointer(self) -> dict[str, Any] | None:
        self.before_pointer_calls += 1
        return {"class_name": "fake-root", "children": []}

    def handle(self, kind: str, payload: dict[str, Any], pre: dict[str, Any] | None) -> None:
        self.actions.append((kind, payload, pre))

    def note_manual(self, kind: str, payload: dict[str, Any]) -> None:
        self.notes.append((kind, payload))


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def write_config_file(tmp_path: Path, extra: dict[str, Any] | None = None) -> Path:
    layer = json.loads(json.dumps(BASE_FILE_LAYER))
    layer["runtime"]["data_dir"] = str(tmp_path / "data")
    if extra:
        _deep_merge(layer, extra)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(layer), encoding="utf-8")
    return path


def _reset_device_state() -> None:
    device_registry.reset_for_tests()
    for serial in hooks.active_serials():
        hooks.unregister(serial)
    devices_api._SCREEN_RECORDERS.clear()


@pytest.fixture()
def core_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    config.reset_to_defaults()
    monkeypatch.setenv("AUTOTEST_CONFIG", str(write_config_file(tmp_path)))
    db.reset_engine_for_tests(tmp_path / "test.db")
    _reset_device_state()
    from app import main  # 延迟 import：确保每次夹具都用最新模块状态

    application = main.create_app()
    with TestClient(application) as client:
        yield client
    _reset_device_state()
    config.reset_to_defaults()


def fake_driver(serial: str = "fake-1") -> Any:
    """取注册表里的 fake 单例（与 API 共用同一实例，可断言调用序列）。"""
    return device_registry.get_driver(serial)


def insert_run(run_id: int, status: str = "running", created_at: str | None = None) -> None:
    with db.session_scope() as session:
        session.add(
            models.Run(
                id=run_id,
                script_name="test-run",
                device_serial="fake-1",
                status=status,
                created_at=created_at or models.now_iso(),
            )
        )


def register_recorder(serial: str = "fake-1", recording_id: str = "rec-1") -> DummyRecorder:
    recorder = DummyRecorder(recording_id=recording_id)
    hooks.register(serial, recorder)
    return recorder


def empty_script(name: str = "未命名脚本") -> dict[str, Any]:
    from app import schemas

    return schemas.empty_script(name)
