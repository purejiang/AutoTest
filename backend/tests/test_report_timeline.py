"""时间线：logcat / 域名记录按节点时间窗归类。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.report.timeline import build_timeline


@dataclass
class _Event:
    created_at: str
    type: str
    payload: dict


@dataclass
class _Step:
    id: int
    step_id: str
    step_name: str
    step_index: int
    started_at: str
    ended_at: str


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds")


def _logcat_line(value: datetime, message: str, level: str = "I") -> str:
    local = value.astimezone()
    return f"{local.strftime('%m-%d %H:%M:%S')}.{local.microsecond // 1000:03d}  1234  5678 {level} App: {message}"


def test_build_timeline_assigns_logcat_and_network_to_steps(tmp_path: Path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    step1 = _Step(1, "s1", "打开应用", 1, _iso(now), _iso(now + timedelta(seconds=2)))
    step2 = _Step(2, "s2", "点击登录", 2, _iso(now + timedelta(seconds=2)), _iso(now + timedelta(seconds=4)))

    logcat = tmp_path / "run.log"
    logcat.write_text(
        "\n".join(
            [
                _logcat_line(now + timedelta(seconds=1), "launch done"),
                _logcat_line(now + timedelta(seconds=3), "click failed", "E"),
                "    at com.example.Login.click(Login.kt:42)",  # 堆栈续行跟随上一条
                _logcat_line(now + timedelta(seconds=10), "after run"),  # 节点外
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    network = tmp_path / "run.jsonl"
    records = [
        (1, 1.5, "api1.example.com"),
        (2, 3.5, "api2.example.com"),
    ]
    network.write_text(
        "\n".join(
            json.dumps(
                {
                    "ts": _iso(now + timedelta(seconds=offset)),
                    "host": host,
                    "port": 443,
                    "scheme": "https",
                }
            )
            for _index, offset, host in records
        )
        + "\n",
        encoding="utf-8",
    )

    result = build_timeline([step1, step2], [logcat], [network], _iso(now), _iso(now + timedelta(seconds=5)))

    assert result["steps"][1]["logcat"][0]["text"] == "launch done"
    assert result["steps"][2]["logcat"][0]["text"].startswith("click failed")
    assert "at com.example.Login.click" in result["steps"][2]["logcat"][0]["text"]
    assert [item["host"] for item in result["steps"][1]["network"]] == ["api1.example.com"]
    assert [item["host"] for item in result["steps"][2]["network"]] == ["api2.example.com"]
    assert result["counts"]["assigned"] == 4  # 3 logcat（续行合并）+ 2 network - 1 节点外
    assert len(result["unassigned"]) == 1

    kinds = [item["kind"] for item in result["timeline"]]
    assert "step_start" in kinds and "step_end" in kinds
    assert kinds.count("logcat") == 3


def test_build_timeline_handles_missing_end_and_bad_lines(tmp_path: Path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    step = _Step(1, "s1", "唯一节点", 1, _iso(now), None)  # type: ignore[arg-type]
    logcat = tmp_path / "run.log"
    logcat.write_text(
        "\n".join(
            [
                "--------- beginning of main",
                _logcat_line(now + timedelta(seconds=1), "ok"),
                "not a logcat line",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = build_timeline([step], [logcat], [], _iso(now), _iso(now + timedelta(seconds=2)))
    # 没有结束时间时用运行结束兜底；非时间行跟随上一行
    assert result["steps"][1]["logcat"][0]["text"].startswith("ok")
    assert "not a logcat line" in result["steps"][1]["logcat"][0]["text"]


def test_build_timeline_includes_run_events(tmp_path: Path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    step = _Step(1, "s1", "点击登录", 1, _iso(now), _iso(now + timedelta(seconds=3)))
    events = [
        _Event(_iso(now + timedelta(seconds=1)), "action_start", {"action": "click"}),
        _Event(_iso(now + timedelta(seconds=2)), "step_finished", {"status": "passed"}),
        _Event(_iso(now + timedelta(seconds=9)), "run_state", {"status": "succeeded"}),
    ]
    result = build_timeline([step], [], [], _iso(now), _iso(now + timedelta(seconds=4)), events)

    assert [item["event_type"] for item in result["steps"][1]["event"]] == [
        "action_start",
        "step_finished",
    ]
    assert result["counts"]["event"] == 3
    assert result["counts"]["assigned"] == 2
    assert len(result["unassigned"]) == 1
    timeline_events = [item for item in result["timeline"] if item["kind"] == "event"]
    assert timeline_events[0]["text"] == '{"action":"click"}'
