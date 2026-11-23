#!/usr/bin/env bash
# 一键发版：同步前后端版本号 → 提交 → 打 annotated tag（纯本地仓库）
#
#   sh scripts/release.sh 0.1.1        # 指定版本号（推荐）
#   sh scripts/release.sh              # 自动在当前后端版本上 +1 patch（0.1.0 → 0.1.1）
#   sh scripts/release.sh 0.2.0 --no-tag   # 只改版本号+提交，不打 tag
#   sh scripts/release.sh 0.2.0 --rebuild  # 发版后顺手重建前端产物（dist 不进 git，此步可选）
#
# 版本号同步范围（Vue 主前端）：
#   backend/pyproject.toml  /  backend/app/__init__.py  /  frontend/package.json  /  frontend/package-lock.json
# frontend-react/ 是 v0.1.3 的冻结回滚备份，release 不修改、不同步它的版本号。
# 发版前要求工作区干净（避免把未提交改动卷进 tag）。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

# ---- 参数
ARG_VERSION=""
DO_TAG=1
DO_REBUILD=0
for arg in "$@"; do
  case "$arg" in
    --no-tag) DO_TAG=0 ;;
    --rebuild) DO_REBUILD=1 ;;
    -h|--help)
      awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"
      exit 0
      ;;
    -*)
      echo "[release] 未知参数：$arg" >&2
      exit 2
      ;;
    *)
      if [ -n "$ARG_VERSION" ]; then
        echo "[release] 只能指定一个版本号" >&2
        exit 2
      fi
      ARG_VERSION="$arg"
      ;;
  esac
done

# ---- 版本号：显式指定或自动 +1 patch
current_version() {
  grep -m1 -oE '[0-9]+\.[0-9]+\.[0-9]+' backend/app/__init__.py
}

CURRENT="$(current_version)"
if [ -z "$ARG_VERSION" ]; then
  IFS='.' read -r major minor patch <<<"$CURRENT"
  VERSION="${major}.${minor}.$((patch + 1))"
else
  VERSION="$ARG_VERSION"
fi

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "[release] 版本号必须是 X.Y.Z 形式（收到：${VERSION}）" >&2
  exit 2
fi
TAG="v$VERSION"

# ---- 前置检查
if [ -n "$(git status --porcelain)" ]; then
  echo "[release] 工作区不干净，请先提交或 stash 后再发版：" >&2
  git status --short >&2
  exit 1
fi
if [ "$DO_TAG" = "1" ] && git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "[release] tag $TAG 已存在，换个版本号（当前代码版本 ${CURRENT}）" >&2
  exit 1
fi

echo "==> 发版 ${CURRENT} -> ${VERSION}（tag: $([ "$DO_TAG" = 1 ] && echo "$TAG" || echo "跳过")）"

# ---- 1) 后端版本号（pyproject.toml + app/__init__.py）
python3 - "$VERSION" <<'PY'
import pathlib
import re
import sys

version = sys.argv[1]
root = pathlib.Path(".")
pyproject = root / "backend" / "pyproject.toml"
text = pyproject.read_text(encoding="utf-8")
new_text, count = re.subn(r'(?m)^version = "[^"]+"', f'version = "{version}"', text, count=1)
if count != 1:
    raise SystemExit("backend/pyproject.toml 里没找到 version 字段")
pyproject.write_text(new_text, encoding="utf-8")

init = root / "backend" / "app" / "__init__.py"
text = init.read_text(encoding="utf-8")
new_text, count = re.subn(r'__version__ = "[^"]+"', f'__version__ = "{version}"', text, count=1)
if count != 1:
    raise SystemExit("backend/app/__init__.py 里没找到 __version__")
init.write_text(new_text, encoding="utf-8")
print(f"    后端版本 -> {version}")
PY

# ---- 1.5) 后端锁文件同步（pyproject 版本变了，uv.lock 里的项目版本也要跟着变，
#          否则每次发版后第一次 uv run 都会把工作区弄脏）
if command -v uv >/dev/null 2>&1; then
  (cd backend && uv lock >/dev/null 2>&1) || echo "    [warn] uv lock 失败（离线/网络问题？），锁文件可能滞后" >&2
fi

# ---- 2) 前端版本号（只同步 frontend/：package.json + package-lock.json）
if command -v npm >/dev/null 2>&1; then
  (cd frontend && npm version "$VERSION" --no-git-tag-version --allow-same-version >/dev/null)
  echo "    frontend 版本 -> $VERSION（frontend-react/ 不参与发版）"
else
  echo "[release] 未找到 npm，跳过前端版本号同步（请手动改 frontend/package.json）" >&2
fi

# ---- 3) 提交
git add -A
if git diff --cached --quiet; then
  echo "    版本号无变化，跳过提交"
else
  git commit -qm "chore(release): v$VERSION"
  echo "    已提交：chore(release): v$VERSION"
fi

# ---- 4) 打 tag（annotated，带摘要）
if [ "$DO_TAG" = "1" ]; then
  git tag -a "$TAG" -m "AutoTest $TAG

版本号：后端 $VERSION / 前端 $VERSION
提交：$(git rev-parse --short HEAD)
仓库：本地 git（无远程），备份建议 git bundle create autotest.bundle --all"
  echo "    已打 tag：$TAG"
fi

# ---- 5) 可选：重建前端产物
if [ "$DO_REBUILD" = "1" ]; then
  echo "==> 重建前端产物（Vue，frontend/dist）"
  sh scripts/build.sh
else
  echo "    提示：dist 不进 git；若前端有改动，记得 sh scripts/start.sh --rebuild 或 npm run build"
fi

echo
echo "==> 完成 v$VERSION"
echo "    查看：git tag -l --format='%(refname:short) %(objectname:short)' | tail -3"
echo "    回退：git reset --hard $TAG"
echo "    备份：git bundle create autotest-v$VERSION.bundle --all"
