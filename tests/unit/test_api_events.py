"""Tests for the SSE EventBroadcaster (Module 21 support)."""

import asyncio

from src.api.events import EventBroadcaster
from src.types import Event


def _evt(i):
    return Event(event_type="poll_result", timestamp="", data={"i": i})


async def test_publish_fans_out_to_all_subscribers():
    b = EventBroadcaster()
    q1, q2 = b.subscribe(), b.subscribe()
    assert b.subscriber_count == 2

    b.publish(_evt(1))
    assert q1.get_nowait().data["i"] == 1
    assert q2.get_nowait().data["i"] == 1


async def test_unsubscribe_stops_delivery():
    b = EventBroadcaster()
    q = b.subscribe()
    b.unsubscribe(q)
    b.publish(_evt(1))
    assert q.empty()
    assert b.subscriber_count == 0


async def test_full_subscriber_drops_oldest():
    b = EventBroadcaster(max_queue=2)
    q = b.subscribe()
    for i in range(4):
        b.publish(_evt(i))
    # Queue holds the two most recent events; oldest were dropped.
    drained = [q.get_nowait().data["i"] for _ in range(q.qsize())]
    assert drained == [2, 3]


async def test_pump_drains_source_into_subscribers():
    b = EventBroadcaster()
    source: asyncio.Queue = asyncio.Queue()
    q = b.subscribe()
    task = asyncio.create_task(b.pump(source))
    await source.put(_evt(7))
    for _ in range(50):
        await asyncio.sleep(0)
        if not q.empty():
            break
    assert q.get_nowait().data["i"] == 7
    task.cancel()
