"""装配 / 健康检查 / 错误信封（契约 §1、§7.6）。"""

from __future__ import annotations

import tests.test_core_support as support

core_client = support.core_client


def test_app_imports_and_exports_factories() -> None:
    from app import main

    assert callable(main.create_app)
    assert main.app is not None
    assert main.app.title == "AutoTest"


def test_health_ok(core_client) -> None:
    response = core_client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["db"] == "ok"
    assert isinstance(body["adb"], dict)
    assert {"path", "version", "devices"} <= set(body["adb"])
    assert body["data_dir"]


def test_root_serves_index_or_hint(core_client) -> None:
    """frontend/dist 已构建时托管 index.html，未构建时返回 JSON 提示。"""
    from app import main

    response = core_client.get("/")
    assert response.status_code == 200
    if (main.FRONTEND_DIST / "index.html").exists():
        assert "text/html" in response.headers["content-type"]
        assert "<html" in response.text.lower()
    else:
        assert response.json()["name"] == "AutoTest"


def test_not_found_error_envelope_from_service(core_client) -> None:
    response = core_client.get("/api/scripts/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "NOT_FOUND"
    assert "not found" in body["error"]["message"]
    assert body["error"]["details"] == {"id": "does-not-exist"}


def test_not_found_error_envelope_from_router_404(core_client) -> None:
    response = core_client.get("/api/definitely-not-a-route")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"]


def test_request_validation_error_envelope(core_client) -> None:
    # 同时给绝对与归一化坐标 -> pydantic 校验失败 -> 422 VALIDATION_ERROR
    response = core_client.post("/api/devices/fake-1/tap", json={"x": 1, "y": 2, "nx": 0.5, "ny": 0.5})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(body["error"]["details"]["errors"], list)
    assert body["error"]["details"]["errors"]
    first = body["error"]["details"]["errors"][0]
    assert {"path", "code", "message"} <= set(first)


def test_openapi_contains_core_routes(core_client) -> None:
    response = core_client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for path in ("/api/health", "/api/devices", "/api/scripts", "/api/settings"):
        assert path in paths, path


async def test_spa_fallback_serves_index_but_not_api(tmp_path) -> None:
    from starlette.exceptions import HTTPException

    from app.main import SPAStaticFiles

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    files = SPAStaticFiles(directory=str(dist), html=True)
    scope = {"type": "http", "method": "GET", "path": "/devices", "headers": []}

    index = await files.get_response("devices", scope)
    assert index.status_code == 200

    try:
        await files.get_response("api/does-not-exist", scope)
    except HTTPException as exc:
        assert exc.status_code == 404
    else:  # pragma: no cover - 明确失败而不是静默通过
        raise AssertionError("api 路径不应回落到 index.html")
