#!/usr/bin/env bash
# 首次安装：uv + python3.12 + 后端依赖 + 前端依赖 + scrcpy-server + 环境自检
#
#   默认安装 Vue 3 主前端 frontend/：sh scripts/setup.sh
#   如需回滚/对照 React 前端：sh scripts/setup.sh --react
set -euo pipefail
cd "$(dirname "$0")/.."

FRONTEND_IMPL="${AUTOTEST_FRONTEND:-vue}"
for arg in "$@"; do
  case "$arg" in
    --vue) FRONTEND_IMPL=vue ;;
    --react) FRONTEND_IMPL=react ;;
    -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
    *) echo "未知参数: $arg（用 --help 查看）"; exit 1 ;;
  esac
done
case "$FRONTEND_IMPL" in
  vue)   FRONTEND_DIR="frontend" ;;
  react) FRONTEND_DIR="frontend-react" ;;
  *) echo "AUTOTEST_FRONTEND 只能是 vue 或 react（收到：$FRONTEND_IMPL）" >&2; exit 2 ;;
esac

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

echo "==> 3/5 安装前端依赖（${FRONTEND_IMPL} / ${FRONTEND_DIR}）"
if command -v npm >/dev/null 2>&1; then
  if [ "$FRONTEND_IMPL" = "react" ]; then
    echo "    注意：frontend-react/ 是 v0.1.3 的回滚备份，不再继续开发。"
  fi
  (cd "$FRONTEND_DIR" && npm install --no-audit --no-fund)
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
echo "完成。启动：sh scripts/dev.sh   （生产模式：sh scripts/start.sh；Windows: powershell -File scripts\\dev.ps1）"
