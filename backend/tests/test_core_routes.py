"""路由表自检：动态 include + router/ws_router 拆分后，关键路径必须仍然存在。

背景：main.py 用 pkgutil 动态发现 ``api`` 包内的 router；FastAPI 新版本把
``include_router`` 包装成 ``_IncludedRouter``，顶层 ``app.routes`` 看不到真实
APIRoute，因此这里递归展开后断言，防止“路由前缀调整/模块拆分”静默丢端点。
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import WebSocketRoute

from app import main

CRITICAL_HTTP: set[tuple[str, str]] = {
    ("GET", "/api/devices"),
    ("POST", "/api/devices/connect"),
    ("GET", "/api/devices/{serial}/hierarchy"),
    ("POST", "/api/devices/{serial}/mirror/start"),
    ("POST", "/api/devices/{serial}/mirror/stop"),
    ("POST", "/api/devices/{serial}/mirror/diag"),
    ("GET", "/api/devices/{serial}/mirror/status"),
    ("GET", "/api/recordings"),
    ("POST", "/api/recordings"),
    ("POST", "/api/recordings/{recording_id}/to_script"),
    ("POST", "/api/runs"),
    ("GET", "/api/runs/{run_id}"),
    ("POST", "/api/runs/{run_id}/report"),
    ("GET", "/api/reports"),
    ("GET", "/api/scripts"),
    ("GET", "/api/scripts/{script_id}"),
    ("GET", "/api/settings"),
    ("GET", "/api/settings/scrcpy/options"),
}

CRITICAL_WS: set[str] = {
    "/ws/runs/{run_id}",
    "/ws/mirror/{serial}",
    "/ws/recordings/{recording_id}",
}


def _walk(routes: list) -> Iterator:
    """递归展开 _IncludedRouter；只沿 original_router 下钻，不碰 StaticFiles。"""
    for route in routes:
        yield route
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _walk(inner.routes)


def _route_table(app: FastAPI) -> tuple[set[tuple[str, str]], set[str], list[tuple[str, str]]]:
    http: set[tuple[str, str]] = set()
    ws: set[str] = set()
    all_http: list[tuple[str, str]] = []
    for route in _walk(app.routes):
        if isinstance(route, WebSocketRoute):
            ws.add(route.path)
        elif isinstance(route, APIRoute):
            for method in route.methods or ():
                if method in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
                    http.add((method, route.path))
                    all_http.append((method, route.path))
    return http, ws, all_http


def test_critical_http_routes_registered() -> None:
    app = main.create_app()
    http, _, _ = _route_table(app)
    missing = CRITICAL_HTTP - http
    assert not missing, f"missing HTTP routes: {sorted(missing)}"


def test_critical_websocket_routes_registered() -> None:
    app = main.create_app()
    _, ws, _ = _route_table(app)
    missing = CRITICAL_WS - ws
    assert not missing, f"missing WS routes: {sorted(missing)}"
    # WS 不能误挂到 /api 前缀下（契约 §6：WS 路径挂根）
    assert not any(path.startswith("/api/") for path in ws), sorted(ws)


def test_no_duplicate_http_route() -> None:
    app = main.create_app()
    _, _, all_http = _route_table(app)
    duplicates = {item for item in all_http if all_http.count(item) > 1}
    assert not duplicates, f"duplicate HTTP routes: {sorted(duplicates)}"


def test_openapi_exposes_core_response_models() -> None:
    """OpenAPI 必须带核心资源的响应 schema（前端 TS 类型由它生成）。"""
    schema = main.create_app().openapi()
    schemas = schema["components"]["schemas"]
    for name in ("RunOut", "RunDetailOut", "StepResultOut", "ArtifactOut", "RunListOut"):
        assert name in schemas, name
    detail = schema["paths"]["/api/runs/{run_id}"]["get"]["responses"]["200"]
    assert detail["content"]["application/json"]["schema"]["$ref"].endswith("/RunDetailOut")
