"""录制 REST + WS（契约 §7.4 / §6.3）。

路由统一挂在根 router 上（`/api/...` 与 `/ws/...`），由 main.py 动态 include；
WS 路径 `/ws/recordings/{recording_id}?since_seq=` 与契约 §6.3 对齐。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import delete, func, select

from ..api_schemas import RecordingCreateRequest, ToScriptRequest, items_response
from ..db import session_scope
from ..errors import ApiError, not_found
from ..logs import logger
from ..models import Recording, RecordingEvent, now_iso
from ..recorder import session as recorder_session
from ..recorder.session import RecordingEventBus

router = APIRouter(tags=["recordings"])


# ================================================================ 序列化 / 查行
def _recording_dict(recording: Recording) -> dict[str, Any]:
    return {
        "id": recording.id,
        "name": recording.name,
        "mode": recording.mode,
        "serial": recording.serial,
        "status": recording.status,
        "started_at": recording.started_at,
        "ended_at": recording.ended_at,
        "event_count": recording.event_count,
        "script_id": recording.script_id,
        "meta": recording.meta or {},
        "created_at": recording.created_at,
    }


def _event_dict(event: RecordingEvent) -> dict[str, Any]:
    return {
        "seq": event.seq,
        "kind": event.kind,
        "payload": event.payload or {},
        "confidence": event.confidence,
        "created_at": event.created_at,
    }


def _get_recording_row(recording_id: str) -> Recording:
    with session_scope() as db:
        recording = db.get(Recording, str(recording_id))
        if recording is None:
            raise not_found("recording", recording_id)
        return recording


def _recording_payload(recording_id: str) -> dict[str, Any]:
    return _recording_dict(_get_recording_row(recording_id))


def _latest_seq(recording_id: str) -> int:
    with session_scope() as db:
        value = db.scalar(
            select(func.max(RecordingEvent.seq)).where(RecordingEvent.recording_id == str(recording_id))
        )
        return int(value or 0)


# ================================================================ 设备能力（be-core 未就绪时降级）
def _registry() -> Any:
    try:
        from ..services import device_registry

        return device_registry
    except Exception:  # pragma: no cover - 装配期/单测
        return None


def _ensure_device_available(serial: str) -> Any:
    """serial 有效性 + 被 run 占用检查；返回 driver（无设备服务时返回 None）。"""
    registry = _registry()
    if registry is None or not hasattr(registry, "get_driver"):
        return None
    try:
        driver = registry.get_driver(serial)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError("DEVICE_UNAVAILABLE", f"设备不可用: {serial}", {"serial": serial, "error": str(exc)}) from exc
    if driver is None:
        raise ApiError("DEVICE_UNAVAILABLE", f"设备不可用: {serial}", {"serial": serial})
    try:
        alive = bool(driver.alive())
    except Exception:  # pragma: no cover - 驱动实现差异
        alive = True
    if not alive:
        raise ApiError("DEVICE_UNAVAILABLE", f"设备离线: {serial}", {"serial": serial})

    occupancy: list[Any] = []
    if hasattr(registry, "occupancy"):
        try:
            occupancy = registry.occupancy(serial) or []
        except Exception:  # pragma: no cover
            occupancy = []
    for item in occupancy:
        purpose = item.get("purpose") if isinstance(item, dict) else getattr(item, "purpose", None)
        if purpose == "run":
            raise ApiError(
                "DEVICE_BUSY",
                "设备正在运行脚本，无法开始录制",
                {"serial": serial, "occupancy": occupancy},
            )
    return driver


def _find_active_recording(serial: str) -> str | None:
    session = recorder_session.get_by_serial(serial)
    if session is not None:
        return session.recording_id
    with session_scope() as db:
        row = db.scalars(
            select(Recording)
            .where(Recording.serial == serial, Recording.status.in_(("recording", "paused")))
            .order_by(Recording.created_at.desc())
            .limit(1)
        ).first()
        return row.id if row is not None else None


def _acquire_device(serial: str, recording_id: str) -> None:
    registry = _registry()
    if registry is None or not hasattr(registry, "acquire"):
        return
    try:
        registry.acquire(serial, "recording", recording_id)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(
            "DEVICE_BUSY",
            "设备已被占用，无法开始录制",
            {"serial": serial, "error": str(exc)},
        ) from exc


def _release_device(serial: str, recording_id: str) -> None:
    registry = _registry()
    if registry is None or not hasattr(registry, "release"):
        return
    try:
        registry.release(serial, "recording", recording_id)
    except Exception:  # pragma: no cover - 尽力释放
        logger.debug("release device for recording failed: %s", recording_id)


# ================================================================ REST：录制 CRUD
@router.post("/api/recordings", status_code=201)
def create_recording(request: RecordingCreateRequest) -> dict[str, Any]:
    serial = (request.serial or "").strip()
    if not serial:
        raise ApiError("INVALID_ARGUMENT", "serial 不能为空")
    mode = request.mode or "web"
    _ensure_device_available(serial)
    active_id = _find_active_recording(serial)
    if active_id is not None:
        raise ApiError(
            "CONFLICT",
            "该设备已有进行中的录制，请先停止",
            {"serial": serial, "recording_id": active_id},
        )
    if mode == "physical":
        # 触摸设备探测失败 -> UNSUPPORTED（create 时会再探测一次，保持幂等）
        recorder_session.probe_physical(serial)

    recording_id = f"rec_{uuid.uuid4().hex[:12]}"
    started_at = now_iso()
    default_name = f"录制 {started_at[:19].replace('T', ' ')}"
    name = (request.name or "").strip() or default_name
    _acquire_device(serial, recording_id)
    try:
        with session_scope() as db:
            db.add(
                Recording(
                    id=recording_id,
                    name=name,
                    mode=mode,
                    serial=serial,
                    status="recording",
                    started_at=started_at,
                    event_count=0,
                    meta={"mode": mode},
                    created_at=started_at,
                )
            )
        recorder_session.create(recording_id, serial, mode)
    except BaseException as exc:
        _cleanup_failed_recording(recording_id, serial)
        if isinstance(exc, ApiError):
            raise
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):  # pragma: no cover
            raise
        raise ApiError(
            "UNSUPPORTED" if mode == "physical" else "INTERNAL",
            "创建录制会话失败",
            {"serial": serial, "error": str(exc)},
        ) from exc
    logger.info("recording created: %s serial=%s mode=%s", recording_id, serial, mode)
    return _recording_payload(recording_id)


def _cleanup_failed_recording(recording_id: str, serial: str) -> None:
    try:
        recorder_session.stop(recording_id)
    except Exception:  # pragma: no cover - 清理尽力而为
        pass
    _release_device(serial, recording_id)
    try:
        with session_scope() as db:
            db.execute(delete(RecordingEvent).where(RecordingEvent.recording_id == recording_id))
            recording = db.get(Recording, recording_id)
            if recording is not None:
                db.delete(recording)
    except Exception:  # pragma: no cover
        logger.warning("cleanup failed recording row failed: %s", recording_id)


@router.get("/api/recordings")
def list_recordings(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    with session_scope() as db:
        condition = Recording.status == status if status else None
        count_statement = select(func.count()).select_from(Recording)
        list_statement = select(Recording).order_by(Recording.created_at.desc(), Recording.id.desc())
        if condition is not None:
            count_statement = count_statement.where(condition)
            list_statement = list_statement.where(condition)
        total = int(db.scalar(count_statement) or 0)
        rows = list(db.scalars(list_statement.limit(limit).offset(offset)))
    return items_response([_recording_dict(row) for row in rows], total=total)


@router.get("/api/recordings/{recording_id}")
def get_recording(recording_id: str) -> dict[str, Any]:
    return _recording_payload(recording_id)


@router.post("/api/recordings/{recording_id}/pause")
def pause_recording(recording_id: str) -> dict[str, Any]:
    _get_recording_row(recording_id)
    recorder_session.pause(recording_id)
    return _recording_payload(recording_id)


@router.post("/api/recordings/{recording_id}/resume")
def resume_recording(recording_id: str) -> dict[str, Any]:
    _get_recording_row(recording_id)
    recorder_session.resume(recording_id)
    return _recording_payload(recording_id)


@router.post("/api/recordings/{recording_id}/stop")
def stop_recording(recording_id: str) -> dict[str, Any]:
    _get_recording_row(recording_id)
    recorder_session.stop(recording_id)
    return _recording_payload(recording_id)


# ================================================================ REST：事件
@router.get("/api/recordings/{recording_id}/events")
def list_recording_events(
    recording_id: str,
    since_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=2000),
) -> dict[str, Any]:
    _get_recording_row(recording_id)
    with session_scope() as db:
        rows = list(
            db.scalars(
                select(RecordingEvent)
                .where(RecordingEvent.recording_id == str(recording_id), RecordingEvent.seq > since_seq)
                .order_by(RecordingEvent.seq)
                .limit(limit)
            )
        )
    return items_response([_event_dict(row) for row in rows])


@router.delete("/api/recordings/{recording_id}/events/{seq}")
def delete_recording_event(recording_id: str, seq: int) -> dict[str, Any]:
    _get_recording_row(recording_id)
    with session_scope() as db:
        event = db.scalars(
            select(RecordingEvent).where(
                RecordingEvent.recording_id == str(recording_id),
                RecordingEvent.seq == int(seq),
            )
        ).first()
        if event is None:
            raise not_found("recording event", f"{recording_id}#{seq}")
        db.delete(event)
        db.flush()
        total = int(
            db.scalar(
                select(func.count())
                .select_from(RecordingEvent)
                .where(RecordingEvent.recording_id == str(recording_id))
            )
            or 0
        )
        recording = db.get(Recording, str(recording_id))
        if recording is not None:
            recording.event_count = total
    return {"deleted": True, "seq": int(seq), "event_count": total}


@router.delete("/api/recordings/{recording_id}")
def delete_recording(recording_id: str) -> dict[str, Any]:
    _get_recording_row(recording_id)
    recorder_session.stop(recording_id)
    with session_scope() as db:
        db.execute(delete(RecordingEvent).where(RecordingEvent.recording_id == str(recording_id)))
        recording = db.get(Recording, str(recording_id))
        if recording is not None:
            db.delete(recording)
    logger.info("recording deleted: %s", recording_id)
    return {"deleted": True, "id": str(recording_id)}


@router.post("/api/recordings/{recording_id}/to_script")
def recording_to_script(
    recording_id: str,
    request: ToScriptRequest | None = None,
) -> dict[str, Any]:
    payload = request or ToScriptRequest()
    return recorder_session.to_script(
        recording_id,
        name=payload.name,
        include_kinds=payload.include_kinds,
        exclude_seqs=payload.exclude_seqs,
    )


# ================================================================ WS
async def _forward_events(websocket: WebSocket, queue: asyncio.Queue[dict[str, Any]]) -> None:
    try:
        while True:
            frame = await queue.get()
            await websocket.send_json(frame)
    except (WebSocketDisconnect, RuntimeError):
        return


async def _control_loop(websocket: WebSocket) -> None:
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(message, dict):
                continue
            kind = message.get("kind")
            if kind == "ping":
                await websocket.send_json({"kind": "pong"})
            # ack 仅记录 meta，契约 §6.1/§6.3 不要求持久化
    except (WebSocketDisconnect, RuntimeError):
        return


@router.websocket("/ws/recordings/{recording_id}")
async def recording_websocket(
    websocket: WebSocket,
    recording_id: str,
    since_seq: int = 0,
) -> None:
    await websocket.accept()
    with session_scope() as db:
        recording = db.get(Recording, str(recording_id))
        if recording is None:
            await websocket.send_json(
                {"kind": "error", "code": "NOT_FOUND", "message": "recording not found"}
            )
            await websocket.close(code=4404)
            return
        state = {
            "kind": "state",
            "recording_id": recording.id,
            "status": recording.status,
            "event_count": recording.event_count,
            "last_seq": int(
                db.scalar(
                    select(func.max(RecordingEvent.seq)).where(RecordingEvent.recording_id == str(recording_id))
                )
                or 0
            ),
        }
    queue = RecordingEventBus.subscribe(recording_id)
    try:
        await websocket.send_json(state)
        for frame in RecordingEventBus.replay(recording_id, int(since_seq or 0)):
            await websocket.send_json(frame)
        forward_task = asyncio.create_task(_forward_events(websocket, queue))
        control_task = asyncio.create_task(_control_loop(websocket))
        try:
            await asyncio.wait({forward_task, control_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (forward_task, control_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(forward_task, control_task, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    finally:
        RecordingEventBus.unsubscribe(recording_id, queue)
        try:
            await websocket.close()
        except Exception:  # pragma: no cover - 已关闭
            pass
