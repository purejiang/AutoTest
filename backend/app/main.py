"""应用装配（契约 §0 / §14.6）。

- lifespan：文件层配置 -> 目录 -> 建表 -> 从 DB settings 载入覆盖；
- 动态路由发现：遍历 `app.api` 包内模块，凡暴露 `router` 的自动 include；
  队友模块尚未落地/依赖缺失（ImportError）时跳过并告警，保证任何阶段都能启动；
- 静态托管：`backend/static` -> /static；`frontend/dist` -> /（SPA fallback 到 index.html）；
- 错误信封：`errors.install_error_handlers()` + 框架级 HTTPException 也套信封（契约 §1）。
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from . import __version__, config, db, errors
from .logs import logger
from .services import settings_service

BACKEND_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = BACKEND_ROOT / "static"
# 源码运行：仓库根/frontend/dist；PyInstaller onefile：_MEIPASS/frontend/dist。
# 冻结后 __file__ 在 _MEIPASS/app 下，BACKEND_ROOT.parent 会跑到临时目录之外。
if getattr(sys, "frozen", False):
    FRONTEND_DIST = Path(getattr(sys, "_MEIPASS", BACKEND_ROOT)) / "frontend" / "dist"
else:
    FRONTEND_DIST = BACKEND_ROOT.parent / "frontend" / "dist"


def frontend_dist_dir() -> Path:
    """对外提供前端产物的目录。

    默认用仓库内 ``frontend/dist``；设置 ``AUTOTEST_FRONTEND_DIR``（或配置
    ``runtime.frontend_dir``）可指向任意目录——这样**打包后的单文件程序也能热更前端**：
    重建 dist 后覆盖该目录里的文件、刷新浏览器即可，无需重启后端。
    """
    override = os.environ.get("AUTOTEST_FRONTEND_DIR") or ""
    if not override:
        try:
            override = str(config.get("runtime.frontend_dir") or "")
        except Exception:  # pragma: no cover - 配置层异常不影响启动
            override = ""
    if override:
        candidate = Path(override).expanduser()
        if (candidate / "index.html").exists():
            return candidate
        logger.warning("frontend dir override has no index.html, fallback to builtin: %s", candidate)
    return FRONTEND_DIST

CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

_STATUS_TO_CODE: dict[int, str] = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHORIZED",
    403: "UNAUTHORIZED",
    404: "NOT_FOUND",
    405: "INVALID_ARGUMENT",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    502: "SCRCPY_FAILED",
    503: "DEVICE_UNAVAILABLE",
}


# ---------------------------------------------------------------- lifespan
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    config.reload_file_layer()
    config.ensure_dirs()
    db.init_db()
    try:
        settings_service.load_into_config()
    except Exception:  # DB 坏了也要能起服务，前端可看到 /api/health 的 degraded
        logger.exception("failed to load settings from db; fallback to file/default layer")
    logger.info("AutoTest backend ready (version %s, data_dir=%s)", __version__, config.data_dir())
    yield
    logger.info("AutoTest backend stopped")


# ---------------------------------------------------------------- 路由发现
def _include_api_routers(app: FastAPI) -> list[str]:
    from . import api as api_package

    included: list[str] = []
    modules = sorted(pkgutil.iter_modules(api_package.__path__), key=lambda item: item.name)
    for module_info in modules:
        name = module_info.name
        if name.startswith("_"):
            continue
        module_path = f"{api_package.__name__}.{name}"
        try:
            module = importlib.import_module(module_path)
        except (ImportError, SyntaxError) as exc:
            # 队友模块尚未落地 / 可选依赖缺失 / 并行开发中的半成品文件：跳过，不阻塞启动（契约 §14.6）
            logger.warning("skip api module %s: %s", module_path, exc)
            continue
        router = getattr(module, "router", None)
        if router is None:
            continue
        app.include_router(router)
        included.append(name)
    # 运行事件 WS 可能在 app/ws.py（be-runner）；仅当没有其它 WS 路由时兜底挂载，避免重复注册
    if not any(isinstance(route, WebSocketRoute) for route in app.routes):
        try:
            ws_module = importlib.import_module("app.ws")
        except (ImportError, SyntaxError):
            ws_module = None
        ws_router = getattr(ws_module, "router", None) if ws_module is not None else None
        if ws_router is not None:
            app.include_router(ws_router)
            included.append("app.ws")
    return included


# ---------------------------------------------------------------- 错误信封（框架级 404/405 等）
def _install_http_exception_handler(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code)
        if code is None:
            code = "INTERNAL" if exc.status_code >= 500 else "INVALID_ARGUMENT"
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(status_code=exc.status_code, content=errors.error_body(code, detail, {}))


# ---------------------------------------------------------------- 静态托管
class SPAStaticFiles(StaticFiles):
    """命中不到文件时回落 index.html（前端 history 路由）。

    /api、/ws、/docs 等后端路径不回落（否则拼错的 API 会拿到 index.html 200，掩盖错误）。
    """

    _NO_FALLBACK_PREFIXES = ("api/", "ws/", "docs", "redoc", "openapi.json")

    async def get_response(self, path: str, scope: Scope) -> Any:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            if path == "api" or path.startswith(self._NO_FALLBACK_PREFIXES):
                raise
            return await super().get_response("index.html", scope)


def _mount_static(app: FastAPI) -> list[str]:
    mounted: list[str] = []
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
        mounted.append(str(STATIC_DIR))
    # 产物直链（截图/视频/logcat）：必须在 "/" 之前挂载，否则被 SPA fallback 吞掉
    try:
        artifacts_root = config.artifacts_dir()
        if artifacts_root.is_dir():
            app.mount("/artifacts", StaticFiles(directory=str(artifacts_root)), name="artifacts")
            mounted.append(f"/artifacts -> {artifacts_root}")
    except OSError:  # 数据目录不可写时不挂载，端点仍返回 url（会 404，由前端提示）
        logger.warning("artifacts dir unavailable, skip /artifacts mount")
    dist_dir = frontend_dist_dir()
    if dist_dir.is_dir() and (dist_dir / "index.html").exists():
        app.mount("/", SPAStaticFiles(directory=str(dist_dir), html=True), name="frontend")
        mounted.append(str(dist_dir))
    return mounted


# ---------------------------------------------------------------- 装配
def create_app() -> FastAPI:
    # 静态挂载（尤其 /artifacts）要读 data_dir/artifacts_dir：必须先装载文件层配置，
    # 否则用 ~/.autotest/config.json 切换数据目录后，DB 在新区、产物静态目录还指向旧区。
    config.reload_file_layer()
    application = FastAPI(
        title="AutoTest",
        version=__version__,
        description="Android automated testing tool backend (adb / uiautomator2 / scrcpy)",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    errors.install_error_handlers(application)
    _install_http_exception_handler(application)

    included = _include_api_routers(application)
    logger.info("included api routers: %s", ", ".join(included) if included else "(none)")

    frontend_built = (frontend_dist_dir() / "index.html").exists()
    mounted = _mount_static(application)
    logger.info("mounted static: %s", ", ".join(mounted) if mounted else "(none)")

    if not frontend_built:
        # 前端未构建时给根路径一个可读提示（构建后由 SPAStaticFiles 托管 index.html）
        @application.get("/", include_in_schema=False)
        def _index() -> dict[str, Any]:
            return {
                "name": "AutoTest",
                "version": __version__,
                "docs": "/docs",
                "api": "/api",
                "hint": "frontend/dist not built yet; run `npm run dev` (vite) or `npm run build`",
            }

    return application


app = create_app()


def _run_server() -> None:
    """`python -m app.main` 入口：按配置 host/port 起 uvicorn。"""
    import uvicorn

    config.reload_file_layer()
    config.ensure_dirs()
    db.init_db()
    try:
        settings_service.load_into_config()
    except Exception:  # pragma: no cover - 与 lifespan 同口径
        logger.exception("failed to load settings from db")
    host = str(config.get("server.host") or "127.0.0.1")
    port = int(config.get("server.port") or 8787)
    logger.info("starting uvicorn on http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info", ws_per_message_deflate=False)


if __name__ == "__main__":
    _run_server()
