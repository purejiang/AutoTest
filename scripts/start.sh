#!/usr/bin/env bash
# AutoTest 一键启动（生产模式：后端同时托管前端，只开一个进程 / 一个端口）
#
#   sh scripts/start.sh            # 启动并打印地址（Vue 主前端）
#   sh scripts/start.sh --open     # 启动并自动打开浏览器
#   sh scripts/start.sh --port 9000
#   sh scripts/start.sh --rebuild  # 强制重新构建前端
#   sh scripts/start.sh --react    # 回滚到冻结的 React 前端（构建产物仍放 frontend/dist）
#
# 首次运行会自动：安装后端依赖(uv sync) → 安装前端依赖(npm install) → 构建前端 → 下载 scrcpy-server。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

# ---- 参数
PORT="${AUTOTEST_PORT:-8787}"
HOST="${AUTOTEST_HOST:-127.0.0.1}"
OPEN_BROWSER=0
REBUILD=0
FRONTEND_IMPL="${AUTOTEST_FRONTEND:-vue}"
while [ $# -gt 0 ]; do
  case "$1" in
    --open) OPEN_BROWSER=1 ;;
    --rebuild) REBUILD=1 ;;
    --react) FRONTEND_IMPL=react ;;
    --vue) FRONTEND_IMPL=vue ;;
    --frontend) shift; FRONTEND_IMPL="$1" ;;
    --port) shift; PORT="$1" ;;
    --host) shift; HOST="$1" ;;
    -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
    *) echo "未知参数: $1（用 --help 查看）"; exit 1 ;;
  esac
  shift
done

case "$FRONTEND_IMPL" in
  vue)   FRONTEND_DIR="frontend" ;;
  react) FRONTEND_DIR="frontend-react" ;;
  *) echo "AUTOTEST_FRONTEND 只能是 vue 或 react（收到：$FRONTEND_IMPL）" >&2; exit 2 ;;
esac

# ---- 环境准备（uv / adb 常见路径补齐）
export PATH="$HOME/.local/bin:$HOME/Library/Android/sdk/platform-tools:$PATH"

say() { printf '\033[1;36m[start]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[start]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[start]\033[0m %s\n' "$*" >&2; exit 1; }

# ---- 1. uv（Python 项目管理器）
if ! command -v uv >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/uv" ]; then
    export PATH="$HOME/.local/bin:$PATH"
  else
    say "未安装 uv，正在自动安装（https://astral.sh/uv）…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null 2>&1 || die "uv 安装失败，请手动安装后重试"
  fi
fi

# ---- 2. 后端依赖
if [ ! -x "backend/.venv/bin/python" ]; then
  say "首次运行：安装 Python 3.12 与后端依赖（约 1 分钟）…"
  (cd backend && uv python install 3.12 && uv sync)
fi

# ---- 3. 前端构建（后端直接托管 frontend/dist，无需单独起前端服务）
BUILT_IMPL="$(cat frontend/dist/.impl 2>/dev/null || true)"
NEED_BUILD=0
if [ ! -f "frontend/dist/index.html" ]; then NEED_BUILD=1; fi
# 没有实现标记时无法判断 dist 是 Vue 还是 React，按需要重建一次（之后会写入标记）。
if [ -z "$BUILT_IMPL" ]; then NEED_BUILD=1; fi
if [ -n "$BUILT_IMPL" ] && [ "$BUILT_IMPL" != "$FRONTEND_IMPL" ]; then NEED_BUILD=1; fi
if [ "$REBUILD" = "1" ]; then NEED_BUILD=1; fi

if [ "$NEED_BUILD" = "1" ]; then
  if command -v npm >/dev/null 2>&1; then
    if [ ! -d "${FRONTEND_DIR}/node_modules" ]; then
      say "安装前端依赖（${FRONTEND_DIR}，首次约 1-2 分钟）…"
      (cd "$FRONTEND_DIR" && npm install --no-audit --no-fund)
    fi
    say "构建前端（${FRONTEND_IMPL} / ${FRONTEND_DIR}）…"
    (cd "$FRONTEND_DIR" && npm run build)
    if [ "$FRONTEND_IMPL" = "react" ]; then
      say "将 React 产物同步到 backend 静态目录 frontend/dist …"
      rm -rf frontend/dist
      mkdir -p frontend/dist
      cp -R frontend-react/dist/. frontend/dist/
    fi
    printf '%s\n' "$FRONTEND_IMPL" > frontend/dist/.impl
  else
    warn "未找到 npm/Node.js：跳过前端构建。仅 API 可用（/api、/docs），界面需另装 Node 20+ 后重跑本脚本。"
  fi
fi

if [ -d "frontend/dist" ]; then
  say "前端实现：${FRONTEND_IMPL}（静态目录 frontend/dist）"
fi

# ---- 4. scrcpy-server（镜像功能）
if [ ! -f "backend/vendor/scrcpy-server-v5.0.1.jar" ]; then
  say "下载 scrcpy-server（镜像需要，约 734KB）…"
  (cd backend && uv run python ../scripts/fetch_scrcpy.py) || \
    warn "scrcpy-server 下载失败：镜像不可用，其余功能不受影响（可稍后重跑本脚本）"
fi

# ---- 5. 端口检查
if command -v lsof >/dev/null 2>&1 && lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  die "端口 $PORT 已被占用。换个端口：sh scripts/start.sh --port 8788"
fi

URL="http://${HOST}:${PORT}"

# ---- 6. 设备/依赖自检（只提示，不阻塞启动）
ADB_BIN="$(command -v adb 2>/dev/null || true)"
if [ -n "$ADB_BIN" ]; then
  DEV_COUNT="$("$ADB_BIN" devices 2>/dev/null | awk 'NR>1 && $2=="device"' | wc -l | tr -d ' ')"
  say "adb: $ADB_BIN ｜ 在线设备: ${DEV_COUNT:-0}"
else
  warn "未找到 adb：请安装 platform-tools 或在「设置」里填写 adb 绝对路径（不影响先打开界面）"
fi
say "启动 AutoTest → $URL   （API 文档 $URL/docs，Ctrl+C 退出）"
if [ "${DEV_COUNT:-0}" = "0" ]; then
  say "提示：插上手机并授权 USB 调试后，「设备」页即可看到真机；没有设备时可用 fake-1 演示设备体验全流程。"
fi

if [ "$OPEN_BROWSER" = "1" ]; then
  ( sleep 2; (command -v open >/dev/null && open "$URL") || (command -v xdg-open >/dev/null && xdg-open "$URL") || true ) &
fi

cd backend
exec uv run uvicorn app.main:app --host "$HOST" --port "$PORT"
