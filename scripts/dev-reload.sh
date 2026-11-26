#!/usr/bin/env bash
# 一键重启后端 + 热更新（改 backend/app 下的 .py 自动重启，不用再手动重启）
#
#   sh scripts/dev-reload.sh                  # 默认 8787：停旧进程 → 启动 --reload 后端
#   sh scripts/dev-reload.sh --port 9000      # 换端口
#   sh scripts/dev-reload.sh --watch-frontend # 同时监听前端源码，自动重建 frontend/dist
#   sh scripts/dev-reload.sh --open           # 启动后自动打开浏览器
#   sh scripts/dev-reload.sh -h               # 帮助
#
# 说明：
#   - 先停掉当前端口上的 AutoTest 旧进程（只停本项目进程；端口被别的程序占用会报错退出）；
#   - 后端 code 改动由 uvicorn --reload 自动重启；脚本额外做健康看门狗，
#     服务卡死/worker 崩溃也会自动重启（uvicorn reloader 在 worker 被强杀时可能不退出）；
#   - 前端是静态托管 frontend/dist：不加 --watch-frontend 时改前端仍需 build；
#     想要前端 HMR 用 scripts/dev.sh（Vite 开发服务器）。
#   - 跑批/演示时不要用本脚本：重启会中断正在执行的运行任务。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${AUTOTEST_PORT:-8787}"
HOST="${AUTOTEST_HOST:-127.0.0.1}"
WATCH_FRONTEND=0
OPEN_BROWSER=0

while [ $# -gt 0 ]; do
  case "$1" in
    --port) shift; PORT="${1:-8787}" ;;
    --host) shift; HOST="${1:-127.0.0.1}" ;;
    --watch-frontend) WATCH_FRONTEND=1 ;;
    --open) OPEN_BROWSER=1 ;;
    -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
    *) echo "[reload] 未知参数: $1（用 -h 查看帮助）" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\033[1;36m[reload]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[reload]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[reload]\033[0m %s\n' "$*" >&2; exit 1; }

PY="$ROOT/backend/.venv/bin/python"
[ -x "$PY" ] || die "缺少 $ROOT/backend/.venv；先执行：sh $ROOT/scripts/setup.sh"

port_pids() { lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true; }

is_project_pid() {
  local pid="$1" cmd cwd
  cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  case "$cmd" in
    *app.main:app*|*autotest*) return 0 ;;
  esac
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1 || true)"
  [ "$cwd" = "$ROOT" ] || [ "$cwd" = "$ROOT/backend" ]
}

kill_tree() {
  local pid="$1" child
  [ -z "$pid" ] && return 0
  child="$(pgrep -P "$pid" 2>/dev/null || true)"
  [ -n "$child" ] && kill -TERM $child 2>/dev/null || true
  kill -TERM "$pid" 2>/dev/null || true
}

stop_existing() {
  local pids pid
  pids="$(port_pids)"
  [ -z "$pids" ] && return 0
  for pid in $pids; do
    is_project_pid "$pid" || die "端口 ${PORT} 被其他程序占用（PID ${pid}：$(ps -p "$pid" -o command= 2>/dev/null)）；换端口：--port 9000"
  done
  say "停止端口 $PORT 上的旧进程：$(printf '%s ' $pids)"
  for pid in $pids; do kill_tree "$pid"; done
  for _ in $(seq 1 40); do
    [ -z "$(port_pids)" ] && { say "旧进程已停止"; return 0; }
    sleep 0.25
  done
  warn "旧进程未响应 TERM，强制结束"
  for pid in $(port_pids); do
    child="$(pgrep -P "$pid" 2>/dev/null || true)"
    [ -n "$child" ] && kill -KILL $child 2>/dev/null || true
    kill -KILL "$pid" 2>/dev/null || true
  done
  sleep 0.5
  [ -z "$(port_pids)" ] || die "端口 ${PORT} 仍被占用，停止失败"
}

FE_PID=""
UV_PID=""
WD_PID=""
STOP=0
FLAG_FILE="/tmp/autotest-dev-reload-${PORT}.restart"
rm -f "$FLAG_FILE"
cleanup() {
  STOP=1
  for pid in "$WD_PID" "$UV_PID" "$FE_PID"; do
    [ -n "$pid" ] && kill_tree "$pid"
  done
  # npx 可能把 vite 子进程 reparent 掉，按绝对路径兜底清理
  if [ -n "$FE_PID" ]; then
    for pid in $(pgrep -f "$ROOT/frontend/node_modules/.bin/vite build --watch" 2>/dev/null || true); do
      kill -TERM "$pid" 2>/dev/null || true
    done
  fi
  rm -f "$FLAG_FILE"
}
trap 'STOP=1; cleanup; exit 0' INT TERM
trap cleanup EXIT

cd "$ROOT/backend"
stop_existing

if [ "$WATCH_FRONTEND" = "1" ]; then
  if [ -d "$ROOT/frontend/node_modules" ]; then
    ( cd "$ROOT/frontend" && exec npx vite build --watch --logLevel warn ) &
    FE_PID=$!
    say "前端自动重建已开启：保存 frontend/src 后自动 build 到 frontend/dist（刷新浏览器生效）"
  else
    warn "frontend/node_modules 不存在，跳过前端监听（先 cd frontend && npm install）"
  fi
fi

URL="http://${HOST}:${PORT}"
HEALTH_URL="$URL/api/health"
say "启动后端 $URL  （--reload：改 backend/app 下的 .py 自动重启；Ctrl+C 停止）"
say "API 文档：$URL/docs"
if [ "$OPEN_BROWSER" = "1" ]; then
  ( sleep 2; (command -v open >/dev/null && open "$URL") || (command -v xdg-open >/dev/null && xdg-open "$URL") || true ) &
fi

health_watchdog() {
  # $1 = 被监控的 uvicorn 进程 PID；连续 ~10s 健康检查失败就强杀，交给外层循环重启。
  local target="$1" fails=0
  while kill -0 "$target" 2>/dev/null; do
    sleep 1.5
    if curl -fsS -m 1.5 "$HEALTH_URL" >/dev/null 2>&1; then
      fails=0
    else
      fails=$((fails + 1))
      if [ "$fails" -ge 4 ]; then
        warn "健康检查连续失败，重启后端（进程可能卡死/worker 已退出）"
        : > "$FLAG_FILE"
        kill_tree "$target"
        sleep 1
        kill -KILL "$target" 2>/dev/null || true
        exit 1
      fi
    fi
  done
}

while [ "$STOP" = "0" ]; do
  "$PY" -m uvicorn app.main:app \
    --host "$HOST" --port "$PORT" --ws-per-message-deflate false \
    --reload --reload-dir app &
  UV_PID=$!
  health_watchdog "$UV_PID" &
  WD_PID=$!
  wait "$UV_PID"
  code=$?
  kill "$WD_PID" 2>/dev/null || true
  wait "$WD_PID" 2>/dev/null || true
  WD_PID=""
  [ "$STOP" = "1" ] && break
  # 看门狗杀进程触发的重启：wait 返回 143，不能当作用户主动停止
  if [ -f "$FLAG_FILE" ]; then
    rm -f "$FLAG_FILE"
    warn "看门狗触发重启，1 秒后重新拉起后端…"
    sleep 1
    stop_existing
    continue
  fi
  # 130=SIGINT：用户 Ctrl+C
  if [ "$code" = "130" ]; then
    break
  fi
  warn "后端已退出（code=${code}），1 秒后自动重启…（Ctrl+C 停止）"
  sleep 1
  # 清掉可能残留的监听/worker，避免下次启动端口被占
  stop_existing
done
