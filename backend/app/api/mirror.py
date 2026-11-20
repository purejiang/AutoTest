"""镜像 REST + WS（契约 §6.2 / §7.1 / §14.3）。

路由（由 main.py 动态 include，不带额外 prefix）：
- ``POST /api/devices/{serial}/mirror/start``：启动/复用 MirrorSession（可选白名单参数）；
- ``POST /api/devices/{serial}/mirror/stop``：停止会话（幂等）；
- ``GET  /api/devices/{serial}/mirror/status``：状态（无会话 active=False）；
- ``WS   /ws/mirror/{serial}?token=``：hello → binary 帧（12 字节头）→ 控制消息，
  处理 ``request_keyframe`` / ``ping``。

帧二进制布局与上游 scrcpy 完全一致（后端直接透传 12 字节头 + Annex B payload）：
``[u64 BE pts_us][u32 BE payload_len][payload]``。

坐标控制**不走 WS**（前端镜像页走 REST tap/swipe，带 nx/ny 归一化坐标，§6.2-5）。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Body, Query, WebSocket, WebSocketDisconnect

from .. import config
from .. import mirror as mirror_module
from ..android import scrcpy
from ..errors import ApiError
from ..logs import logger

router = APIRouter()

# 每个 serial 在 device_registry 的镜像占用 ref（purpose="mirror"）
_MIRROR_REFS: dict[str, str] = {}


# ---------------------------------------------------------------- helpers
def _session_payload(serial: str) -> dict[str, Any]:
    status = mirror_module.mirror_status(serial)
    status["active"] = bool(status.get("active"))
    return status


def _build_config(payload: dict[str, Any] | None) -> scrcpy.ServerConfig:
    """settings 默认 + 请求体白名单（max_size/max_fps/video_bit_rate/crop）。"""
    base = scrcpy.ServerConfig.from_settings()
    overrides: dict[str, Any] = {}
    payload = payload or {}
    allowed = {"max_size", "max_fps", "video_bit_rate", "crop"}
    unknown = set(payload) - allowed
    if unknown:
        raise ApiError("INVALID_ARGUMENT", f"mirror start 不支持的参数: {sorted(unknown)}", {"allowed": sorted(allowed)})
    try:
        if "max_size" in payload:
            overrides["max_size"] = int(payload["max_size"])
        if "max_fps" in payload:
            overrides["max_fps"] = int(payload["max_fps"])
        if "video_bit_rate" in payload:
            overrides["video_bit_rate"] = int(payload["video_bit_rate"])
        if "crop" in payload:
            overrides["crop"] = str(payload["crop"] or "")
    except (TypeError, ValueError) as exc:
        raise ApiError("INVALID_ARGUMENT", f"mirror start 参数类型非法: {exc}", {"payload": payload}) from exc
    values = dict(
        jar_path=base.jar_path,
        max_size=base.max_size,
        max_fps=base.max_fps,
        video_bit_rate=base.video_bit_rate,
        crop=base.crop,
        codec=base.codec,
        socket_name=base.socket_name,
        tunnel_forward=base.tunnel_forward,
        control=base.control,
        log_level=base.log_level,
        stream_mode=base.stream_mode,
    )
    values.update(overrides)
    return scrcpy.ServerConfig(**values)


def _try_acquire(serial: str) -> str:
    """be-core 的 device_registry 若已就绪则登记 mirror 占用；未就绪时跳过（不阻塞媒体管线）。"""
    ref_id = f"mirror_{uuid.uuid4().hex[:12]}"
    try:
        from ..services import device_registry  # noqa: PLC0415 - 可选依赖（be-core 负责）
    except Exception:  # noqa: BLE001 - 模块未就绪
        return ""
    acquire = getattr(device_registry, "acquire", None)
    if not callable(acquire):
        return ""
    try:
        acquire(serial, "mirror", ref_id)
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001 - 注册表接口未就绪时不阻塞镜像
        logger.warning("device_registry.acquire failed for mirror(%s): %s", serial, exc)
        return ""
    _MIRROR_REFS[serial] = ref_id
    return ref_id


def _try_release(serial: str) -> None:
    ref_id = _MIRROR_REFS.pop(serial, "")
    if not ref_id:
        return
    try:
        from ..services import device_registry  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return
    release = getattr(device_registry, "release", None)
    if callable(release):
        try:
            release(serial, "mirror", ref_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("device_registry.release failed for mirror(%s): %s", serial, exc)


# ---------------------------------------------------------------- REST
@router.post("/api/devices/{serial}/mirror/start")
async def start_mirror(serial: str, payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    cfg = _build_config(payload)
    _try_acquire(serial)
    try:
        session = await asyncio.to_thread(mirror_module.start_session, serial, cfg)
    except ApiError:
        _try_release(serial)
        raise
    except Exception as exc:  # noqa: BLE001 - 兜底成 SCRCPY_FAILED，绝不 500
        _try_release(serial)
        logger.exception("mirror start failed for %s", serial)
        raise ApiError("SCRCPY_FAILED", f"镜像启动失败: {exc}", {"serial": serial}) from exc
    return {"active": True, "serial": serial, "session_id": session.session_id, **session.status()}


@router.post("/api/devices/{serial}/mirror/stop")
async def stop_mirror(serial: str) -> dict[str, Any]:
    await asyncio.to_thread(mirror_module.stop_session, serial)
    _try_release(serial)
    return {"active": False, "serial": serial}


@router.get("/api/devices/{serial}/mirror/status")
async def mirror_status(serial: str) -> dict[str, Any]:
    return _session_payload(serial)


# ---------------------------------------------------------------- WS
def _check_token(token: str | None) -> bool:
    configured = str(config.get("security.token") or "")
    if not configured:
        return True
    return token == configured


async def _receive_loop(websocket: WebSocket, session: Any, sub: Any) -> None:
    """处理 client→server 控制消息（ping / request_keyframe），回复走订阅队列保证发送串行。"""
    while True:
        try:
            text = await websocket.receive_text()
        except WebSocketDisconnect:
            return
        try:
            message = json.loads(text)
        except (TypeError, ValueError):
            logger.debug("mirror ws got non-json text: %r", text[:100])
            continue
        if not isinstance(message, dict):
            continue
        kind = message.get("kind")
        if kind == "ping":
            session.push_control(sub, {"kind": "pong"})
        elif kind == "request_keyframe":
            # 客户端显式请求（工具栏按钮/新连接首帧）→ 绕过节流
            session.request_keyframe(force=True)
            logger.debug("mirror ws requested keyframe for %s", session.serial)
        else:
            logger.debug("mirror ws ignored control message: %s", kind)


async def _send_loop(websocket: WebSocket, session: Any, sub: Any) -> None:
    """串行发送：控制消息（controls，优先）→ 二进制帧 → 空闲时周期性 stats。"""
    while True:
        if sub.controls:
            item = sub.controls.popleft()
        else:
            try:
                item = await asyncio.wait_for(sub.queue.get(), timeout=5.0)
            except TimeoutError:
                stats = session.stats()
                await websocket.send_json({"kind": "stats", **stats})
                continue
            if item is mirror_module.WAKE_SENTINEL:
                continue
        if item is None:  # session.stop() 的哨兵
            await websocket.close(code=1000)
            return
        if isinstance(item, (bytes, bytearray)):
            await websocket.send_bytes(bytes(item))
        else:
            await websocket.send_json(item)


@router.websocket("/ws/mirror/{serial}")
async def mirror_ws(websocket: WebSocket, serial: str, token: str | None = Query(default=None)) -> None:
    await websocket.accept()
    if not _check_token(token):
        await websocket.send_json({"kind": "error", "code": "UNAUTHORIZED", "message": "invalid token"})
        await websocket.close(code=1008)
        return
    session = mirror_module.get_session(serial)
    if session is None or not session.active:
        try:
            session = await asyncio.to_thread(mirror_module.start_session, serial)
        except ApiError as exc:
            await websocket.send_json({"kind": "error", "code": exc.code, "message": exc.message})
            await websocket.close(code=1011)
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("mirror ws auto-start failed for %s", serial)
            await websocket.send_json({"kind": "error", "code": "SCRCPY_FAILED", "message": str(exc)})
            await websocket.close(code=1011)
            return
    try:
        await websocket.send_json(session.hello_payload())
    except (WebSocketDisconnect, RuntimeError):
        return
    sub = session.subscribe()
    receive_task = asyncio.create_task(_receive_loop(websocket, session, sub))
    try:
        await _send_loop(websocket, session, sub)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:  # noqa: BLE001 - WS 已关闭/序列化异常
        logger.exception("mirror ws send loop failed for %s", serial)
    finally:
        receive_task.cancel()
        try:
            await receive_task
        except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            pass
        session.unsubscribe(sub)
