"""设置三级优先级 / 读写 / 校验（契约 §7.6、§11）。"""

from __future__ import annotations

import tests.test_core_support as support

core_client = support.core_client


def test_default_response_shape(core_client) -> None:
    response = core_client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert {"effective", "sources", "schema"} <= set(body)
    assert body["sources"]["engine.poll_interval_ms"] == "file"  # 文件层
    assert body["effective"]["engine.poll_interval_ms"] == 300
    assert body["sources"]["engine.default_timeout_ms"] == "default"
    assert body["effective"]["engine.default_timeout_ms"] == 8000
    assert body["sources"]["runtime.data_dir"] == "file"
    keys = {item["key"] for item in body["schema"]}
    assert {"adb.path", "engine.poll_interval_ms", "scrcpy.max_size"} <= keys


def test_db_layer_overrides_file_layer(core_client) -> None:
    response = core_client.put("/api/settings", json={"values": {"engine.poll_interval_ms": 500}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["effective"]["engine.poll_interval_ms"] == 500
    assert body["sources"]["engine.poll_interval_ms"] == "db"

    again = core_client.get("/api/settings").json()
    assert again["effective"]["engine.poll_interval_ms"] == 500
    assert again["sources"]["engine.poll_interval_ms"] == "db"


def test_put_null_removes_db_override(core_client) -> None:
    core_client.put("/api/settings", json={"values": {"engine.poll_interval_ms": 500}})
    response = core_client.put("/api/settings", json={"values": {"engine.poll_interval_ms": None}})
    assert response.status_code == 200
    body = response.json()
    assert body["sources"]["engine.poll_interval_ms"] == "file"
    assert body["effective"]["engine.poll_interval_ms"] == 300


def test_delete_setting_falls_back_to_file(core_client) -> None:
    core_client.put("/api/settings", json={"values": {"engine.poll_interval_ms": 500}})
    response = core_client.delete("/api/settings/engine.poll_interval_ms")
    assert response.status_code == 200
    body = response.json()
    assert body["effective"]["engine.poll_interval_ms"] == 300
    assert body["sources"]["engine.poll_interval_ms"] == "file"


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
    assert body["sources"]["recording.click_dump"] == "db"


def test_reset_clears_db_layer(core_client) -> None:
    core_client.put(
        "/api/settings",
        json={"values": {"engine.poll_interval_ms": 600, "engine.max_steps": 10}},
    )
    response = core_client.post("/api/settings/reset")
    assert response.status_code == 200
    body = response.json()
    assert body["sources"]["engine.poll_interval_ms"] == "file"
    assert body["effective"]["engine.max_steps"] == 2000
    assert "db" not in set(body["sources"].values())


def test_scrcpy_options(core_client) -> None:
    response = core_client.get("/api/settings/scrcpy/options")
    assert response.status_code == 200
    body = response.json()
    assert body["server_version"] == "5.0.1"
    assert body["codec"] == "h264"
    assert isinstance(body["jar_present"], bool)
    items = {item["key"]: item for item in body["items"]}
    assert items["scrcpy.max_size"]["value"] == 1600
    assert items["scrcpy.max_size"]["type"] == "int"
    assert items["scrcpy.max_size"]["source"] == "default"
