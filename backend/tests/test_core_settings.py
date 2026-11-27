"""设置文件化：优先级（default < file < env）/ 写入 config.json / 校验 / 旧 DB 迁移。"""

from __future__ import annotations

import json

import tests.test_core_support as support
from app import config, db, models
from app.services import settings_service

core_client = support.core_client


def _config_json() -> dict:
    return json.loads(config.config_file_path().read_text(encoding="utf-8"))


def test_default_display_for_empty_paths(core_client) -> None:
    """空值默认直接给出可读值：数据目录是绝对路径，供前端做占位符。"""
    body = core_client.get("/api/settings").json()
    entries = {entry["key"]: entry for entry in body["schema"]}
    assert entries["runtime.data_dir"]["default_display"] == str(config.home_dir() / "data")
    assert entries["scrcpy.crop"]["default_display"] == "不裁剪"
    assert entries["security.token"]["default_display"] == "不校验"


def test_default_response_shape(core_client) -> None:
    response = core_client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert {"effective", "sources", "schema", "config_file"} <= set(body)
    assert body["sources"]["engine.poll_interval_ms"] == "file"  # 文件层
    assert body["effective"]["engine.poll_interval_ms"] == 300
    assert body["sources"]["engine.default_timeout_ms"] == "default"
    assert body["effective"]["engine.default_timeout_ms"] == 8000
    assert body["sources"]["runtime.data_dir"] == "file"
    schema = {item["key"]: item for item in body["schema"]}
    assert schema["engine.default_timeout_ms"]["default"] == 8000
    assert schema["security.token"]["default"] == ""
    keys = set(schema)
    assert {"adb.path", "engine.poll_interval_ms", "scrcpy.max_size"} <= keys
    assert {"adb.connect_timeout_s", "scrcpy.jar"} <= keys
    assert body["effective"]["adb.connect_timeout_s"] == 20
    assert body["effective"]["scrcpy.jar"] == ""
    assert "db" not in set(body["sources"].values())


def test_default_delay_settings_exposed(core_client) -> None:
    """全局默认停顿：设置页可见、默认 0、有范围校验。"""
    body = core_client.get("/api/settings").json()
    schema = {item["key"]: item for item in body["schema"]}
    for key in ("engine.default_delay_before_ms", "engine.default_delay_after_ms"):
        assert schema[key]["default"] == 0
        assert body["effective"][key] == 0
        assert body["sources"][key] == "default"
    out_of_range = core_client.put("/api/settings", json={"values": {"engine.default_delay_before_ms": -1}})
    assert out_of_range.status_code == 422


def test_default_on_fail_setting(core_client) -> None:
    """设置页可配「新建脚本默认失败策略」，且只接受 abort/continue。"""
    body = core_client.get("/api/settings").json()
    schema = {item["key"]: item for item in body["schema"]}
    entry = schema["engine.default_on_fail"]
    assert entry["type"] == "enum"
    assert entry["default"] == "abort"
    assert [option["value"] for option in entry["options"]] == ["abort", "continue"]
    assert body["effective"]["engine.default_on_fail"] == "abort"
    assert body["sources"]["engine.default_on_fail"] == "default"

    put = core_client.put("/api/settings", json={"values": {"engine.default_on_fail": "continue"}})
    assert put.status_code == 200, put.text
    assert put.json()["effective"]["engine.default_on_fail"] == "continue"
    assert put.json()["sources"]["engine.default_on_fail"] == "file"

    bad = core_client.put("/api/settings", json={"values": {"engine.default_on_fail": "retry"}})
    assert bad.status_code == 422
    assert "可选值" in bad.text


def test_network_enabled_migrates_old_switches(core_client) -> None:
    """旧版 DB 开关（enabled/auto_attach）迁移成单一的 capture.network.enabled。"""
    with db.session_scope() as session:
        session.add(models.Setting(key="capture.network.enabled", value=True))
        session.add(models.Setting(key="capture.network.auto_attach", value=True))
    migrated = settings_service.migrate_db_settings_to_file()

    assert migrated["capture.network.enabled"] is True
    body = core_client.get("/api/settings").json()
    assert body["effective"]["capture.network.enabled"] is True
    assert body["sources"]["capture.network.enabled"] == "file"
    assert _config_json()["capture"]["network"]["enabled"] is True
    with db.session_scope() as session:
        assert session.get(models.Setting, "capture.network.auto_attach") is None


def test_network_legacy_mode_migrates_to_enabled(core_client) -> None:
    """旧的三态 mode 行也收敛成 bool；off 以外都按开启处理。"""
    with db.session_scope() as session:
        session.add(models.Setting(key="capture.network.mode", value="manual"))
    migrated = settings_service.migrate_db_settings_to_file()

    assert migrated["capture.network.enabled"] is True
    with db.session_scope() as session:
        assert session.get(models.Setting, "capture.network.mode") is None


def test_legacy_network_mode_in_config_file_maps_to_enabled(tmp_path) -> None:
    """配置文件里的旧 mode 在读取时映射成新 bool，不要求用户手改文件。"""
    path = tmp_path / "config.json"
    path.write_text('{"capture": {"network": {"mode": "manual"}}}', encoding="utf-8")
    layer = config.load_file_layer(path)
    assert layer["capture.network.enabled"] is True
    assert "capture.network.mode" not in layer


def test_new_toolchain_keys_are_editable(core_client) -> None:
    response = core_client.put(
        "/api/settings",
        json={"values": {"adb.connect_timeout_s": 45, "scrcpy.jar": "/tmp/scrcpy-server.jar"}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["effective"]["adb.connect_timeout_s"] == 45
    assert body["effective"]["scrcpy.jar"] == "/tmp/scrcpy-server.jar"
    assert body["sources"]["adb.connect_timeout_s"] == "file"
    assert body["sources"]["scrcpy.jar"] == "file"
    saved = _config_json()
    assert saved["adb"]["connect_timeout_s"] == 45
    assert saved["scrcpy"]["jar"] == "/tmp/scrcpy-server.jar"

    out_of_range = core_client.put("/api/settings", json={"values": {"adb.connect_timeout_s": 999}})
    assert out_of_range.status_code == 422


def test_bootstrap_keys_are_readonly(core_client) -> None:
    """启动项（数据目录/端口）不能通过设置页写：schema 标 bootstrap，API 直接拒绝。"""
    body = core_client.get("/api/settings").json()
    schema = {item["key"]: item for item in body["schema"]}
    entry = schema["runtime.data_dir"]
    assert entry["layer"] == "bootstrap"
    assert entry["writable"] is False
    assert entry["env"] == "AUTOTEST_DATA_DIR"
    assert schema["engine.default_timeout_ms"]["layer"] == "runtime"
    assert schema["engine.default_timeout_ms"]["writable"] is True
    assert body["config_file"].endswith("config.json")

    response = core_client.put(
        "/api/settings", json={"values": {"runtime.data_dir": "/tmp/other-data-dir"}}
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "INVALID_ARGUMENT"
    assert "启动项" in error["message"]
    assert "AUTOTEST_DATA_DIR" in error["message"]
    assert core_client.delete("/api/settings/runtime.data_dir").status_code == 400
    # server.host/port 不在设置页展示，但同样是启动项，不能通过 API 写
    assert (
        core_client.put("/api/settings", json={"values": {"server.port": 9000}}).status_code == 400
    )


def test_legacy_db_rows_migrate_to_file(core_client) -> None:
    """旧库里的配置行：运行项迁进 config.json，启动项只告警不迁移。"""
    with db.session_scope() as session:
        session.add(models.Setting(key="engine.max_steps", value=123))
        session.add(models.Setting(key="runtime.data_dir", value="/tmp/legacy-data-dir"))
    migrated = settings_service.migrate_db_settings_to_file()

    assert migrated == {"engine.max_steps": 123}
    body = core_client.get("/api/settings").json()
    assert body["effective"]["engine.max_steps"] == 123
    assert body["sources"]["engine.max_steps"] == "file"
    assert body["effective"]["runtime.data_dir"] != "/tmp/legacy-data-dir"
    assert _config_json()["engine"]["max_steps"] == 123
    with db.session_scope() as session:
        assert session.get(models.Setting, "engine.max_steps") is None
        assert session.get(models.Setting, "runtime.data_dir") is None


def test_scrcpy_max_fps_zero_is_editable(core_client) -> None:
    """scrcpy.max_fps 存成 0（不限，即 scrcpy 默认值）：保存不能把 0 当成空值。"""
    from app.android import scrcpy

    response = core_client.put("/api/settings", json={"values": {"scrcpy.max_fps": 0}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["effective"]["scrcpy.max_fps"] == 0
    assert body["sources"]["scrcpy.max_fps"] == "file"
    assert _config_json()["scrcpy"]["max_fps"] == 0
    assert core_client.get("/api/settings").json()["effective"]["scrcpy.max_fps"] == 0
    cfg = scrcpy.ServerConfig.from_settings()
    assert cfg.max_fps == 0
    assert "max_fps=0" in " ".join(scrcpy.build_server_args(cfg, "raw"))
    # 从配置文件移除后回到默认 0（不限，与 scrcpy 一致）
    assert core_client.delete("/api/settings/scrcpy.max_fps").status_code == 200
    assert "max_fps" not in _config_json().get("scrcpy", {})  # 空对象会被一起删掉
    assert scrcpy.ServerConfig.from_settings().max_fps == 0


def test_save_writes_config_file_and_takes_effect(core_client) -> None:
    """设置页保存 = 写 config.json + 立即 reload：不重启也生效。"""
    response = core_client.put("/api/settings", json={"values": {"engine.poll_interval_ms": 500}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["effective"]["engine.poll_interval_ms"] == 500
    assert body["sources"]["engine.poll_interval_ms"] == "file"
    assert _config_json()["engine"]["poll_interval_ms"] == 500
    # 其他文件层内容不能被覆盖（runtime.data_dir 等仍在）
    assert _config_json()["runtime"]["data_dir"]

    again = core_client.get("/api/settings").json()
    assert again["effective"]["engine.poll_interval_ms"] == 500
    assert again["sources"]["engine.poll_interval_ms"] == "file"


def test_put_null_removes_key_from_file(core_client) -> None:
    core_client.put("/api/settings", json={"values": {"engine.poll_interval_ms": 500}})
    response = core_client.put("/api/settings", json={"values": {"engine.poll_interval_ms": None}})
    assert response.status_code == 200
    body = response.json()
    assert "poll_interval_ms" not in _config_json().get("engine", {})
    # 文件里的 300 已被覆盖再删除 -> 回到代码默认 350
    assert body["effective"]["engine.poll_interval_ms"] == 350
    assert body["sources"]["engine.poll_interval_ms"] == "default"


def test_delete_setting_falls_back_to_default(core_client) -> None:
    core_client.put("/api/settings", json={"values": {"engine.poll_interval_ms": 500}})
    response = core_client.delete("/api/settings/engine.poll_interval_ms")
    assert response.status_code == 200
    body = response.json()
    assert body["effective"]["engine.poll_interval_ms"] == 350
    assert body["sources"]["engine.poll_interval_ms"] == "default"


def test_reload_picks_up_manual_file_edit(core_client) -> None:
    """手改 config.json 后 POST /api/settings/reload 即可生效（不用重启）。"""
    path = config.config_file_path()
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.setdefault("engine", {})["default_timeout_ms"] = 4321
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    before = core_client.get("/api/settings").json()
    assert before["effective"]["engine.default_timeout_ms"] == 8000  # 还没 reload

    body = core_client.post("/api/settings/reload").json()
    assert body["effective"]["engine.default_timeout_ms"] == 4321
    assert body["sources"]["engine.default_timeout_ms"] == "file"
    assert body["restart_required"] == []


def test_reload_does_not_switch_bootstrap_at_runtime(core_client) -> None:
    """运行中改 data_dir 只提示需重启：绝不能把 effective 目录切走（DB 还在原地）。"""
    old_dir = config.data_dir()
    path = config.config_file_path()
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.setdefault("runtime", {})["data_dir"] = str(path.parent / "another-data-dir")
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    body = core_client.post("/api/settings/reload").json()
    assert "runtime.data_dir" in body["restart_required"]
    assert config.data_dir() == old_dir
    assert str(body["effective"]["runtime.data_dir"]) == str(old_dir)


def test_invalid_config_json_is_not_overwritten(core_client) -> None:
    """配置文件被手改成非法 JSON 时：保存必须报错，不能把用户文件清空重写。"""
    path = config.config_file_path()
    path.write_text("{ not-json", encoding="utf-8")
    response = core_client.put("/api/settings", json={"values": {"engine.max_steps": 10}})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
    assert path.read_text(encoding="utf-8") == "{ not-json"


def test_unknown_key_is_invalid_argument(core_client) -> None:
    response = core_client.put("/api/settings", json={"values": {"not.a.key": 1}})
    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "INVALID_ARGUMENT"
    assert body["details"] == {"key": "not.a.key"}


def test_invalid_value_is_validation_error(core_client) -> None:
    response = core_client.put("/api/settings", json={"values": {"engine.poll_interval_ms": "abc"}})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"]["errors"][0]["path"] == "engine.poll_interval_ms"


def test_out_of_range_value_rejected(core_client) -> None:
    response = core_client.put("/api/settings", json={"values": {"engine.poll_interval_ms": 999999}})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_bool_coercion_from_string(core_client) -> None:
    response = core_client.put("/api/settings", json={"values": {"recording.click_dump": "false"}})
    assert response.status_code == 200
    body = response.json()
    assert body["effective"]["recording.click_dump"] is False
    assert body["sources"]["recording.click_dump"] == "file"
    assert _config_json()["recording"]["click_dump"] is False


def test_reset_clears_file_overrides(core_client) -> None:
    core_client.put(
        "/api/settings",
        json={"values": {"engine.poll_interval_ms": 600, "engine.max_steps": 10}},
    )
    response = core_client.post("/api/settings/reset")
    assert response.status_code == 200
    body = response.json()
    assert body["effective"]["engine.max_steps"] == 2000
    assert body["sources"]["engine.max_steps"] == "default"
    saved = _config_json()
    assert "poll_interval_ms" not in saved.get("engine", {})
    assert saved["runtime"]["data_dir"]  # 启动项不能被 reset 动


def test_scrcpy_options(core_client) -> None:
    response = core_client.get("/api/settings/scrcpy/options")
    assert response.status_code == 200
    body = response.json()
    assert body["server_version"] == "5.0.1"
    assert body["codec"] == "h264"
    assert isinstance(body["jar_present"], bool)
    items = {item["key"]: item for item in body["items"]}
    assert items["scrcpy.max_size"]["value"] == 4096
    assert items["scrcpy.max_size"]["type"] == "int"
    assert items["scrcpy.max_size"]["source"] == "default"
    assert items["scrcpy.scale"]["value"] == 85
    assert items["scrcpy.scale"]["type"] == "int"
    assert items["scrcpy.scale"]["source"] == "default"
