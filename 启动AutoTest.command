#!/usr/bin/env bash
# macOS 双击启动（Finder 里双击本文件即可；关掉终端窗口 = 停止服务）
cd "$(dirname "$0")"
exec sh scripts/start.sh --open
