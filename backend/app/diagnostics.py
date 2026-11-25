"""镜像诊断指标落盘（JSONL），用于事后分析“不跟手/卡顿”发生在哪一环。

两类记录追加到同一个文件，靠 ``source`` 区分：

- ``source="client"``：浏览器每 5s 上报一次（渲染 fps / 绘制耗时 / 解码队列 / 丢帧 / 分辨率 / 帧率上限）；
- ``source="server"``：后端每 10s 的性能打点（出帧率 / 丢帧 / 订阅者与发送队列积压 / 几何）。

文件：``<data_dir>/diag/mirror-YYYYMMDD.jsonl``（按天切分）。单文件超过 ``MAX_BYTES`` 后
自动换到 ``-2``、``-3``… 后缀，避免长时间开着镜像把磁盘写满；不做自动删除
（``scripts/analyze_mirror_diag.py --list`` 可查看体积），跟 30 天保留策略无关。

写盘是**尽力而为**：任何 IO 异常只记 warning，绝不影响镜像链路。
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config
from .logs import logger

MAX_BYTES = 8 * 1024 * 1024  # 单文件 8MB ≈ 数万行
_LOCK = threading.Lock()


def diag_dir() -> Path:
    path = config.data_dir() / "diag"
    path.mkdir(parents=True, exist_ok=True)
    return path


def current_file(now: datetime | None = None) -> Path:
    """当天文件；超过体积上限时顺延到 -2、-3…（同一天内可多段）。"""
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d")
    base = diag_dir() / f"mirror-{stamp}.jsonl"
    if not base.exists() or base.stat().st_size < MAX_BYTES:
        return base
    index = 2
    while True:
        candidate = diag_dir() / f"mirror-{stamp}-{index}.jsonl"
        if not candidate.exists() or candidate.stat().st_size < MAX_BYTES:
            return candidate
        index += 1


def append_sample(source: str, record: dict[str, Any]) -> Path | None:
    """追加一条 JSONL；返回写入的文件（失败返回 None，不抛）。"""
    payload = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "source": source}
    payload.update(record)
    try:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with _LOCK:
            path = current_file()
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return path
    except Exception as exc:  # noqa: BLE001 - 诊断写盘失败不能影响镜像
        logger.warning("append mirror diag failed: %s", exc)
        return None


def list_files() -> list[dict[str, Any]]:
    """已落盘的诊断文件（体积/行数），供分析脚本 --list 使用。"""
    items: list[dict[str, Any]] = []
    for path in sorted(diag_dir().glob("mirror-*.jsonl")):
        try:
            size = path.stat().st_size
            with path.open("rb") as fh:
                lines = sum(1 for _ in fh)
        except OSError:
            continue
        items.append({"file": str(path), "size": size, "lines": lines})
    return items
