"""脚本 CRUD / 校验 / 导入导出 / 快捷运行（契约 §3、§7.2）。"""

from __future__ import annotations

import json

import tests.test_core_support as support

core_client = support.core_client

THREE_STEPS = {
    "schema_version": 1,
    "name": "三步脚本",
    "notes": "note",
    "variables": {"account": "demo"},
    "settings": {"default_timeout_ms": 8000, "on_fail": "abort", "logcat": False, "retry": None, "screenshot_on_fail": True},
    "defaults": {"retry": None},
    "steps": [
        {
            "id": f"s{index}",
            "name": f"步骤{index}",
            "type": "click",
            "params": {"target": {"by": "text", "value": f"按钮{index}"}},
        }
        for index in range(1, 4)
    ],
}


def _create(client, name: str = "登录流程") -> dict:
    response = client.post("/api/scripts", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()


def test_create_defaults_to_empty_script(core_client) -> None:
    script = _create(core_client)
    assert len(script["id"]) >= 32
    assert script["name"] == "登录流程"
    assert script["source"] == "manual"
    assert script["schema_version"] == 1
    assert script["step_count"] == 1
    assert script["data"]["name"] == "登录流程"
    assert script["created_at"] and script["updated_at"]

    fetched = core_client.get(f"/api/scripts/{script['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == script["id"]


def test_list_query_and_pagination(core_client) -> None:
    for name in ("登录流程", "登出流程", "数据清理"):
        _create(core_client, name)
    listed = core_client.get("/api/scripts")
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert all("step_count" in item for item in body["items"])

    filtered = core_client.get("/api/scripts", params={"query": "登"})
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 2
    assert {item["name"] for item in filtered.json()["items"]} == {"登录流程", "登出流程"}

    paged = core_client.get("/api/scripts", params={"limit": 2, "offset": 0})
    assert len(paged.json()["items"]) == 2
    page2 = core_client.get("/api/scripts", params={"limit": 2, "offset": 2})
    assert len(page2.json()["items"]) == 1
    assert paged.json()["total"] == 3


def test_update_replaces_data_and_step_count(core_client) -> None:
    script = _create(core_client)
    response = core_client.put(f"/api/scripts/{script['id']}", json={"name": "改名脚本", "data": THREE_STEPS})
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["name"] == "改名脚本"
    assert updated["data"]["name"] == "改名脚本"
    assert updated["step_count"] == 3
    assert updated["data"]["variables"] == {"account": "demo"}


def test_update_validation_error_envelope(core_client) -> None:
    script = _create(core_client)
    duplicated = json.loads(json.dumps(THREE_STEPS))
    duplicated["steps"][1]["id"] = duplicated["steps"][0]["id"]
    response = core_client.put(f"/api/scripts/{script['id']}", json={"data": duplicated})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    codes = {item["code"] for item in body["details"]["errors"]}
    assert "duplicate_step_id" in codes


def test_create_invalid_script_rejected(core_client) -> None:
    response = core_client.post("/api/scripts", json={"name": "坏脚本", "data": {"steps": []}})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_validate_endpoint(core_client) -> None:
    valid = core_client.post("/api/scripts/validate", json={"data": support.empty_script("ok")})
    assert valid.status_code == 200
    assert valid.json()["valid"] is True
    assert valid.json()["errors"] == []

    broken = json.loads(json.dumps(THREE_STEPS))
    broken["steps"][0]["on_fail"] = {"action": "goto", "target_id": "missing"}
    invalid = core_client.post("/api/scripts/validate", json={"data": broken})
    assert invalid.status_code == 200
    body = invalid.json()
    assert body["valid"] is False
    assert {item["code"] for item in body["errors"]} == {"goto_target_missing"}


def test_validate_warns_missing_sub_script(core_client) -> None:
    data = json.loads(json.dumps(THREE_STEPS))
    data["steps"] = [
        {
            "id": "sub1",
            "type": "sub_script",
            "params": {"script_id": "not-exists", "variables": {}},
        }
    ]
    response = core_client.post("/api/scripts/validate", json={"data": data})
    assert response.status_code == 200
    codes = {item["code"] for item in response.json()["warnings"]}
    assert "sub_script_missing" in codes


def test_delete_script(core_client) -> None:
    script = _create(core_client)
    deleted = core_client.delete(f"/api/scripts/{script['id']}")
    assert deleted.status_code == 200
    assert core_client.get(f"/api/scripts/{script['id']}").status_code == 404


def test_duplicate_script(core_client) -> None:
    script = _create(core_client)
    core_client.put(f"/api/scripts/{script['id']}", json={"data": THREE_STEPS})
    response = core_client.post(f"/api/scripts/{script['id']}/duplicate", json={"name": "副本A"})
    assert response.status_code == 200
    clone = response.json()
    assert clone["id"] != script["id"]
    assert clone["name"] == "副本A"
    assert clone["step_count"] == 3
    assert clone["data"]["name"] == "副本A"


def test_duplicate_without_body_uses_suffix(core_client) -> None:
    script = _create(core_client, name="原脚本")
    response = core_client.post(f"/api/scripts/{script['id']}/duplicate")
    assert response.status_code == 200
    assert response.json()["name"] == "原脚本 副本"


def test_export_attachment(core_client) -> None:
    script = _create(core_client, name="导出脚本")
    response = core_client.get(f"/api/scripts/{script['id']}/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert "filename*=UTF-8''" in disposition
    exported = response.json()
    assert exported["name"] == "导出脚本"
    assert exported["steps"]


def test_import_json_body(core_client) -> None:
    payload = support.empty_script("导入脚本")
    response = core_client.post("/api/scripts/import", json={"data": payload})
    assert response.status_code == 200, response.text
    imported = response.json()
    assert imported["name"] == "导入脚本"
    assert imported["source"] == "imported"


def test_import_multipart_file(core_client) -> None:
    payload = support.empty_script("上传脚本")
    files = {"file": ("script.json", json.dumps(payload).encode("utf-8"), "application/json")}
    response = core_client.post("/api/scripts/import", files=files)
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "上传脚本"


def test_import_invalid_json(core_client) -> None:
    files = {"file": ("bad.json", b"{not json", "application/json")}
    response = core_client.post("/api/scripts/import", files=files)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_run_returns_503_when_runner_missing(core_client, monkeypatch) -> None:
    from app.api import scripts as scripts_api

    monkeypatch.setattr(scripts_api, "_runner_create_run", lambda: None)
    script = _create(core_client)
    response = core_client.post(f"/api/scripts/{script['id']}/run", json={"serial": "fake-1"})
    assert response.status_code == 503
    body = response.json()["error"]
    assert "run engine" in body["message"]
    assert body["details"]["reason"] == "runner_not_ready"


def test_run_delegates_to_runner(core_client, monkeypatch) -> None:
    from app.api import scripts as scripts_api

    captured: dict = {}

    def fake_create_run(script_id, serial=None, variables=None, debug=None):
        captured.update(script_id=script_id, serial=serial, variables=variables, debug=debug)
        return {"id": 11, "status": "queued", "device_serial": serial}

    monkeypatch.setattr(scripts_api, "_runner_create_run", lambda: fake_create_run)
    script = _create(core_client)
    response = core_client.post(
        f"/api/scripts/{script['id']}/run",
        json={"serial": "fake-1", "variables": {"a": "b"}, "debug": {"step_mode": True}},
    )
    assert response.status_code == 200, response.text
    assert captured["script_id"] == script["id"]
    assert captured["serial"] == "fake-1"
    assert captured["variables"] == {"a": "b"}
    assert captured["debug"] == {"step_mode": True}
    assert response.json()["id"] == 11


def test_run_missing_script_404(core_client, monkeypatch) -> None:
    from app.api import scripts as scripts_api

    monkeypatch.setattr(scripts_api, "_runner_create_run", lambda: None)
    response = core_client.post("/api/scripts/nope/run", json={})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
