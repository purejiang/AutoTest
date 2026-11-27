#!/usr/bin/env python3
"""导出后端 OpenAPI 到 docs/openapi.json（前端生成 TS 类型的唯一输入）。

用法：
    python scripts/export_openapi.py            # 写入 docs/openapi.json
    python scripts/export_openapi.py --check    # 只校验是否与仓库文件一致（CI 用）

脚本会优先使用 backend/.venv 的解释器启动；直接 `python3 scripts/export_openapi.py`
也可以，只要当前环境能 import app.main。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
TARGET = ROOT / "docs" / "openapi.json"


def build_schema() -> dict:
    sys.path.insert(0, str(BACKEND))
    from app.main import create_app  # noqa: PLC0415 - 需要先插 sys.path

    return create_app().openapi()


def main() -> int:
    check = "--check" in sys.argv[1:]
    schema = build_schema()
    rendered = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    if check:
        if not TARGET.exists():
            print(f"[openapi] {TARGET} 不存在，请先运行 python scripts/export_openapi.py", file=sys.stderr)
            return 1
        current = TARGET.read_text(encoding="utf-8")
        if current != rendered:
            print("[openapi] docs/openapi.json 与后端代码不一致，请重新导出", file=sys.stderr)
            return 1
        print("[openapi] 与后端一致")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(rendered, encoding="utf-8")
    paths = len(schema.get("paths", {}))
    print(f"[openapi] 已写入 {TARGET.relative_to(ROOT)}（{paths} 个路径）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
