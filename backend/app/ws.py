"""运行事件 WebSocket：`/ws/runs/{run_id}?since_seq=0`（契约 §6.1）。

连接后：
1) `{"kind":"state","run_id":..,"status":..,"last_seq":..}`
2) 补发 since_seq 之后的历史事件（DB replay）
3) 实时转发内存 pubsub 的新事件
client → server：`{"kind":"ping"}` → `{"kind":"pong"}`；`{"kind":"ack","last_seq":n}` 仅记 meta。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .db import session_scope
from .events import RunEventBus
from .logs import logger
from .models import Run

router = APIRouter()

_ACK_LOCK = threading.Lock()
_ACKS: dict[int, int] = {}
_REPLAY_LIMIT = 1000


def last_ack(run_id: int) -> int:
    with _ACK_LOCK:
        return int(_ACKS.get(int(run_id), 0))


def reset_acks() -> None:
    with _ACK_LOCK:
        _ACKS.clear()


def _run_status(run_id: int) -> str | None:
    try:
        with session_scope() as session:
            run = session.get(Run, int(run_id))
            return None if run is None else str(run.status)
    except Exception:  # DB 未初始化等场景：按不存在处理
        logger.exception("failed to read run %s status for ws", run_id)
        return None


def _event_frame(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "event",
        "seq": int(event.get("seq", 0)),
        "type": event.get("type", ""),
        "ts": event.get("ts") or event.get("created_at") or "",
        "payload": event.get("payload") or {},
    }


@router.websocket("/ws/runs/{run_id}")
async def run_events_ws(websocket: WebSocket, run_id: int, since_seq: int = 0) -> None:
    await websocket.accept()
    status = _run_status(run_id)
    if status is None:
        with contextlib.suppress(Exception):
            await websocket.send_json(
                {"kind": "error", "code": "NOT_FOUND", "message": f"run {run_id} not found"}
            )
            await websocket.close(code=1008)
        return

    # 先订阅再 replay：避免 replay 与订阅之间的事件丢失；转发时用 seq 去重
    queue = RunEventBus.subscribe(run_id)
    get_task: asyncio.Task | None = None
    recv_task: asyncio.Task | None = None
    try:
        await websocket.send_json(
            {
                "kind": "state",
                "run_id": int(run_id),
                "status": status,
                "last_seq": RunEventBus.last_seq(run_id),
            }
        )
        max_seq = max(0, int(since_seq or 0))
        for event in RunEventBus.replay(run_id, since_seq, limit=_REPLAY_LIMIT):
            await websocket.send_json(_event_frame(event))
            max_seq = max(max_seq, int(event.get("seq", 0)))

        while True:
            if get_task is None:
                get_task = asyncio.create_task(queue.get())
            if recv_task is None:
                recv_task = asyncio.create_task(websocket.receive_text())
            done, _pending = await asyncio.wait(
                {get_task, recv_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if get_task in done:
                frame = get_task.result()
                get_task = None
                seq = int(frame.get("seq", 0))
                if seq > max_seq:
                    max_seq = seq
                    await websocket.send_json(frame)
            if recv_task in done:
                raw = recv_task.result()
                recv_task = None
                await _handle_client_message(websocket, run_id, raw)
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        raise
    except Exception:  # pragma: no cover - 兜底，避免连接泄漏
        logger.exception("ws runs %s crashed", run_id)
        with contextlib.suppress(Exception):
            await websocket.send_json(
                {"kind": "error", "code": "INTERNAL", "message": "internal error"}
            )
    finally:
        for task in (get_task, recv_task):
            if task is None or task.done():
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        RunEventBus.unsubscribe(run_id, queue)


async def _handle_client_message(websocket: WebSocket, run_id: int, raw: str | bytes) -> None:
    try:
        message = json.loads(raw)
    except (TypeError, ValueError):
        return
    if not isinstance(message, dict):
        return
    kind = message.get("kind")
    if kind == "ping":
        await websocket.send_json({"kind": "pong"})
    elif kind == "ack":
        try:
            seq = int(message.get("last_seq") or 0)
        except (TypeError, ValueError):
            return
        with _ACK_LOCK:
            _ACKS[int(run_id)] = seq
