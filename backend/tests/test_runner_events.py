"""RunEventBus 单测：seq 单调、replay、内存订阅、无订阅者 emit、payload JSON 化。"""

from __future__ import annotations

import asyncio

from app.events import RunEventBus


def test_emit_seq_monotonic_and_replay(temp_db):
    RunEventBus.reset()
    seqs = [RunEventBus.emit(1, "log", {"message": f"m{i}"}) for i in range(3)]
    assert seqs == [1, 2, 3]
    assert RunEventBus.last_seq(1) == 3

    events = RunEventBus.replay(1)
    assert [event["seq"] for event in events] == [1, 2, 3]
    assert events[0]["type"] == "log"
    assert events[0]["payload"] == {"message": "m0"}
    assert events[0]["created_at"]
    assert events[0]["ts"] == events[0]["created_at"]

    assert [event["seq"] for event in RunEventBus.replay(1, since_seq=1)] == [2, 3]
    assert RunEventBus.replay(1, since_seq=3) == []
    assert [event["seq"] for event in RunEventBus.replay(1, since_seq=0, limit=2)] == [1, 2]
    assert RunEventBus.last_seq(999) == 0


def test_emit_json_sanitizes_payload(temp_db):
    RunEventBus.reset()

    class Weird:
        pass

    seq = RunEventBus.emit(2, "log", {"obj": Weird(), "nested": {"n": 1}})
    payload = RunEventBus.replay(2, 0, 1)[0]["payload"]
    assert isinstance(payload["obj"], str)
    assert payload["nested"] == {"n": 1}
    assert seq == 1


def test_emit_without_subscribers_still_persists(temp_db):
    RunEventBus.reset()
    seq = RunEventBus.emit(3, "run_finished", {"status": "succeeded"})
    assert seq == 1
    assert RunEventBus.subscriber_count(3) == 0
    assert RunEventBus.last_seq(3) == 1


async def test_subscribe_receives_realtime_and_unsubscribe(temp_db):
    RunEventBus.reset()
    queue = RunEventBus.subscribe(4)
    assert RunEventBus.subscriber_count(4) == 1

    # 从工作线程 emit（模拟引擎线程），投递必须回到订阅者 loop
    seq = await asyncio.to_thread(RunEventBus.emit, 4, "step_started", {"step_id": "s1"})
    frame = await asyncio.wait_for(queue.get(), timeout=2)
    assert frame["kind"] == "event"
    assert frame["seq"] == seq
    assert frame["type"] == "step_started"
    assert frame["payload"] == {"step_id": "s1"}
    assert frame["ts"]

    RunEventBus.unsubscribe(4, queue)
    assert RunEventBus.subscriber_count(4) == 0
    assert RunEventBus.emit(4, "log", {"message": "no subscriber"}) == seq + 1


def test_multiple_subscribers_each_get_frame(temp_db):
    RunEventBus.reset()

    async def _scenario() -> None:
        first = RunEventBus.subscribe(5)
        second = RunEventBus.subscribe(5)
        assert RunEventBus.subscriber_count(5) == 2
        await asyncio.to_thread(RunEventBus.emit, 5, "log", {"message": "broadcast"})
        one = await asyncio.wait_for(first.get(), timeout=2)
        two = await asyncio.wait_for(second.get(), timeout=2)
        assert one["seq"] == two["seq"] == 1
        RunEventBus.unsubscribe(5, first)
        RunEventBus.unsubscribe(5, second)
        assert RunEventBus.subscriber_count(5) == 0

    asyncio.run(_scenario())
