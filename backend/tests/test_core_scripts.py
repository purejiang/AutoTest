"""脚本 CRUD / 校验 / 导入导出 / 快捷运行（契约 §3、§7.2）。"""

from __future__ import annotations

import json

import tests.test_core_support as support
from app import db, models

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
    assert script["origin_recording_id"] is None
    assert script["schema_version"] == 1
    assert script["step_count"] == 1
    assert script["data"]["name"] == "登录流程"
    assert script["last_run"] is None
    assert script["created_at"] and script["updated_at"]

    fetched = core_client.get(f"/api/scripts/{script['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == script["id"]


def _insert_run(
    run_id: int,
    script_id: str,
    status: str = "succeeded",
    *,
    duration_ms: int = 1000,
) -> None:
    stamp = models.now_iso()
    with db.session_scope() as session:
        session.add(
            models.Run(
                id=run_id,
                script_id=script_id,
                script_name="run",
                device_serial="fake-1",
                status=status,
                started_at=stamp,
                ended_at=stamp,
                step_index=3,
                total_steps=3,
                summary={"duration_ms": duration_ms, "passed_steps": 3},
            )
        )


def test_list_scripts_includes_latest_run(core_client) -> None:
    first = _create(core_client, "有运行")
    second = _create(core_client, "无运行")
    _insert_run(101, first["id"], "failed", duration_ms=500)
    _insert_run(102, first["id"], "succeeded", duration_ms=1200)

    body = core_client.get("/api/scripts").json()
    items = {item["id"]: item for item in body["items"]}
    latest = items[first["id"]]["last_run"]
    assert latest["id"] == 102
    assert latest["status"] == "succeeded"
    assert latest["summary"]["duration_ms"] == 1200
    assert latest["device_serial"] == "fake-1"
    assert items[second["id"]]["last_run"] is None

    detail = core_client.get(f"/api/scripts/{first['id']}").json()
    assert detail["last_run"]["id"] == 102

    updated = core_client.put(f"/api/scripts/{first['id']}", json={"name": "改名后"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["last_run"]["id"] == 102

    deleted = core_client.delete("/api/runs/102")
    assert deleted.status_code == 200, deleted.text
    fallback = core_client.get(f"/api/scripts/{first['id']}").json()
    assert fallback["last_run"]["id"] == 101
    assert fallback["last_run"]["status"] == "failed"


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


def test_new_script_seeds_global_default_delays(core_client) -> None:
    """设置 → 执行 的全局默认停顿只作为新建脚本的初值。"""
    put = core_client.put(
        "/api/settings",
        json={
            "values": {
                "engine.default_delay_before_ms": 300,
                "engine.default_delay_after_ms": 200,
            }
        },
    )
    assert put.status_code == 200, put.text
    created = _create(core_client, name="默认停顿脚本")
    settings = created["data"]["settings"]
    assert settings["default_delay_before_ms"] == 300
    assert settings["default_delay_after_ms"] == 200


def test_explicit_delay_settings_are_not_overwritten(core_client) -> None:
    """导入/模板/录制自带停顿值时不被全局初值覆盖（录制显式 0 就保持 0）。"""
    core_client.put("/api/settings", json={"values": {"engine.default_delay_before_ms": 300}})
    data = json.loads(json.dumps(THREE_STEPS))
    data["settings"]["default_delay_before_ms"] = 0
    data["settings"]["default_delay_after_ms"] = 0
    response = core_client.post("/api/scripts", json={"name": "显式零停顿", "data": data})
    assert response.status_code == 200, response.text
    settings = response.json()["data"]["settings"]
    assert settings["default_delay_before_ms"] == 0
    assert settings["default_delay_after_ms"] == 0


def test_validation_error_message_points_to_node(core_client) -> None:
    """前端 toast 只展示 message：必须带节点 id/名称，否则用户不知道是哪个节点。"""
    script = _create(core_client)
    broken = json.loads(json.dumps(THREE_STEPS))
    broken["steps"][1]["params"]["target"]["value"] = ""
    response = core_client.put(f"/api/scripts/{script['id']}", json={"data": broken})
    assert response.status_code == 422
    error = response.json()["error"]["details"]["errors"][0]
    assert error["path"].startswith("script.steps.1")
    assert "节点 s2（步骤2）" in error["message"]
    assert "target by=text requires value" in error["message"]
    assert "Value error," not in error["message"]


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


def test_delete_script_clears_recording_link_only(core_client) -> None:
    script = _create(core_client, name="待删脚本")
    with db.session_scope() as session:
        session.add(
            models.Recording(
                id="rec-link",
                name="关联录制",
                mode="web",
                serial="fake-1",
                status="stopped",
                script_id=script["id"],
            )
        )

    response = core_client.delete(f"/api/scripts/{script['id']}")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "id": script["id"]}
    with db.session_scope() as session:
        assert session.get(models.Script, script["id"]) is None
        recording = session.get(models.Recording, "rec-link")
        assert recording is not None  # 录制本身保留
        assert recording.script_id is None  # 只清空引用


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


# ---------------------------------------------------------------- 批量删除
def test_bulk_delete_scripts_partial_success_dedup_and_unlinks(core_client) -> None:
    first = _create(core_client, name="批量一")
    second = _create(core_client, name="批量二")
    with db.session_scope() as session:
        session.add(
            models.Recording(
                id="rec-bulk-links",
                name="批量关联录制",
                mode="web",
                serial="fake-1",
                status="stopped",
                script_id=first["id"],
            )
        )

    response = core_client.post(
        "/api/scripts/bulk-delete",
        json={"ids": [first["id"], second["id"], "missing-script", first["id"]]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == 2  # 去重后删除 2 个
    assert [(item["id"], item["code"]) for item in body["failed"]] == [
        ("missing-script", "NOT_FOUND")
    ]
    assert core_client.get(f"/api/scripts/{first['id']}").status_code == 404
    assert core_client.get(f"/api/scripts/{second['id']}").status_code == 404
    with db.session_scope() as session:
        recording = session.get(models.Recording, "rec-bulk-links")
        assert recording is not None
        assert recording.script_id is None


def test_bulk_delete_scripts_empty_list_is_400(core_client) -> None:
    response = core_client.post("/api/scripts/bulk-delete", json={"ids": []})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


def test_bulk_delete_scripts_limit_200(core_client) -> None:
    response = core_client.post(
        "/api/scripts/bulk-delete", json={"ids": [f"s{i}" for i in range(201)]}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
