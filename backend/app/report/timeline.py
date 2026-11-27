"""运行时间线：把 logcat 与域名抓包按时间窗口归到节点。

logcat 由 `adb logcat -v time` 写出，行首是设备本地时间 `MM-DD HH:MM:SS.mmm`（没有年份/
时区）；这里用后端本地时区补全年份/时区，与 `StepResult.started_at/ended_at`（UTC ISO）比较。
因此要求设备时区与后端一致（同机部署时成立），否则时间线会整体偏移——原始文件仍完整保留。

网络记录来自 `network` JSONL 的 `ts`（后端 `now_iso()`，UTC），可直接比较。

产物：
- `steps`: step_result_id -> {logcat: [...], network: [...]}
- `timeline`: 节点开始/结束 + 全部日志/域名，按时间排序
- `unassigned`: 落在所有节点窗口之外的日志/请求
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# `06-12 10:00:00.000 ...`，logcat -v time / -v threadtime 的前缀一致。
_LOGCAT_TS = re.compile(r"^(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s+(.*)$")
# 两种常见正文：`I/App( 123): msg` 与 `1234  5678 I App : msg`
_LOGCAT_TIME_BODY = re.compile(r"([VDIWEF])/([^(:]+)\(\s*\d+\):\s?(.*)$")
_LOGCAT_THREADTIME_BODY = re.compile(r"\d+\s+\d+\s+([VDIWEF])\s+(.+?)\s*:\s?(.*)$")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _local_tz() -> Any:
    # 后端进程所在时区；logcat 是设备本地时间，同机部署时两者一致。
    return datetime.now().astimezone().tzinfo


def _logcat_datetime(line: str, base: datetime) -> datetime | None:
    match = _LOGCAT_TS.match(line)
    if match is None:
        return None
    month, day, hour, minute, second, millis = (int(part) for part in match.groups()[:6])
    year = base.year
    # 跨年：12 月的日志配到 1 月的运行（或反之）时修正年份。
    if month - base.month > 6:
        year -= 1
    elif base.month - month > 6:
        year += 1
    try:
        return datetime(
            year, month, day, hour, minute, second, millis * 1000, tzinfo=_local_tz()
        )
    except ValueError:
        return None


def _logcat_body(rest: str) -> tuple[str, str, str]:
    """返回 (level, tag, message)；解析不了时 level/tag 为空、message 为整行。"""
    match = _LOGCAT_TIME_BODY.search(rest) or _LOGCAT_THREADTIME_BODY.search(rest)
    if match is None:
        return "", "", rest.strip()
    return match.group(1), match.group(2).strip(), match.group(3).strip()


def read_logcat(path: Path, base: datetime) -> list[dict[str, Any]]:
    """把 logcat 文件转成带绝对时间的条目；续行（堆栈）跟随上一条。"""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:  # pragma: no cover - 读取失败不拖垮报告
        return []
    entries: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip() or line.startswith("--------- beginning of"):
            continue
        at = _logcat_datetime(line, base)
        if at is not None:
            rest = _LOGCAT_TS.match(line).group(7) if _LOGCAT_TS.match(line) else line
            level, tag, message = _logcat_body(rest)
            entries.append(
                {
                    "at": at.timestamp(),
                    "kind": "logcat",
                    "ts": at.isoformat(timespec="milliseconds"),
                    "level": level,
                    "tag": tag,
                    "text": message or line,
                    "raw": line,
                }
            )
        elif entries and (line.startswith((" ", "\t")) or entries[-1]["kind"] == "logcat"):
            # 堆栈等续行：沿用上一条时间，文本追加到上一条（保留完整现场）。
            entries[-1]["text"] = f"{entries[-1]['text']}\n{line}"
            entries[-1]["raw"] = f"{entries[-1]['raw']}\n{line}"
    return entries


def read_network(path: Path) -> list[dict[str, Any]]:
    """读取域名 JSONL；ts 非法/缺失的行跳过。"""
    entries: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:  # pragma: no cover
        return []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        parsed = _parse_iso(record.get("ts"))
        if parsed is None:
            continue
        host = str(record.get("host") or "")
        port = record.get("port")
        scheme = str(record.get("scheme") or "")
        method = str(record.get("method") or "")
        path = str(record.get("path") or "")
        status = record.get("status")
        entry: dict[str, Any] = {
            "at": parsed.timestamp(),
            "kind": "network",
            "ts": parsed.isoformat(timespec="milliseconds"),
            "host": host,
            "port": port,
            "scheme": scheme,
            "text": f"{host}:{port} {scheme}".strip(),
            "raw": record,
        }
        if method:
            entry["method"] = method
        if path:
            entry["path"] = path
        if isinstance(status, int):
            entry["status"] = status
        entries.append(entry)
    return entries


def read_events(events: list[Any]) -> list[dict[str, Any]]:
    """把 run_events 转成时间线条目（操作/状态/日志事件）。"""
    entries: list[dict[str, Any]] = []
    for event in events:
        parsed = _parse_iso(getattr(event, "created_at", None))
        if parsed is None:
            continue
        event_type = str(getattr(event, "type", "") or "event")
        payload = getattr(event, "payload", None)
        text = ""
        if isinstance(payload, dict) and payload:
            try:
                text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):  # pragma: no cover
                text = str(payload)
            if len(text) > 300:
                text = text[:300] + "…"
        entries.append(
            {
                "at": parsed.timestamp(),
                "kind": "event",
                "ts": parsed.isoformat(timespec="milliseconds"),
                "event_type": event_type,
                "text": text or event_type,
                "raw": payload,
            }
        )
    return entries


def _step_windows(
    steps: list[Any],
    run_started: datetime | None,
    run_ended: datetime | None,
) -> list[tuple[Any, datetime, datetime]]:
    windows: list[tuple[Any, datetime, datetime]] = []
    fallback = run_started or datetime.now(UTC)
    for index, step in enumerate(steps):
        start = _parse_iso(getattr(step, "started_at", None)) or fallback
        end = _parse_iso(getattr(step, "ended_at", None))
        if end is None:
            next_started = (
                _parse_iso(getattr(steps[index + 1], "started_at", None))
                if index + 1 < len(steps)
                else None
            )
            end = next_started or run_ended or datetime.now(UTC)
        if end < start:
            end = start
        windows.append((step, start, end))
        fallback = end
    return windows


def build_timeline(
    steps: list[Any],
    logcat_paths: list[Path],
    network_paths: list[Path],
    run_started_at: str | None = None,
    run_ended_at: str | None = None,
    events: list[Any] | None = None,
) -> dict[str, Any]:
    """按节点时间窗归类日志/域名，并生成合并时间线。"""
    run_started = _parse_iso(run_started_at) or datetime.now(UTC)
    run_ended = _parse_iso(run_ended_at)
    base = run_started.astimezone(_local_tz())
    windows = _step_windows(steps, run_started, run_ended)

    entries: list[dict[str, Any]] = []
    for path in logcat_paths:
        entries.extend(read_logcat(path, base))
    for path in network_paths:
        entries.extend(read_network(path))
    entries.extend(read_events(events or []))

    per_step: dict[int, dict[str, list[dict[str, Any]]]] = {
        int(step.id): {"logcat": [], "network": [], "event": []} for step in steps
    }
    unassigned: list[dict[str, Any]] = []
    for event in entries:
        at = datetime.fromtimestamp(float(event["at"]), tz=UTC)
        matched = next(
            (step for step, start, end in windows if start <= at < end),
            None,
        )
        if matched is None:
            unassigned.append(event)
            continue
        event = {
            **event,
            "step_id": matched.step_id,
            "step_name": matched.step_name,
            "step_index": matched.step_index,
        }
        per_step[int(matched.id)].setdefault(str(event["kind"]), []).append(event)

    markers: list[dict[str, Any]] = []
    for step, start, end in windows:
        markers.append(
            {
                "at": start.timestamp(),
                "kind": "step_start",
                "step_id": step.step_id,
                "step_name": step.step_name,
                "step_index": step.step_index,
                "ts": start.astimezone(_local_tz()).isoformat(timespec="milliseconds"),
                "text": f"开始 {step.step_name or step.step_id}",
            }
        )
        markers.append(
            {
                "at": end.timestamp(),
                "kind": "step_end",
                "step_id": step.step_id,
                "step_name": step.step_name,
                "step_index": step.step_index,
                "ts": end.astimezone(_local_tz()).isoformat(timespec="milliseconds"),
                "text": f"结束 {step.step_name or step.step_id}",
            }
        )

    timeline = sorted(
        [*markers, *entries],
        key=lambda item: (float(item["at"]), 0 if item["kind"].startswith("step_") else 1),
    )
    return {
        "steps": per_step,
        "timeline": timeline,
        "unassigned": unassigned,
        "counts": {
            "logcat": sum(1 for event in entries if event["kind"] == "logcat"),
            "network": sum(1 for event in entries if event["kind"] == "network"),
            "event": sum(1 for event in entries if event["kind"] == "event"),
            "assigned": len(entries) - len(unassigned),
            "unassigned": len(unassigned),
        },
        "window": {
            "start": run_started.astimezone(_local_tz()).isoformat(timespec="milliseconds"),
            "end": (run_ended or datetime.now(UTC)).astimezone(_local_tz()).isoformat(timespec="milliseconds"),
        },
    }


__all__ = ["build_timeline", "read_events", "read_logcat", "read_network"]
