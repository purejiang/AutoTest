"""无网络测试：scripts/fetch_scrcpy.py 的 --check 行为。

只使用本地临时 jar/lock（``AUTOTEST_SCRCPY_JAR`` 覆盖目标路径），绝不触发下载。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fetch_scrcpy.py"
VERSION = "5.0.1"
JAR_NAME = f"scrcpy-server-v{VERSION}.jar"


def run_fetch(args: list[str], jar_path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AUTOTEST_SCRCPY_JAR"] = str(jar_path)
    env.pop("AUTOTEST_SCRCPY_SKIP_VERIFY", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def write_valid_jar(tmp_path: Path, data: bytes = b"scrcpy-server-bytes") -> tuple[Path, dict]:
    jar = tmp_path / JAR_NAME
    jar.write_bytes(data)
    lock = {
        "version": VERSION,
        "url": f"https://example.invalid/{JAR_NAME}",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "fetched_at": "2026-01-01T00:00:00+00:00",
    }
    (tmp_path / "scrcpy.lock.json").write_text(json.dumps(lock), encoding="utf-8")
    return jar, lock


def test_check_missing_jar_returns_nonzero(tmp_path) -> None:
    missing = tmp_path / JAR_NAME
    result = run_fetch(["--check"], missing)
    assert result.returncode != 0
    output = (result.stdout + result.stderr).lower()
    assert "not found" in output
    assert "missing.jar" not in output  # 打印的是目标路径，不是占位名


def test_check_valid_jar_returns_zero(tmp_path) -> None:
    jar, lock = write_valid_jar(tmp_path)
    result = run_fetch(["--check"], jar)
    assert result.returncode == 0, result.stdout + result.stderr
    assert lock["sha256"] in result.stdout
    assert "ok" in result.stdout.lower()


def test_check_sha_mismatch_returns_nonzero(tmp_path) -> None:
    jar, _lock = write_valid_jar(tmp_path)
    jar.write_bytes(b"tampered bytes")
    result = run_fetch(["--check"], jar)
    assert result.returncode != 0
    output = (result.stdout + result.stderr).lower()
    assert "sha256 mismatch" in output


def test_check_missing_lock_returns_nonzero(tmp_path) -> None:
    jar = tmp_path / JAR_NAME
    jar.write_bytes(b"bytes")
    result = run_fetch(["--check"], jar)
    assert result.returncode != 0
    output = (result.stdout + result.stderr).lower()
    assert "lock" in output


def test_check_size_mismatch_returns_nonzero(tmp_path) -> None:
    jar, lock = write_valid_jar(tmp_path)
    lock["size"] = len(b"scrcpy-server-bytes") + 1
    (tmp_path / "scrcpy.lock.json").write_text(json.dumps(lock), encoding="utf-8")
    result = run_fetch(["--check"], jar)
    assert result.returncode != 0
    assert "size mismatch" in (result.stdout + result.stderr).lower()
