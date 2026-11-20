#!/usr/bin/env bash
# 首次安装：uv + python3.12 + 后端依赖 + 前端依赖 + scrcpy-server + 环境自检
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> 1/5 检查 uv"
if ! command -v uv >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/uv" ]; then
    export PATH="$HOME/.local/bin:$PATH"
  else
    echo "    未安装 uv，正在安装…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
fi
uv --version

echo "==> 2/5 安装 Python 3.12 与后端依赖"
(cd backend && uv python install 3.12 && uv sync --extra dev)

echo "==> 3/5 安装前端依赖"
if command -v npm >/dev/null 2>&1; then
  (cd frontend && npm install --no-audit --no-fund)
else
  echo "    !! 未找到 npm，请先安装 Node.js 20+；前端暂不可用"
fi

echo "==> 4/5 下载 scrcpy-server"
if [ -f "backend/vendor/scrcpy-server-v5.0.1.jar" ]; then
  echo "    已存在，跳过（校验见 scripts/fetch_scrcpy.py --check）"
else
  (cd backend && uv run python ../scripts/fetch_scrcpy.py) || \
    echo "    !! scrcpy-server 下载失败：镜像功能不可用，其余功能不受影响（可稍后手动执行 scripts/fetch_scrcpy.py）"
fi

echo "==> 5/5 环境自检"
(cd backend && uv run python ../scripts/check_env.py) || true

echo
echo "完成。启动：sh scripts/dev.sh   （Windows: powershell -File scripts\\dev.ps1）"
