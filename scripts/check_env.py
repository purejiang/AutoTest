#!/usr/bin/env python
"""环境自检：Python/adb/设备/scrcpy jar/数据目录/端口。在 backend 目录用 uv run 执行：

    cd backend && uv run python ../scripts/check_env.py
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def ok(cond: bool) -> str:
    return "OK  " if cond else "FAIL"


def main() -> int:
    print("=" * 62)
    print("AutoTest 环境自检")
    print("=" * 62)
    failures = 0

    # 1. Python
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    good = sys.version_info >= (3, 12)
    failures += 0 if good else 1
    print(f"[{ok(good)}] Python {py} (需要 >=3.12)")

    try:
        from app import config  # noqa: F401
    except Exception as exc:  # pragma: no cover
        print(f"[FAIL] 无法导入后端包，是否在 backend 目录用 uv run 执行？{exc}")
        return 1

    # 2. adb
    adb = config.find_adb()
    adb_ok = Path(adb).exists() or adb == "adb"
    version = ""
    if adb_ok:
        try:
            out = subprocess.run([adb, "version"], capture_output=True, text=True, timeout=10)
            version = (out.stdout or "").strip().splitlines()[0] if out.stdout else ""
        except Exception as exc:  # noqa: BLE001
            adb_ok = False
            version = str(exc)
    failures += 0 if adb_ok else 1
    print(f"[{ok(adb_ok)}] adb: {adb}  {version}")

    # 3. 设备
    devices: list[str] = []
    if adb_ok:
        try:
            out = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=10)
            for line in (out.stdout or "").splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2:
                    devices.append(f"{parts[0]}({parts[1]})")
        except Exception:  # noqa: BLE001
            pass
    print(f"[{'OK  ' if devices else 'WARN'}] 在线设备: {', '.join(devices) or '无（可先用 fake 设备开发/联调）'}")

    # 4. scrcpy-server
    jar = config.vendor_dir() / "scrcpy-server-v5.0.1.jar"
    lock = config.vendor_dir() / "scrcpy.lock.json"
    if jar.exists():
        size = jar.stat().st_size
        print(f"[OK  ] scrcpy-server: {jar.name} ({size} bytes)  lock: {lock.exists()}")
    else:
        print("[WARN] scrcpy-server 未下载：镜像功能不可用（sh scripts/setup.sh 或 python scripts/fetch_scrcpy.py）")

    # 5. 数据目录
    try:
        config.ensure_dirs()
        d = config.data_dir()
        writable = d.is_dir()
        print(f"[{ok(writable)}] 数据目录: {d}")
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"[FAIL] 数据目录不可用: {exc}")

    # 6. 端口
    for port in (config.get("server.port"), 5173):
        probe = socket.socket()
        probe.settimeout(0.3)
        in_use = probe.connect_ex(("127.0.0.1", int(port))) == 0
        probe.close()
        print(f"[{'WARN' if in_use else 'OK  '}] 端口 {port}: {'被占用' if in_use else '可用'}")

    # 7. 前端依赖 & scrcpy 容量
    print(f"[{'OK  ' if (ROOT / 'frontend' / 'node_modules').exists() else 'WARN'}] 前端依赖 node_modules")
    print(f"[{'OK  ' if (ROOT / 'frontend' / 'dist').exists() else 'INFO'}] 前端构建产物 dist（生产部署需要）")

    print("=" * 62)
    print(f"结论：{'全部就绪' if failures == 0 else f'{failures} 项需要处理'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
