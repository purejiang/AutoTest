#!/usr/bin/env bash
# 开发模式：后端(8787) + 前端(5173)，Ctrl+C 同时退出
#
#   默认使用 Vue 3 主前端 frontend/：
#     sh scripts/dev.sh
#   回滚到冻结的 React 前端（frontend-react/，仅用于对照/回滚）：
#     sh scripts/dev.sh --react
#   也可用环境变量切换：AUTOTEST_FRONTEND=react sh scripts/dev.sh
set -euo pipefail
cd "$(dirname "$0")/.."

FRONTEND_IMPL="${AUTOTEST_FRONTEND:-vue}"
for arg in "$@"; do
  case "$arg" in
    --vue) FRONTEND_IMPL=vue ;;
    --react) FRONTEND_IMPL=react ;;
    -h|--help)
      awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"
      exit 0
      ;;
    *) echo "[dev] 未知参数：$arg（用 --help 查看）" >&2; exit 2 ;;
  esac
done

case "$FRONTEND_IMPL" in
  vue)   FRONTEND_DIR="frontend" ;;
  react) FRONTEND_DIR="frontend-react" ;;
  *) echo "[dev] AUTOTEST_FRONTEND 只能是 vue 或 react（收到：$FRONTEND_IMPL）" >&2; exit 2 ;;
esac

BACKEND_PORT="${AUTOTEST_PORT:-8787}"
export PATH="$HOME/.local/bin:$HOME/Library/Android/sdk/platform-tools:$PATH"

if [ ! -d "backend/.venv" ]; then
  echo "[dev] 首次运行，先执行依赖安装…"
  (cd backend && uv sync --extra dev)
fi
if [ ! -d "${FRONTEND_DIR}/node_modules" ]; then
  echo "[dev] 安装 ${FRONTEND_DIR} 依赖…"
  (cd "$FRONTEND_DIR" && npm install --no-audit --no-fund)
fi

echo "[dev] 前端实现：${FRONTEND_IMPL}（${FRONTEND_DIR}）"
if [ "$FRONTEND_IMPL" = "react" ]; then
  echo "[dev] 注意：frontend-react/ 是 v0.1.3 的回滚备份，不再继续开发。"
fi

PIDS=()
cleanup() {
  echo
  echo "[dev] 停止服务…"
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[dev] 后端 http://127.0.0.1:${BACKEND_PORT}  (API 文档 /docs)"
(
  cd backend
  if [ -x ".venv/bin/python" ]; then
    exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" --ws-per-message-deflate false --reload
  fi
  exec uv run uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" --ws-per-message-deflate false --reload
) &
PIDS+=($!)

sleep 2
echo "[dev] 前端 http://127.0.0.1:5173"
(
  cd "$FRONTEND_DIR" && AUTOTEST_BACKEND="http://127.0.0.1:${BACKEND_PORT}" npm run dev
) &
PIDS+=($!)

wait
