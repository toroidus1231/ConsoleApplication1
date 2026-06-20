"""SSE event broadcaster (contracts spec §6.2).

The backend modules each push :class:`~src.types.Event` objects onto a single
``asyncio.Queue``. The API server runs one ``pump`` task that drains that queue
and fans every event out to all connected dashboard clients. Each SSE client
gets its own bounded subscriber queue, so a slow client can't block the others
(its queue simply drops the oldest events once full).
"""

from __future__ import annotations

import asyncio

from ..types import Event


class EventBroadcaster:
    def __init__(self, max_queue: int = 1000):
        self._subscribers: set[asyncio.Queue] = set()
        self._max_queue = max_queue

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: Event) -> None:
        """Fan an event out to every subscriber. Non-blocking; drops the oldest
        event on any subscriber whose queue is full."""
        for q in list(self._subscribers):
            if q.full():
                try:
                    q.get_nowait()  # drop oldest to make room
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - racing producers
                pass

    async def pump(self, source: asyncio.Queue) -> None:
        """Drain ``source`` (the modules' shared event queue) into all
        subscribers forever. Cancel the task to stop."""
        while True:
            event = await source.get()
            self.publish(event)
