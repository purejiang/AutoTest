#!/usr/bin/env bash
# 停止 AutoTest（生产或开发模式的后端；可选同时停 Vite）。
#
#   sh scripts/stop.sh                 # 停止 8787 上的 AutoTest（AUTOTEST_PORT 可覆盖）
#   sh scripts/stop.sh --port 9000     # 停止指定端口
#   sh scripts/stop.sh --dev           # 同时停止开发前端 5173
#   sh scripts/stop.sh --dry-run       # 只打印将要停止的 PID，不实际停止
#
# 优先按端口找监听进程，并校验它确实是本项目的 AutoTest/uvicorn/Vite 进程，
# 避免误杀占用同一端口的其它服务。也可以继续用 Ctrl+C 停前台进程。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PORT="${AUTOTEST_PORT:-8787}"
DEV=0
DRY=0
FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --port|-p) shift; PORT="${1:-}" ;;
    --dev) DEV=1 ;;
    --dry-run|-n) DRY=1 ;;
    --force|-f) FORCE=1 ;;
    -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
    *) echo "[stop] 未知参数: $1（用 --help 查看）" >&2; exit 2 ;;
  esac
  shift
done

case "$PORT" in
  ''|*[!0-9]*) echo "[stop] 端口非法: $PORT" >&2; exit 2 ;;
esac

say() { printf '\033[1;36m[stop]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[stop]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[stop]\033[0m %s\n' "$*" >&2; exit 1; }

# ---- 找监听端口的所有 PID（macOS/Linux 常见工具都兼容）
pids_for_port() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
    return 0
  fi
  if command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "$port" 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+$' || true
    return 0
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -ltnp 2>/dev/null | awk -v p=":$port" '
      $4 ~ p"$" {
        line=$0
        while (match(line, /pid=[0-9]+/)) {
          print substr(line, RSTART+4, RLENGTH-4)
          line=substr(line, RSTART+RLENGTH)
        }
      }' || true
    return 0
  fi
  return 1
}

# 进程命令行（ps 可能被环境禁用，失败时返回空，后面用 cwd 兜底）
process_cmd() {
  ps -p "$1" -o command= 2>/dev/null || true
}

process_cwd() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1 || true
  fi
}

is_autotest_pid() {
  local pid="$1" kind="$2" cmd cwd
  cmd="$(process_cmd "$pid")"
  case "$cmd" in
    *app.main:app*|*autotest*) return 0 ;;
  esac
  if [ "$kind" = "vite" ]; then
    case "$cmd" in *vite*) return 0 ;; esac
  fi
  cwd="$(process_cwd "$pid")"
  case "$cwd" in
    "$ROOT"|"$ROOT/backend")
      [ "$kind" != "vite" ] && return 0
      ;;
    "$ROOT/frontend"|"$ROOT/frontend-react")
      [ "$kind" = "vite" ] && return 0
      ;;
  esac
  return 1
}

parent_of() {
  ps -p "$1" -o ppid= 2>/dev/null | tr -d ' ' || true
}

TARGETS=""
add_target() {
  local pid="$1"
  [ -n "$pid" ] || return 0
  case " $TARGETS " in *" $pid "*) return 0 ;; esac
  TARGETS="$TARGETS $pid"
}

collect_port() {
  local port="$1" kind="$2" label="$3" pids pid cmd
  if ! pids="$(pids_for_port "$port")"; then
    die "找不到 lsof/fuser/ss，无法按端口查找进程；请用 Ctrl+C 或手动 kill"
  fi
  if [ -z "$pids" ]; then
    say "端口 $port 上没有运行中的$label"
    return 0
  fi
  for pid in $pids; do
    if is_autotest_pid "$pid" "$kind"; then
      add_target "$pid"
      # uvicorn --reload / npm 包装进程：把已识别的父进程也纳入停止范围
      local parent
      parent="$(parent_of "$pid")"
      if [ -n "$parent" ] && [ "$parent" != "1" ] && is_autotest_pid "$parent" "$kind"; then
        add_target "$parent"
      fi
    elif [ "$FORCE" = "1" ]; then
      warn "PID $pid 不是 AutoTest 进程，--force 仍将停止：$(process_cmd "$pid")"
      add_target "$pid"
    else
      warn "跳过 PID $pid（不是本项目的 AutoTest/Vite 进程）：$(process_cmd "$pid")"
    fi
  done
}

collect_port "$PORT" "backend" " AutoTest 服务"

if [ "$DEV" = "1" ]; then
  collect_port 5173 "vite" "开发前端（Vite）"
fi

if [ -z "$TARGETS" ]; then
  say "没有找到可停止的 AutoTest 进程（端口 ${PORT}$([ "$DEV" = 1 ] && echo ' / 5173')）"
  exit 0
fi

if [ "$DRY" = "1" ]; then
  say "dry-run：将停止 PID:$(printf ' %s' $TARGETS)"
  exit 0
fi

say "停止 PID:$(printf ' %s' $TARGETS) …"
for pid in $TARGETS; do
  kill -TERM "$pid" 2>/dev/null || true
done

# 最多等 5s，仍未退出则 SIGKILL
remaining=0
for _ in $(seq 1 50); do
  remaining=0
  for pid in $TARGETS; do
    if kill -0 "$pid" 2>/dev/null; then
      remaining=1
      break
    fi
  done
  [ "$remaining" = "0" ] && break
  sleep 0.1
done

if [ "$remaining" = "1" ]; then
  warn "有进程未响应 TERM，强制结束…"
  for pid in $TARGETS; do
    kill -KILL "$pid" 2>/dev/null || true
  done
fi

say "已停止。"
