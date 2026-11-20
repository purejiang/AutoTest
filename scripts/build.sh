#!/usr/bin/env bash
# 生产构建：前端 dist + 后端静态托管；可选 PyInstaller 单文件
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> 1/3 前端构建"
(cd frontend && npm run build)

echo "==> 2/3 后端静态资源检查"
if [ -d "frontend/dist" ]; then
  echo "    dist 就绪：后端启动后访问 http://127.0.0.1:8787 即为完整应用"
else
  echo "    !! frontend/dist 缺失"
  exit 1
fi

echo "==> 3/3 可选打包（PyInstaller，需要 backend/.venv 已装 dev+build 依赖）"
if [ "${1:-}" = "--pack" ]; then
  (cd backend && uv run pyinstaller --clean --noconfirm autotest.spec)
  echo "    产物：backend/dist/autotest"
else
  echo "    跳过（加 --pack 生成单文件可执行程序）"
fi
