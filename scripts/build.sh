#!/usr/bin/env bash
# 生产构建：前端 dist（后端静态目录固定为 frontend/dist）+ 可选 PyInstaller 单文件
#
#   默认构建 Vue 3 主前端：sh scripts/build.sh
#   回滚构建（React 产物仍输出到 frontend/dist，后端无需改动）：
#     sh scripts/build.sh --react
#   加 --pack 额外打包单文件可执行程序。
set -euo pipefail
cd "$(dirname "$0")/.."

FRONTEND_IMPL="${AUTOTEST_FRONTEND:-vue}"
DO_PACK=0
for arg in "$@"; do
  case "$arg" in
    --vue) FRONTEND_IMPL=vue ;;
    --react) FRONTEND_IMPL=react ;;
    --pack) DO_PACK=1 ;;
    -h|--help)
      awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"
      exit 0
      ;;
    *) echo "[build] 未知参数：$arg（用 --help 查看）" >&2; exit 2 ;;
  esac
done

case "$FRONTEND_IMPL" in
  vue)   FRONTEND_DIR="frontend" ;;
  react) FRONTEND_DIR="frontend-react" ;;
  *) echo "[build] AUTOTEST_FRONTEND 只能是 vue 或 react（收到：$FRONTEND_IMPL）" >&2; exit 2 ;;
esac

echo "==> 1/3 前端构建（${FRONTEND_IMPL} / ${FRONTEND_DIR}）"
if [ ! -d "${FRONTEND_DIR}/node_modules" ]; then
  echo "    安装 ${FRONTEND_DIR} 依赖…"
  (cd "$FRONTEND_DIR" && npm install --no-audit --no-fund)
fi
(cd "$FRONTEND_DIR" && npm run build)

if [ "$FRONTEND_IMPL" = "react" ]; then
  echo "    将 React 产物同步到 backend 静态目录 frontend/dist …"
  rm -rf frontend/dist
  mkdir -p frontend/dist
  cp -R frontend-react/dist/. frontend/dist/
fi
printf '%s\n' "$FRONTEND_IMPL" > frontend/dist/.impl

echo "==> 2/3 后端静态资源检查"
if [ -f "frontend/dist/index.html" ]; then
  echo "    dist 就绪（实现：${FRONTEND_IMPL}）：后端启动后访问 http://127.0.0.1:8787 即为完整应用"
else
  echo "    !! frontend/dist/index.html 缺失"
  exit 1
fi

echo "==> 3/3 可选打包（PyInstaller，需要 backend/.venv 已装 dev+build 依赖）"
if [ "$DO_PACK" = "1" ]; then
  (cd backend && uv run pyinstaller --clean --noconfirm autotest.spec)
  echo "    产物：backend/dist/autotest"
else
  echo "    跳过（加 --pack 生成单文件可执行程序）"
fi
