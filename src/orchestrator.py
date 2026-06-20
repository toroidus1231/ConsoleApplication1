"""Module 13: Orchestrator.

Schedules active tests so that two tests never run simultaneously on devices
that share a power chain (contracts spec §5.1). The power-dependency graph is a
DAG of ``power_source → powered_device`` edges built from NetBox power
connections. For each device we precompute the set of all ancestor power
sources; two tests CONFLICT iff their devices share any ancestor.

Scheduling algorithm (spec §5.1):

1. Sort the pending queue by priority (1 = highest), then by request time.
2. Dispatch a test only if it conflicts with no currently-running test and we
   are under ``max_concurrent_tests``; otherwise leave it queued.
3. Re-evaluate the queue whenever a test completes.

The test engine, the device map, and the ancestor graph are all injected, so the
scheduler is unit-tested deterministically without NetBox or real tests running.
If power connections are not modeled in NetBox, every device has an empty
ancestor set and all tests run concurrently (spec §5.1 warning) — except that a
device is always treated as its own ancestor, so two tests on the *same* device
still serialize.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Protocol

from .timeutil import iso_now
from .types import DeviceInfo, Event, TestRequest, TestResult


class TestEngineLike(Protocol):
    async def execute(self, request: TestRequest, device: DeviceInfo) -> TestResult: ...


def build_power_graph(edges: list[tuple[str, str]]) -> dict[str, set[str]]:
    """Build ``device_id → {all ancestor power-source ids}`` from DAG edges.

    Each edge ``(source, powered)`` means *source powers powered*. Ancestors are
    transitive: a device's ancestors include its direct source and every
    ancestor of that source. Cycle-safe (defensive; a power DAG shouldn't cycle).
    """
    direct: dict[str, set[str]] = {}
    for source, powered in edges:
        direct.setdefault(powered, set()).add(source)

    cache: dict[str, set[str]] = {}

    def ancestors(node: str, seen: frozenset[str]) -> set[str]:
        if node in cache:
            return cache[node]
        result: set[str] = set()
        for parent in direct.get(node, ()):  # noqa: E1133
            if parent in seen:
                continue
            result.add(parent)
            result |= ancestors(parent, seen | {parent})
        cache[node] = result
        return result

    return {node: ancestors(node, frozenset({node})) for node in direct}


class Orchestrator:
    def __init__(
        self,
        *,
        test_engine: TestEngineLike,
        devices: dict[str, DeviceInfo],
        ancestors: dict[str, set[str]] | None = None,
        max_concurrent_tests: int = 5,
        event_queue: asyncio.Queue | None = None,
    ):
        self._engine = test_engine
        self._devices = devices
        self._ancestors = ancestors or {}
        self.max_concurrent_tests = max_concurrent_tests
        self._event_queue = event_queue

        self.pending: list[TestRequest] = []
        self._tasks: dict[str, asyncio.Task] = {}
        self._running_ancestors: dict[str, set[str]] = {}
        self.results: dict[str, TestResult] = {}
        self._wakeup = asyncio.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def submit(self, request: TestRequest) -> None:
        """Queue a test for scheduling."""
        self.pending.append(request)
        self._wakeup.set()

    @property
    def running(self) -> set[str]:
        return set(self._tasks)

    def ancestors_of(self, device_id: str) -> set[str]:
        """Power ancestors of a device, including the device itself."""
        return set(self._ancestors.get(device_id, set())) | {device_id}

    def conflicts(self, device_a: str, device_b: str) -> bool:
        """True if two devices share any power-chain ancestor."""
        return bool(self.ancestors_of(device_a) & self.ancestors_of(device_b))

    async def run_until_idle(self) -> dict[str, TestResult]:
        """Schedule and run every queued test to completion. Used by tests and
        by batch reconciliation runs."""
        while self.pending or self._tasks:
            self._dispatch_ready()
            if not self._tasks:
                break  # nothing dispatchable (shouldn't happen: self is own ancestor)
            done, _ = await asyncio.wait(
                self._tasks.values(), return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                self._reap(task)
        return self.results

    async def run(self) -> None:
        """Continuous production loop: schedule as requests arrive and as tests
        complete. Cancel the task to stop."""
        while True:
            self._dispatch_ready()
            if self._tasks:
                done, _ = await asyncio.wait(
                    self._tasks.values(), return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    self._reap(task)
            else:
                self._wakeup.clear()
                await self._wakeup.wait()

    # ------------------------------------------------------------------
    # Scheduling internals
    # ------------------------------------------------------------------
    def _dispatch_ready(self) -> None:
        # Priority 1 = highest; stable sort keeps submission order within a
        # priority/time tier.
        self.pending.sort(key=lambda r: (r.priority, r.requested_at))
        for request in list(self.pending):
            if len(self._tasks) >= self.max_concurrent_tests:
                break
            anc = self.ancestors_of(request.device_id)
            if self._conflicts_with_running(anc):
                continue
            self.pending.remove(request)
            self._running_ancestors[request.test_id] = anc
            device = self._devices.get(request.device_id)
            self._tasks[request.test_id] = asyncio.create_task(
                self._execute(request, device)
            )

    def _conflicts_with_running(self, anc: set[str]) -> bool:
        return any(anc & running for running in self._running_ancestors.values())

    async def _execute(self, request: TestRequest, device: DeviceInfo | None) -> TestResult:
        if device is None:
            return TestResult(
                test_id=request.test_id,
                device_id=request.device_id,
                test_name=request.test_name,
                status="failed",
                abort_reason=f"Device {request.device_id} not found",
                started_at=iso_now(),
                completed_at=iso_now(),
            )
        return await self._engine.execute(request, device)

    def _reap(self, task: asyncio.Task) -> None:
        test_id = next((tid for tid, t in self._tasks.items() if t is task), None)
        if test_id is None:
            return
        del self._tasks[test_id]
        self._running_ancestors.pop(test_id, None)
        result = task.result()
        self.results[test_id] = result
        if self._event_queue is not None:
            self._event_queue.put_nowait(
                Event(
                    event_type="test_completed",
                    timestamp=iso_now(),
                    data={"test_id": test_id, "status": result.status},
                )
            )
        self._wakeup.set()
