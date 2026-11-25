"""分析脚本的判定逻辑（scripts/analyze_mirror_diag.py）：纯函数、不依赖浏览器。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "analyze_mirror_diag.py"


def load_module():
    spec = importlib.util.spec_from_file_location("analyze_mirror_diag", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


analyzer = load_module()


def client_sample(**overrides) -> dict:
    sample = {
        "source": "client",
        "serial": "s",
        "session_id": "sess",
        "render_fps": 58.0,
        "server_fps": 60.0,
        "draw_ms": 2.0,
        "queue_size": 0,
        "dropped": 0,
        "max_fps": 60,
    }
    sample.update(overrides)
    return sample


def server_sample(**overrides) -> dict:
    sample = {"source": "server", "serial": "s", "session_id": "sess", "server_fps": 60.0}
    sample.update(overrides)
    return sample


def test_healthy_pipeline_reports_healthy() -> None:
    samples = [client_sample() for _ in range(6)] + [server_sample() for _ in range(3)]
    stats = analyzer.summarize(samples)
    assert stats["verdict"].startswith("健康"), stats
    assert stats["reasons"] == []


def test_decode_bound_render_far_below_stream() -> None:
    """120fps 推流、浏览器只画出 50fps 且解码队列排队 → 判定解码瓶颈。"""
    samples = [client_sample(render_fps=50, server_fps=120, queue_size=5) for _ in range(6)]
    samples += [server_sample(server_fps=120) for _ in range(3)]
    stats = analyzer.summarize(samples)
    assert stats["verdict"].startswith("解码"), stats
    assert any("解码队列" in reason for reason in stats["reasons"])


def test_draw_bound_when_draw_ms_is_high() -> None:
    samples = [client_sample(render_fps=40, server_fps=60, draw_ms=12.5, queue_size=0) for _ in range(5)]
    stats = analyzer.summarize(samples)
    assert stats["verdict"].startswith("绘制"), stats
    assert any("绘制 p95" in reason for reason in stats["reasons"])


def test_static_screen_is_not_reported_as_bottleneck() -> None:
    """画面静止时两端都是 0：不能误报成“解码瓶颈”。"""
    samples = [client_sample(render_fps=0, server_fps=0) for _ in range(4)]
    stats = analyzer.summarize(samples)
    assert stats["verdict"].startswith("无有效数据"), stats


def test_dropped_frames_surface_in_reasons() -> None:
    samples = [client_sample(dropped=7) for _ in range(3)]
    stats = analyzer.summarize(samples)
    assert stats["dropped"] == 7
    assert any("丢帧 7" in reason for reason in stats["reasons"])


def test_percentile_handles_empty_and_single() -> None:
    assert analyzer._pct([], 0.5) == 0.0
    assert analyzer._pct([4.0], 0.95) == 4.0
    assert analyzer._pct([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5


def test_idle_server_samples_do_not_skew_stream_fps() -> None:
    """启动瞬间/静止画面的低帧样本不能把“设备出帧”拉低成假象：优先用 client 对齐的 server_fps。"""
    samples = [client_sample(render_fps=58, server_fps=60) for _ in range(4)]
    samples += [server_sample(server_fps=1.0), server_sample(server_fps=1.0)]
    stats = analyzer.summarize(samples)
    assert stats["server_fps_p50"] == 60, stats
    assert stats["verdict"].startswith("健康"), stats


def test_slow_render_without_queue_still_flags_decode() -> None:
    """解码器队列读数为 0 但渲染长期只有出帧的一半 → 仍判定解码/合成跟不上。"""
    samples = [client_sample(render_fps=30, server_fps=60, queue_size=0) for _ in range(5)]
    stats = analyzer.summarize(samples)
    assert stats["verdict"].startswith("解码"), stats
    assert stats["slow_samples"] == 5


def test_60hz_display_caps_expectation() -> None:
    """60Hz 屏幕上推流 120fps 时渲染到 60 就是到顶，不能判成瓶颈（分析脚本曾经的误判）。"""
    samples = [
        client_sample(render_fps=58, server_fps=120, display_hz=60, queue_size=0, draw_ms=0.4)
        for _ in range(6)
    ]
    stats = analyzer.summarize(samples)
    assert stats["verdict"].startswith("健康"), stats
    assert stats["slow_samples"] == 0


def test_high_refresh_display_uses_stream_as_expectation() -> None:
    """120Hz 屏幕上推流 120，渲染只有 60 → 真跟不上，应判解码瓶颈。"""
    samples = [
        client_sample(render_fps=60, server_fps=120, display_hz=120, queue_size=0, draw_ms=0.4)
        for _ in range(6)
    ]
    stats = analyzer.summarize(samples)
    assert stats["verdict"].startswith("解码"), stats
