"""运行事件总线（契约 §5/§14.4）。

职责：
- `emit()`：同一 run 内 seq 单调递增（短事务 SELECT MAX(seq)+1 + insert，唯一约束冲突重试 ≤3），
  落库后推给内存订阅者；
- `subscribe()`：WS 用，返回 asyncio.Queue；投递用订阅者自己的 event loop
  （`loop.call_soon_threadsafe`），因此引擎线程可安全调用；
- `replay()`：断线重连从 DB 补事件；
- `last_seq()`：当前最大 seq（0 表示还没有事件）；
- 无订阅者时 emit 必须照常落库（WS 后连也能 replay 到）。

payload 保证 JSON 可序列化（非标准类型用 str 兜底）。
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .db import session_scope
from .logs import logger
from .models import RunEvent, now_iso

_MAX_SEQ_RETRIES = 3
_DEFAULT_QUEUE_SIZE = 1000


def _jsonable(payload: Any) -> dict[str, Any]:
    """payload 必须是 dict 且可 JSON 序列化；不可序列化的值降级为 str。"""
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        payload = {"value": payload}
    try:
        json.dumps(payload)
        return payload
    except (TypeError, ValueError):
        return json.loads(json.dumps(payload, default=str))


def _safe_put(queue: asyncio.Queue, frame: dict[str, Any]) -> None:
    """在订阅者的 loop 内执行；队列满时丢最旧帧，保证 WS 不反压引擎。"""
    try:
        queue.put_nowait(frame)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(frame)
        except asyncio.QueueFull:  # pragma: no cover - 极端并发
            pass


class RunEventBus:
    """进程内事件总线 + DB 持久化。所有方法线程安全。"""

    _lock = threading.RLock()
    # run_id -> {id(queue): (loop, queue)}；用 queue 对象身份做键，unsubscribe 按身份查找
    _subscribers: dict[int, dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}

    # ------------------------------------------------------------ emit / replay
    @staticmethod
    def emit(run_id: int, type: str, payload: dict[str, Any] | None = None) -> int:
        """写入一条事件并广播，返回分配的 seq。"""
        run_id = int(run_id)
        event_type = str(type)
        safe_payload = _jsonable(payload)
        last_error: Exception | None = None
        for attempt in range(_MAX_SEQ_RETRIES):
            try:
                with session_scope() as session:
                    current = session.execute(
                        select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id)
                    ).scalar()
                    seq = int(current or 0) + 1
                    created_at = now_iso()
                    session.add(
                        RunEvent(
                            run_id=run_id,
                            seq=seq,
                            type=event_type,
                            payload=safe_payload,
                            created_at=created_at,
                        )
                    )
                    session.flush()
            except IntegrityError as exc:  # (run_id, seq) 撞了：重新取 MAX 再来
                last_error = exc
                if attempt == _MAX_SEQ_RETRIES - 1:
                    logger.warning("event seq conflict run=%s retry=%s: %s", run_id, attempt + 1, exc)
                continue
            frame = {
                "kind": "event",
                "seq": seq,
                "type": event_type,
                "ts": created_at,
                "payload": safe_payload,
            }
            RunEventBus._publish(run_id, frame)
            return seq
        raise RuntimeError(f"failed to emit event for run {run_id}") from last_error

    @staticmethod
    def replay(run_id: int, since_seq: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
        """按 seq 升序返回 since_seq 之后的历史事件。"""
        with session_scope() as session:
            stmt = (
                select(RunEvent)
                .where(RunEvent.run_id == int(run_id), RunEvent.seq > int(since_seq or 0))
                .order_by(RunEvent.seq)
            )
            if limit is not None and int(limit) > 0:
                stmt = stmt.limit(int(limit))
            rows = session.execute(stmt).scalars().all()
            return [
                {
                    "seq": int(row.seq),
                    "type": row.type,
                    "payload": row.payload or {},
                    "created_at": row.created_at,
                    "ts": row.created_at,
                }
                for row in rows
            ]

    @staticmethod
    def last_seq(run_id: int) -> int:
        with session_scope() as session:
            current = session.execute(
                select(func.max(RunEvent.seq)).where(RunEvent.run_id == int(run_id))
            ).scalar()
            return int(current or 0)

    # ------------------------------------------------------------ pubsub
    @staticmethod
    def subscribe(run_id: int, maxsize: int = _DEFAULT_QUEUE_SIZE) -> asyncio.Queue:
        """订阅实时事件；必须在有 running loop 的线程（WS handler）里调用。"""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with RunEventBus._lock:
            RunEventBus._subscribers.setdefault(int(run_id), {})[id(queue)] = (loop, queue)
        return queue

    @staticmethod
    def unsubscribe(run_id: int, queue: asyncio.Queue) -> None:
        with RunEventBus._lock:
            bucket = RunEventBus._subscribers.get(int(run_id))
            if not bucket:
                return
            bucket.pop(id(queue), None)
            if not bucket:
                RunEventBus._subscribers.pop(int(run_id), None)

    @staticmethod
    def subscriber_count(run_id: int) -> int:
        with RunEventBus._lock:
            return len(RunEventBus._subscribers.get(int(run_id), {}))

    @staticmethod
    def drop_subscribers(run_id: int) -> None:
        """run 被删除时清掉内存订阅（WS 连接会自行在写失败/断开时退出）。"""
        with RunEventBus._lock:
            RunEventBus._subscribers.pop(int(run_id), None)

    @staticmethod
    def _publish(run_id: int, frame: dict[str, Any]) -> None:
        with RunEventBus._lock:
            targets = list(RunEventBus._subscribers.get(int(run_id), {}).values())
        for loop, queue in targets:
            try:
                if loop.is_closed():
                    continue
                loop.call_soon_threadsafe(_safe_put, queue, frame)
            except RuntimeError:  # loop 已关闭（客户端刚好断开）
                continue

    # ------------------------------------------------------------ tests
    @staticmethod
    def reset() -> None:
        """清空内存订阅（测试用；DB 事件由 temp_db 隔离）。"""
        with RunEventBus._lock:
            RunEventBus._subscribers.clear()
