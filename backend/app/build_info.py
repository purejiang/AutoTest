"""构建元信息：当前运行的是哪份代码（git commit / 是否带未提交改动 / 构建时间）。

用途：后端 `/api/health` 与前端页面都能显示"现在跑的是哪个提交"，
前端热更（只替换 frontend/dist、不重启后端）后也能一眼看出页面版本与后端是否同源。

来源优先级：环境变量（打包/部署时注入）→ 源码目录 git 命令（源码运行时）→ None。
"""

from __future__ import annotations

import logging
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger("autotest")

# backend/app/build_info.py -> 仓库根 = parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str, timeout_s: float = 2.0) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), *args],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    # 注意：成功但输出为空（如干净工作区的 git status）也是有效结果，不能转成 None
    return result.stdout.strip()


@lru_cache(maxsize=1)
def build_info() -> dict[str, Any]:
    """返回 {commit, dirty, built_at}；拿不到 git 时为 None（打包版建议注入环境变量）。"""
    commit = os.environ.get("AUTOTEST_GIT_COMMIT") or _git("rev-parse", "--short", "HEAD") or None
    dirty_env = os.environ.get("AUTOTEST_GIT_DIRTY")
    if dirty_env is not None:
        dirty: bool | None = dirty_env.strip().lower() in ("1", "true", "yes", "dirty")
    else:
        status = _git("status", "--porcelain")
        dirty = bool(status) if status is not None else None  # 空输出 = 干净 = False
    built_at = os.environ.get("AUTOTEST_BUILD_TIME") or None
    if commit is None and built_at is None:
        logger.debug("build info unavailable (not a git checkout and no env injected)")
    return {"commit": commit, "dirty": dirty, "built_at": built_at}
