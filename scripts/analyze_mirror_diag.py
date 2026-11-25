#!/usr/bin/env python3
"""分析镜像诊断日志（``<data_dir>/diag/mirror-*.jsonl``），给“不跟手/卡顿”下结论。

数据来源（同一个文件，靠 ``source`` 区分）：
- ``client``：浏览器每 5s 上报的渲染 fps / 绘制耗时 / 解码队列 / 丢帧（前端 useMirrorSession 上报）
- ``server``：后端每 10s 打点的出帧率 / 发送队列积压 / 分辨率

用法：
    python scripts/analyze_mirror_diag.py                 # 分析今天最新的文件
    python scripts/analyze_mirror_diag.py --file PATH     # 指定文件
    python scripts/analyze_mirror_diag.py --list          # 列出已有文件（体积/行数）
    python scripts/analyze_mirror_diag.py --session ID    # 只看某次镜像会话
    python scripts/analyze_mirror_diag.py --window 10     # 分段窗口秒数（默认 10）

判读规则（阈值来自实测与 WebCodecs 行为，可在 --help 里看到常量）：
- 解码队列 p95 > 2 或渲染 fps 长期低于出帧 fps 的一半 → 卡解码：降分辨率 / 帧率限到屏幕刷新率；
- 绘制 p95 > 8ms → 卡绘制/GPU 上传：降缩放或降 DPR，必要时把渲染搬到 Worker + OffscreenCanvas；
- 两者都不高但渲染 fps 低 → 主线程争用（Vue/UI/其它定时器），同样走 Worker 方案更划算。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DRAW_MS_WARN = 8.0  # 单帧绘制超过这个值，60fps 的预算（16.7ms）就被吃掉一半
QUEUE_P95_WARN = 2.0  # 解码队列长期 >2 说明解码排队，画面滞后 2 帧以上
RENDER_GAP_WARN = 0.75  # 渲染 < 期望帧率*0.75 才算“跟不上”（rAF 有抖动，留 25% 余量）
ACTIVE_FPS = 10.0  # 出帧低于这个值视为画面基本静止，不参与“跟不跟得上”的判断
DEFAULT_DISPLAY_HZ = 60.0  # 老样本没有 display_hz 时的默认值（笔记本/普通显示器）
RECENT_WINDOW = 10  # 判定只看最近 N 个活跃样本（≈ 最近 1 分钟）

REPO_ROOT = Path(__file__).resolve().parents[1]


def default_diag_dir() -> Path:
    """复用后端的数据目录规则（AUTOTEST_DATA_DIR / ~/data）。"""
    try:
        sys.path.insert(0, str(REPO_ROOT / "backend"))
        from app import config

        # 与后端启动流程一致：配置文件（~/.autotest/config.json）也参与 data_dir 计算，
        # 否则本机把数据目录指到仓库 data/ 时，脚本会去 ~/.autotest/data 找错地方。
        try:
            config.reload_file_layer()
        except Exception as exc:  # noqa: BLE001 - 配置不可读时仍按默认值/环境变量走
            print(f"(warn) 配置文件读取失败，按默认值推断数据目录: {exc}", file=sys.stderr)
        return Path(config.data_dir()) / "diag"
    except Exception:  # noqa: BLE001 - 后端不可导入时回退到仓库 data/
        return REPO_ROOT / "data" / "diag"


def latest_file(directory: Path) -> Path | None:
    files = sorted(directory.glob("mirror-*.jsonl"))
    return files[-1] if files else None


def group_sessions(samples: list[dict]) -> dict[str, list[dict]]:
    """按 session_id 分组（缺 id 的归到 "unknown"）。"""
    groups: dict[str, list[dict]] = {}
    for sample in samples:
        key = str(sample.get("session_id") or "unknown")
        groups.setdefault(key, []).append(sample)
    return groups


def load(path: Path) -> list[dict]:
    samples: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                samples.append(item)
    return samples


def _pct(values: list[float], ratio: float) -> float:
    """线性插值分位数（与常见统计口径一致；nearest-rank 会把 4 个数中位数算成第 3 个）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * ratio
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return ordered[low] * (1 - fraction) + ordered[high] * fraction


def summarize(samples: list[dict]) -> dict:
    """把原始样本压成统计量 + 判定。纯函数，便于测试。

    对齐原则：优先用 **client 样本自带的 server_fps**（浏览器收到的 stats 与渲染 fps 同一时刻），
    而不是后端 10s 打点——后者包含启动瞬间/静止画面的低帧段，会把“设备出帧”拉低成假象。
    """
    client = [s for s in samples if s.get("source") == "client"]
    server = [s for s in samples if s.get("source") == "server"]

    render = [float(s.get("render_fps") or 0) for s in client]
    draw = [float(s.get("draw_ms") or 0) for s in client]
    queue = [float(s.get("queue_size") or 0) for s in client]
    dropped = max((int(s.get("dropped") or 0) for s in client), default=0)

    # 同一时刻对齐的出帧率（client 里带的）；没有时退回后端打点
    aligned = [float(s["server_fps"]) for s in client if s.get("server_fps") is not None]
    stream = aligned or [float(s["server_fps"]) for s in server if s.get("server_fps") is not None]

    # “活跃样本”：出帧 >= ACTIVE_FPS 才算真的在动；静止画面的低帧不能当瓶颈证据。
    # 期望渲染帧率还要被屏幕刷新率封顶：60Hz 屏幕上出帧 120 时，渲染 60 就是到顶，不是瓶颈。
    active = [
        (
            float(s.get("render_fps") or 0),
            float(s["server_fps"]),
            min(float(s["server_fps"]), float(s.get("display_hz") or DEFAULT_DISPLAY_HZ)),
        )
        for s in client
        if s.get("server_fps") is not None and float(s["server_fps"]) >= ACTIVE_FPS
    ]
    slow = [pair for pair in active if pair[0] < pair[2] * RENDER_GAP_WARN]

    # 判定以“最近的样本”为准：中途换浏览器/改参数后，早期差样本不该继续拉低结论。
    recent = active[-RECENT_WINDOW:] if len(active) > RECENT_WINDOW else active
    recent_slow = [pair for pair in recent if pair[0] < pair[2] * RENDER_GAP_WARN]
    earlier = active[:-RECENT_WINDOW] if len(active) > RECENT_WINDOW else []
    earlier_slow = [pair for pair in earlier if pair[0] < pair[2] * RENDER_GAP_WARN]

    verdict, reasons = _verdict(
        render=render,
        draw=draw,
        queue=queue,
        active=recent,
        slow=recent_slow,
        dropped=dropped,
        stream_p50=_pct(stream, 0.5),
    )
    if earlier and earlier_slow and len(earlier_slow) >= max(2, len(earlier) * 0.3):
        reasons.append(
            f"更早的 {len(earlier)} 个活跃样本里有 {len(earlier_slow)} 个跟不上"
            "（换浏览器/改参数前的情况，仅作对比）"
        )

    # 统计量只算“画面在动”的样本：静止时两端都是 0，混进去会把 p50 拉到 0，看着像全崩
    active_render = [pair[0] for pair in active]
    active_stream = [pair[1] for pair in active]
    return {
        "client_samples": len(client),
        "server_samples": len(server),
        "idle_samples": len(render) - len(active),
        "render_fps_p50": _pct(active_render, 0.5),
        "render_fps_p10": _pct(active_render, 0.1),
        "render_fps_p95": _pct(active_render, 0.95),
        "server_fps_p50": _pct(active_stream, 0.5) or _pct(stream, 0.5),
        "draw_ms_p50": _pct(draw, 0.5),
        "draw_ms_p95": _pct(draw, 0.95),
        "queue_p50": _pct(queue, 0.5),
        "queue_p95": _pct(queue, 0.95),
        "queue_max": max(queue) if queue else 0.0,
        "dropped": dropped,
        "display_hz": next((s.get("display_hz") for s in client if s.get("display_hz")), None),
        "active_samples": len(active),
        "slow_samples": len(slow),
        "verdict": verdict,
        "reasons": reasons,
        "render_fps": render,
        "draw_ms": draw,
        "queue": queue,
        "active": active,
        "active_rows": [
            {"ts": s.get("ts", ""), "render": render_fps, "stream": stream, "expected": expected}
            for s in client
            if s.get("server_fps") is not None and float(s["server_fps"]) >= ACTIVE_FPS
            for render_fps in [float(s.get("render_fps") or 0)]
            for stream in [float(s["server_fps"])]
            for expected in [min(stream, float(s.get("display_hz") or DEFAULT_DISPLAY_HZ))]
        ],
    }


def _verdict(
    render: list[float],
    draw: list[float],
    queue: list[float],
    active: list[tuple[float, float, float]],
    slow: list[tuple[float, float, float]],
    dropped: int,
    stream_p50: float,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    draw_p95 = _pct(draw, 0.95)
    queue_p95 = _pct(queue, 0.95)

    if not active:
        if draw_p95 > DRAW_MS_WARN:
            reasons.append(f"绘制 p95={draw_p95:.1f}ms（> {DRAW_MS_WARN:g}ms）→ 单帧绘制吃掉大半预算")
        return ("无有效数据（没有正在变化的画面；请边滑动边测）" if not any(render) else "数据不足（需要边滑动边测）"), reasons

    slow_ratio = len(slow) / len(active)
    decode_backlog = queue_p95 > QUEUE_P95_WARN
    decode_slow = decode_backlog or slow_ratio >= 0.3
    if queue_p95 > QUEUE_P95_WARN:
        reasons.append(f"解码队列 p95={queue_p95:.1f}（> {QUEUE_P95_WARN:g}）→ WebCodecs 排队")
    if slow_ratio >= 0.3:
        worst = min(slow, key=lambda pair: pair[0] / pair[2]) if slow else (0, 1, 1)
        reasons.append(
            f"{len(slow)}/{len(active)} 个活跃样本渲染落后于期望"
            f"（最差 {worst[0]:.0f}fps，出帧 {worst[1]:.0f}fps、期望 {worst[2]:.0f}fps）"
        )
    if draw_p95 > DRAW_MS_WARN:
        reasons.append(f"绘制 p95={draw_p95:.1f}ms（> {DRAW_MS_WARN:g}ms）→ 单帧绘制吃掉大半预算")
    if dropped > 0:
        reasons.append(f"累计丢帧 {dropped}（断链恢复会补一次 IDR，画面短暂停顿）")

    # 绘制耗时是直接证据：只要单帧绘制就吃掉大半预算，慢渲染的锅就在绘制（除非队列也在涨）
    if draw_p95 > DRAW_MS_WARN and not decode_backlog:
        return "绘制瓶颈（GPU 上传/缩放）", reasons
    if decode_slow:
        return "解码瓶颈（浏览器解码吞吐不足）", reasons
    if not reasons:
        return "健康（未发现明显瓶颈）", reasons
    return "轻微问题（见下方明细）", reasons


def analyzer_active() -> float:
    return ACTIVE_FPS


def _fmt(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}"


def print_report(samples: list[dict], path: Path, window_s: int, session: str | None = None) -> None:
    stats = summarize(samples)
    first_ts = samples[0].get("ts", "?") if samples else "?"
    last_ts = samples[-1].get("ts", "?") if samples else "?"
    print(f"文件: {path}" + (f"（会话 {session[:8]}）" if session else "（全部会话）"))
    print(f"样本: client={stats['client_samples']} server={stats['server_samples']}  {first_ts} → {last_ts}")
    print()
    print("关键指标")
    print(f"  渲染 fps（活跃）p10={_fmt(stats['render_fps_p10'], 0)} p50={_fmt(stats['render_fps_p50'], 0)} "
          f"p95={_fmt(stats['render_fps_p95'], 0)}")
    print(f"  设备出帧 fps（活跃）p50={_fmt(stats['server_fps_p50'], 0)}")
    print(f"  空闲样本      {stats['idle_samples']}（画面静止，不计入判定）")
    print(f"  绘制耗时      p50={_fmt(stats['draw_ms_p50'], 2)}ms p95={_fmt(stats['draw_ms_p95'], 2)}ms")
    print(f"  解码队列      p50={_fmt(stats['queue_p50'])} p95={_fmt(stats['queue_p95'])} max={_fmt(stats['queue_max'], 0)}")
    print(f"  累计丢帧      {stats['dropped']}")
    if 0 < stats["active_samples"] < 5:
        print("  ⚠ 活跃样本偏少（<5）：再多滑一会儿结果更稳")
    print(f"  活跃样本      {stats['active_samples']}（出帧 ≥ {analyzer_active():g}；慢样本 {stats['slow_samples']}）")
    print(f"  本机屏幕      {stats.get('display_hz') or '未记录（按 60Hz 估算）'}")
    phases = [str(s.get("phase")) for s in samples if s.get("source") == "client" and s.get("phase")]
    if phases:
        from collections import Counter

        print(f"  页面状态      {dict(Counter(phases))}")
    print()
    if stats["client_samples"] == 0:
        print()
        print("⚠ 这个会话没有浏览器样本（只有后端打点）：页面很可能没进入「镜像中」——")
        print("  例如停在「等待关键帧」、控制台页面没打开/没刷新，或浏览器不支持 WebCodecs。")
        print(f"  设备侧出帧 p50={_pct([float(s['server_fps']) for s in samples if s['source']=='server' and s.get('server_fps') is not None], 0.5):.0f} fps"
              "（这部分是好的）")
    print(f"结论: {stats['verdict']}")
    for reason in stats["reasons"]:
        print(f"  - {reason}")
    if not stats["reasons"]:
        print("  - 渲染跟得上出帧，绘制也便宜；如果仍觉得不跟手，多半是设备端/编码侧（看 server_fps 是否达上限）")

    # 分段：找出最差的时间窗，方便对照当时在做什么
    rows = stats.get("active_rows") or []
    if len(rows) >= 4:
        buckets: dict[str, list[dict]] = {}
        for row in rows:
            key = str(row["ts"])[11:16]  # HH:MM
            buckets.setdefault(key, []).append(row)
        print()
        print("按分钟分段（只看画面在动的样本）")
        print("  时间    样本  渲染中位  期望中位  跟上比例")
        for key in sorted(buckets):
            items = buckets[key]
            renders = [item["render"] for item in items]
            expecteds = [item["expected"] for item in items]
            ratios = [item["render"] / item["expected"] for item in items if item["expected"] > 0]
            print(
                f"  {key}   {len(items):3d}   {_pct(renders, 0.5):6.0f}   {_pct(expecteds, 0.5):6.0f}"
                f"      {_pct(ratios, 0.5):5.0%}"
            )

    if len(stats["active"]) >= 3 and window_s > 0:
        step = max(1, window_s // 5)  # client 每 5s 一条
        ratios = [render / expected for render, _stream, expected in stats["active"] if expected > 0]
        worst = None
        for start in range(0, max(1, len(ratios) - step + 1), step):
            chunk = ratios[start : start + step]
            if len(chunk) < 2:
                continue
            low = min(chunk)
            if worst is None or low < worst[1]:
                worst = (start, low)
        if worst is not None:
            print()
            print(f"最差活跃窗口: 第 {worst[0] * 5}s 起，渲染/期望 = {worst[1]:.0%}（100% 为完全跟得上）")

    display = stats.get("display_hz") or DEFAULT_DISPLAY_HZ
    if stats["server_fps_p50"] > display * 1.2 and stats["verdict"].startswith("健康"):
        print()
        print(
            f"  提示: 出帧 {stats['server_fps_p50']:.0f}fps > 屏幕 {display:.0f}Hz，"
            f"约 {(1 - display / stats['server_fps_p50']) * 100:.0f}% 的解码结果不会被显示；"
            "把帧率限到屏幕刷新率可以省一半解码，观感不变。"
        )

    print()
    print("建议（按报告数据挑选，不必全做）:")
    if stats["verdict"].startswith("解码"):
        print("  1. 帧率限到本机屏幕刷新率（60Hz 屏设 60），可少解码一半")
        print("  2. 缩放降到 70%（598×1334 之类），解码量再降一半")
        print("  3. 不要在 60Hz 屏上开“不限帧率”")
    elif stats["verdict"].startswith("绘制"):
        print("  1. 降低缩放比例（画布本身只有几百像素宽，无需推 2MP）")
        print("  2. 可把渲染搬到 Worker + OffscreenCanvas（需要改代码）")
    elif stats["verdict"].startswith("健康"):
        print("  - 参数合适，保持即可")
    else:
        print("  - 先确认滑动过程中确实有画面变化，再复测")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="分析镜像诊断日志")
    parser.add_argument("--file", help="指定 JSONL 文件（默认取最新的 mirror-*.jsonl）")
    parser.add_argument("--dir", help="诊断目录（默认按后端数据目录规则推断）")
    parser.add_argument("--session", help="只分析某个 session_id")
    parser.add_argument("--all", dest="all_sessions", action="store_true", help="分析文件里的所有会话")
    parser.add_argument("--window", type=int, default=10, help="分段窗口秒数（默认 10）")
    parser.add_argument("--list", action="store_true", help="列出已有的诊断文件")
    args = parser.parse_args(argv)

    directory = Path(args.dir).expanduser() if args.dir else default_diag_dir()
    if args.list:
        files = sorted(directory.glob("mirror-*.jsonl"))
        if not files:
            print(f"没有诊断文件: {directory}")
            return 1
        for path in files:
            lines = sum(1 for _ in path.open("r", encoding="utf-8"))
            print(f"{path.stat().st_size / 1024:9.1f} KB  {lines:6d} 行  {path}")
        return 0

    path = Path(args.file).expanduser() if args.file else latest_file(directory)
    if path is None or not path.exists():
        print(f"没有诊断文件: {directory}\n（先跑一次镜像并操作设备，浏览器每 5s 会上报一条）", file=sys.stderr)
        return 1

    samples = load(path)
    if not samples:
        print(f"{path} 里没有可分析的样本", file=sys.stderr)
        return 1

    # 按镜像会话分组：换分辨率/帧率/浏览器后是新的 session，混在一起会把旧参数期的差数据
    # 算进当前结论。默认只看**最新会话**，其它会话提示用 --session / --all 查看。
    sessions = group_sessions(samples)
    if args.session:
        chosen = args.session
        if chosen not in sessions:
            print(f"没有 session {chosen}；现有：{', '.join(sessions)}", file=sys.stderr)
            return 1
        samples = sessions[chosen]
    elif args.all_sessions:
        pass
    else:
        chosen = max(sessions, key=lambda sid: max(str(s.get("ts", "")) for s in sessions[sid]))
        others = [sid for sid in sessions if sid != chosen]
        if others:
            print(f"(只分析最新会话 {chosen[:8]}；另有 {len(others)} 个历史会话，"
                  f"用 --session <id> 或 --all 查看)")
        samples = sessions[chosen]
    print_report(samples, path, args.window, session=None if args.all_sessions else (args.session or chosen))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
