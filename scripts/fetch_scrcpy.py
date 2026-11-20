#!/usr/bin/env python3
"""下载并校验 scrcpy-server（契约 §10）。

scrcpy-server 是 Android 端的 dex jar，由 adb push 到手机后通过 app_process 拉起，
视频经 localabstract socket 回传给后端。仓库不提交 jar，首次使用前运行本脚本：

    python scripts/fetch_scrcpy.py            # 缺文件/损坏时下载（幂等）
    python scripts/fetch_scrcpy.py --check    # 只校验现有文件，不联网
    python scripts/fetch_scrcpy.py --force    # 强制重新下载

产物：
    backend/vendor/scrcpy-server-v5.0.1.jar
    backend/vendor/scrcpy.lock.json   # {version,url,sha256,size,fetched_at}

约定：
- 只允许从官方 GitHub Release 下载；下载失败**绝不**伪造成功，打印清晰错误并非零退出。
- 环境变量 ``AUTOTEST_SCRCPY_JAR`` 可覆盖 jar 目标路径（lock 文件放在 jar 同目录），
  便于离线环境/测试注入，正式环境不要设置。
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
JAR_NAME = f"scrcpy-server-v{VERSION}.jar"
LOCK_NAME = "scrcpy.lock.json"
URL = f"https://github.com/Genymobile/scrcpy/releases/download/v{VERSION}/{JAR_NAME}"

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = REPO_ROOT / "backend" / "vendor"

USER_AGENT = "AutoTest-fetch-scrcpy/1.0 (+https://github.com/Genymobile/scrcpy)"
TIMEOUT_S = 30
RETRIES = 2  # 失败后最多再重试 2 次（总尝试 3 次）
CHUNK = 1024 * 256


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


def write_lock(jar: Path) -> dict:
    digest = sha256_file(jar)
    lock = {
        "version": VERSION,
        "url": URL,
        "sha256": digest,
        "size": jar.stat().st_size,
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
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
        download(URL, jar)
    except Exception as exc:  # noqa: BLE001 - 对外只暴露一行清晰错误
        print(
            f"ERROR: failed to download scrcpy-server v{VERSION} from {URL}\n"
            f"  reason: {type(exc).__name__}: {exc}\n"
            "  hint: check network/proxy, or download it manually and place it at "
            f"{jar}\n"
            "        (then write the lock with: python scripts/fetch_scrcpy.py --check "
            "after adding sha256 manually, or rerun --force)",
            file=sys.stderr,
        )
        return 1

    lock = write_lock(jar)
    print(f"saved: {jar} ({lock['size']} bytes)")
    print(f"sha256: {lock['sha256']}")
    print(f"lock:  {lock_path(jar)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
