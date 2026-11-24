#!/usr/bin/env bash
# macOS 双击停止（Finder 里双击本文件即可；也可以继续 Ctrl+C 停前台进程）
cd "$(dirname "$0")"
sh scripts/stop.sh "$@"
code=$?
printf "\n按回车关闭窗口…"
read -r _
exit $code
