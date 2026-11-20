#!/usr/bin/env bash
# 一键启动：后端(8787) + 前端(5173)，Ctrl+C 同时退出
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

BACKEND_PORT="${AUTOTEST_PORT:-8787}"
export PATH="$HOME/.local/bin:$HOME/Library/Android/sdk/platform-tools:$PATH"

if [ ! -d "backend/.venv" ]; then
  echo "[dev] 首次运行，先执行依赖安装…"
  (cd backend && uv sync --extra dev)
fi
if [ ! -d "frontend/node_modules" ]; then
  echo "[dev] 安装前端依赖…"
  (cd frontend && npm install --no-audit --no-fund)
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
  cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" --reload
) &
PIDS+=($!)

sleep 2
echo "[dev] 前端 http://127.0.0.1:5173"
(
  cd frontend && AUTOTEST_BACKEND="http://127.0.0.1:${BACKEND_PORT}" npm run dev
) &
PIDS+=($!)

wait
