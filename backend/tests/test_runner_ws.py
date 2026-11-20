"""运行事件 WebSocket 测试（契约 §6.1）：state/replay/实时/ping/ack/断开清理。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app import ws as ws_api
from app.errors import install_error_handlers
from app.events import RunEventBus
from tests.test_runner_kits import (
    click_step,
    insert_run,
    make_script,
    wait_until,
)
from tests.test_runner_kits import (
    runner_env as runner_env,
)


@pytest.fixture()
def ws_client(runner_env):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(ws_api.router)
    ws_api.reset_acks()
    with TestClient(app) as client:
        yield client


def _run(status: str = "running") -> int:
    return insert_run(make_script([click_step("s1")]), status=status)


def test_ws_state_and_replay(ws_client):
    run_id = _run()
    RunEventBus.emit(run_id, "run_started", {"total_steps": 1})
    RunEventBus.emit(run_id, "step_started", {"step_id": "s1"})

    with ws_client.websocket_connect(f"/ws/runs/{run_id}?since_seq=0") as websocket:
        state = websocket.receive_json()
        assert state == {
            "kind": "state",
            "run_id": run_id,
            "status": "running",
            "last_seq": 2,
        }

        first = websocket.receive_json()
        assert first["kind"] == "event"
        assert first["seq"] == 1
        assert first["type"] == "run_started"
        assert first["payload"] == {"total_steps": 1}
        assert first["ts"]

        second = websocket.receive_json()
        assert second["seq"] == 2
        assert second["type"] == "step_started"

        websocket.send_json({"kind": "ping"})
        assert websocket.receive_json() == {"kind": "pong"}

        websocket.send_json({"kind": "ack", "last_seq": 2})
        websocket.send_json({"kind": "ping"})
        assert websocket.receive_json()["kind"] == "pong"
        assert ws_api.last_ack(run_id) == 2


def test_ws_since_seq_only_replays_newer_events(ws_client):
    run_id = _run()
    for index in range(3):
        RunEventBus.emit(run_id, "log", {"message": f"m{index}"})

    with ws_client.websocket_connect(f"/ws/runs/{run_id}?since_seq=2") as websocket:
        state = websocket.receive_json()
        assert state["last_seq"] == 3
        event = websocket.receive_json()
        assert event["seq"] == 3
        assert event["payload"] == {"message": "m2"}


def test_ws_forwards_realtime_events(ws_client):
    run_id = _run()

    with ws_client.websocket_connect(f"/ws/runs/{run_id}") as websocket:
        state = websocket.receive_json()
        assert state["last_seq"] == 0

        RunEventBus.emit(run_id, "run_paused", {"reason": "user", "at_step_id": "s1"})
        event = websocket.receive_json()

        assert event["kind"] == "event"
        assert event["seq"] == 1
        assert event["type"] == "run_paused"
        assert event["payload"]["reason"] == "user"


def test_ws_unknown_run_gets_error(ws_client):
    with ws_client.websocket_connect("/ws/runs/424242") as websocket:
        message = websocket.receive_json()
        assert message["kind"] == "error"
        assert message["code"] == "NOT_FOUND"


def test_ws_disconnect_unsubscribes(ws_client):
    run_id = _run()
    with ws_client.websocket_connect(f"/ws/runs/{run_id}") as websocket:
        websocket.receive_json()
        assert RunEventBus.subscriber_count(run_id) == 1

    wait_until(
        lambda: RunEventBus.subscriber_count(run_id) == 0,
        timeout=2.0,
        message="ws unsubscribe on disconnect",
    )
