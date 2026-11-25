#!/usr/bin/env python3
"""下载并校验 scrcpy-server（契约 §10）。

scrcpy-server 是 Android 端的 dex jar，由 adb push 到手机后通过 app_process 拉起，
视频经 localabstract socket 回传给后端。仓库不提交 jar，首次使用前运行本脚本：

    python scripts/fetch_scrcpy.py            # 缺文件/损坏时下载（幂等）
    python scripts/fetch_scrcpy.py --check    # 只校验现有文件，不联网
    python scripts/fetch_scrcpy.py --force    # 强制重新下载

产物：
    backend/vendor/scrcpy-server-v5.0.1.jar
    backend/vendor/scrcpy.lock.json   # {version,url,asset_name,local_name,sha256,size,fetched_at}

下载源（国内直连 GitHub 经常超时，按顺序回退）：
    1. 官方 GitHub Release（正式来源）
    2. gh-proxy.com / ghfast.top / ghproxy.net（GitHub 反代，URL 前缀式）
镜像只是通道，**内容校验不变**：下载完必须与 ``OFFICIAL_SHA256``（官方 release 资产摘要）一致，
不一致立即删除并换下一个源——镜像不可信也不能污染 vendor jar。

可用环境变量：
    AUTOTEST_SCRCPY_URL   指定唯一下载地址（公司代理/离线镜像站），设置后不再尝试其它源
    AUTOTEST_SCRCPY_JAR   覆盖 jar 目标路径（lock 文件放在 jar 同目录），便于测试注入

注意：GitHub release 资产名是 ``scrcpy-server-v5.0.1``（**没有** .jar 后缀），
本地保存为 ``scrcpy-server-v5.0.1.jar``（扩展名只是方便识别，jar 内容一致）。
urllib 默认 HTTP/1.1，不会触发 GitHub 的 HTTP/2 framing 问题。

约定：
- 下载失败**绝不**伪造成功，打印清晰错误并非零退出；
- 不做任何解压/执行动作；SHA256 由 backend/app/android/scrcpy.py 启动前复核。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

VERSION = "5.0.1"
# GitHub release 资产名**没有** .jar 后缀；下载后本地保存为 .jar 供 adb push / 校验
ASSET_NAME = f"scrcpy-server-v{VERSION}"
JAR_NAME = f"{ASSET_NAME}.jar"
LOCK_NAME = "scrcpy.lock.json"
URL = f"https://github.com/Genymobile/scrcpy/releases/download/v{VERSION}/{ASSET_NAME}"
# 官方 release API 的 assets[].digest（仅作参考展示；lock 文件以实际下载校验值为准）
OFFICIAL_SHA256 = "764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536"

# 下载源：官方优先，失败按顺序回退到 GitHub 反代（国内直连 GitHub 常被卡）。
# 每一份下载都必须通过 OFFICIAL_SHA256 校验，镜像无法替换内容。
MIRRORS: list[tuple[str, str]] = [
    ("GitHub releases", URL),
    ("gh-proxy.com", f"https://gh-proxy.com/{URL}"),
    ("ghfast.top", f"https://ghfast.top/{URL}"),
    ("ghproxy.net", f"https://ghproxy.net/{URL}"),
]

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = REPO_ROOT / "backend" / "vendor"

USER_AGENT = "AutoTest-fetch-scrcpy/1.0 (+https://github.com/Genymobile/scrcpy)"
TIMEOUT_S = 30
RETRIES = 2  # 失败后最多再重试 2 次（总尝试 3 次）
CHUNK = 1024 * 256


class DownloadError(Exception):
    """所有下载源都失败（或内容校验不通过）。``errors`` 是逐源失败原因。"""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors) or "no download source available")
        self.errors = errors


# ------------------------------------------------------------------ paths
def target_jar() -> Path:
    override = os.environ.get("AUTOTEST_SCRCPY_JAR", "").strip()
    if override:
        return Path(override).expanduser()
    return VENDOR_DIR / JAR_NAME


def lock_path(jar: Path | None = None) -> Path:
    jar = jar or target_jar()
    return jar.parent / LOCK_NAME


# ------------------------------------------------------------------ helpers
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_lock(jar: Path | None = None) -> dict | None:
    path = lock_path(jar)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def verify_existing(jar: Path) -> tuple[bool, str]:
    """校验 jar 是否与 lock 记录一致。返回 (ok, message)。"""
    if not jar.exists():
        return False, f"jar not found: {jar} (run: python scripts/fetch_scrcpy.py)"
    lock = load_lock(jar)
    if lock is None:
        return False, f"lock file missing or unreadable: {lock_path(jar)} (run with --force)"
    expected = str(lock.get("sha256") or "")
    expected_size = lock.get("size")
    if not expected:
        return False, f"lock file has no sha256: {lock_path(jar)} (run with --force)"
    if expected_size is not None and jar.stat().st_size != int(expected_size):
        return False, (
            f"size mismatch: {jar} is {jar.stat().st_size} bytes, "
            f"lock says {expected_size} (run with --force)"
        )
    actual = sha256_file(jar)
    if actual != expected:
        return False, (
            f"sha256 mismatch for {jar}\n  expected: {expected}\n  actual:   {actual}\n"
            "(file corrupted; run with --force)"
        )
    return True, f"ok: {jar} sha256={actual} size={expected_size} version={lock.get('version')}"


def _download_once(url: str, dest: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/octet-stream",
        },
    )
    tmp = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise OSError(f"unexpected HTTP status {status}")
            total = 0
            with tmp.open("wb") as fh:
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    total += len(chunk)
        if total <= 0:
            raise OSError("downloaded 0 bytes")
        tmp.replace(dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def download(url: str, dest: Path, retries: int = RETRIES) -> None:
    """带 UA/超时/重试的下载；全部失败时抛最后一个异常（由 main 打错误退出）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if attempt:
                print(f"retrying ({attempt}/{retries}) ...", file=sys.stderr)
            print(f"downloading {url}")
            _download_once(url, dest)
            return
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.0 + attempt)
    assert last_error is not None
    raise last_error


def candidate_sources() -> list[tuple[str, str]]:
    """(名称, URL) 列表：``AUTOTEST_SCRCPY_URL`` 设置时只用它，否则官方 + 镜像按序回退。"""
    override = os.environ.get("AUTOTEST_SCRCPY_URL", "").strip()
    if override:
        return [("AUTOTEST_SCRCPY_URL", override)]
    return list(MIRRORS)


def download_verified(dest: Path) -> dict:
    """按序尝试各下载源，**每一份都必须**与官方 SHA256 一致才采用。

    镜像只当通道用：内容对不上就删掉并换下一个源，最后仍失败则抛 ``DownloadError``
    （带每个源的失败原因，方便判断是网络问题还是内容问题）。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    for name, url in candidate_sources():
        print(f"downloading ({name}) {url}")
        try:
            # 官方源多试几次；镜像失败得快一点，避免国内网络下每个镜像都卡满超时
            download(url, dest, retries=RETRIES if name == "GitHub releases" else 0)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"  failed: {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        digest = sha256_file(dest)
        if digest != OFFICIAL_SHA256:
            # 内容不对：可能是镜像返回了错误页/被篡改，宁可失败也不能落盘
            errors.append(f"{name}: sha256 mismatch (got {digest}, expected {OFFICIAL_SHA256})")
            print(f"  rejected: sha256 mismatch from {name}", file=sys.stderr)
            try:
                dest.unlink()
            except OSError:
                pass
            continue
        return {"source": name, "url": url, "sha256": digest}
    raise DownloadError(errors)


def write_lock(jar: Path, source: dict | None = None) -> dict:
    digest = sha256_file(jar)
    lock = {
        "version": VERSION,
        "url": URL,
        "asset_name": ASSET_NAME,
        "local_name": JAR_NAME,
        "sha256": digest,
        "size": jar.stat().st_size,
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "verified_against": "GitHub release API digest field (assets[].digest)",
    }
    if source:
        # 记录实际用的下载通道（镜像只影响传输，不影响 sha256）
        lock["downloaded_from"] = source.get("url", "")
        lock["download_source"] = source.get("source", "")
    lock_file = lock_path(jar)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return lock


# ------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch + verify scrcpy-server for AutoTest")
    parser.add_argument("--check", action="store_true", help="only verify the existing jar (no network)")
    parser.add_argument("--force", action="store_true", help="re-download even if the jar looks valid")
    args = parser.parse_args(argv)

    jar = target_jar()

    if args.check:
        ok, message = verify_existing(jar)
        print(message)
        return 0 if ok else 1

    if not args.force:
        ok, message = verify_existing(jar)
        if ok:
            print(f"up to date: {message}")
            return 0

    try:
        source = download_verified(jar)
    except DownloadError as exc:
        detail = "\n".join(f"  - {item}" for item in exc.errors) or f"  - {exc}"
        print(
            f"ERROR: failed to download scrcpy-server v{VERSION}（所有下载源都失败）\n"
            f"{detail}\n"
            "  hint: 国内直连 GitHub 常超时，可用反代前缀自行下载，例如\n"
            f"        https://gh-proxy.com/{URL}\n"
            "        或设置 AUTOTEST_SCRCPY_URL 指向你信任的镜像；把文件放到 "
            f"{jar} 后运行 --check 校验。\n"
            f"        官方 SHA256 必须是 {OFFICIAL_SHA256}。",
            file=sys.stderr,
        )
        return 1

    lock = write_lock(jar, source)
    print(f"saved: {jar} ({lock['size']} bytes) via {lock.get('download_source') or 'unknown'}")
    print(f"sha256: {lock['sha256']}")
    print(f"lock:  {lock_path(jar)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
