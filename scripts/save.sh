#!/usr/bin/env bash
# 一键存档：把当前改动提交到本地 git（不联网、不推送）
#
#   sh scripts/save.sh                  # 自动生成提交信息（时间 + 改动文件数）
#   sh scripts/save.sh "改了什么"        # 自定义提交信息
#   sh scripts/save.sh --log            # 只看最近提交，不提交
set -euo pipefail
cd "$(dirname "$0")/.."

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "当前目录不是 git 仓库" >&2
  exit 1
fi

if [ "${1:-}" = "--log" ]; then
  git log --oneline --graph --decorate -20
  exit 0
fi

if [ -z "$(git status --porcelain)" ]; then
  echo "没有需要提交的改动。最近提交："
  git log --oneline -5
  exit 0
fi

MSG="${1:-}"
if [ -z "$MSG" ]; then
  CHANGED=$(git status --porcelain | wc -l | tr -d ' ')
  MSG="chore: 存档 $(date '+%Y-%m-%d %H:%M')（$CHANGED 个文件）"
fi

git add -A
git commit -m "$MSG"
echo
echo "已提交：$MSG"
git log --oneline --graph --decorate -8
echo
echo "提示：查看改动 git diff HEAD~1 ｜ 看历史 git log --oneline ｜ 回退到某提交 git checkout <hash>"
