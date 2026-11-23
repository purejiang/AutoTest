"""单文件/目录打包入口（PyInstaller 用；也可直接 `python -m app.main` 启动）。

打包后行为：
- 绑定 127.0.0.1:8787（可用 --port/--host 覆盖），静态资源来自内置 frontend/dist；
- 数据目录默认 ~/.autotest/data，可用 --data-dir 覆盖；
- --open 启动后自动打开浏览器。
"""

from __future__ import annotations

import argparse
import os
import threading
import time
import webbrowser


def main() -> None:
    parser = argparse.ArgumentParser(prog="autotest", description="AutoTest Android 自动化测试工具")
    parser.add_argument("--host", default="", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=0, help="端口（默认 8787）")
    parser.add_argument("--data-dir", default="", help="数据目录（默认 ~/.autotest/data）")
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    args = parser.parse_args()

    from app import config

    # CLI 覆盖走环境变量层（优先级最高，且不会被 lifespan 的 file 层 reload 覆盖）
    if args.data_dir:
        os.environ["AUTOTEST_DATA_DIR"] = str(args.data_dir)
    if args.host:
        os.environ["AUTOTEST_HOST"] = args.host
    if args.port:
        os.environ["AUTOTEST_PORT"] = str(args.port)
    config.set_db_overrides({})  # 清缓存，确保读到环境变量层

    host = str(config.get("server.host") or "127.0.0.1")
    port = int(config.get("server.port") or 8787)
    url = f"http://{host}:{port}"
    print(f"[autotest] 启动 {url}  数据目录: {config.data_dir()}")

    if args.open:
        def _open() -> None:
            time.sleep(2.0)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    import uvicorn

    # 冻结（PyInstaller）环境下不能用 "app.main:app" 字符串，直接传对象
    from app.main import app as asgi_app

    uvicorn.run(asgi_app, host=host, port=port, log_level="info", ws_per_message_deflate=False)


if __name__ == "__main__":
    main()
