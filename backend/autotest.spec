# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包：cd backend && uv run pyinstaller --clean --noconfirm autotest.spec
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve()  # backend/
PROJECT = ROOT.parent
FE_DIST = PROJECT / "frontend" / "dist"

datas = [
    (str(ROOT / "app" / "report" / "templates"), "app/report/templates"),
]
if (ROOT / "vendor").exists():
    datas.append((str(ROOT / "vendor"), "vendor"))
if FE_DIST.exists():
    datas.append((str(FE_DIST), "static"))
else:
    print("WARNING: frontend/dist 不存在，打包产物没有前端界面（先 npm run build）", file=sys.stderr)

# 动态导入的子模块必须显式收集，否则冻结包里缺路由
_dynamic = []
for _path in ROOT.glob("app/**/*.py"):
    if _path.name == "__init__.py":
        continue
    _mod = ".".join(_path.relative_to(ROOT).with_suffix("").parts)
    _dynamic.append(_mod)
for _pkg in ["app", "app.api", "app.services", "app.android", "app.runner", "app.recorder", "app.report", "app.capture"]:
    _dynamic.append(_pkg)

hiddenimports = _dynamic + [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "websockets",
    "jinja2.ext",
    "sqlalchemy.dialects.sqlite",
    "uiautomator2",
    "adbutils",
]

a = Analysis(
    [str(ROOT / "autotest_main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "mitmproxy", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="autotest",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
