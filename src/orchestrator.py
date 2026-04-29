"""Module 13: Orchestrator.

Schedules active tests respecting power dependencies (spec §5.1 algorithm,
§5.2 cascading power dependency edge case): two tests CONFLICT if their
target devices share any ancestor in the power DAG. Conflicting tests are
serialized; non-conflicting tests run up to ``max_concurrent_tests`` in
parallel.

The DAG is built once from NetBox power-port → power-outlet connections.
Each device maps to a set of upstream power-source device_ids. Two tests
conflict if their devices' ancestor sets intersect.

Spec also notes (§4.3 ATS Transfer): "Must verify EVERY downstream UPS.
One unhealthy = outage." That's a per-test precondition (Module 10);
the orchestrator just guarantees the test isn't running concurrently
with anything else on the chain.

Tests inject ``device_loader`` (gives DeviceInfo by id) and a fake
TestEngine.execute. No NetBox/Modbus needed.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from .types import DeviceInfo, Event, TestRequest, TestResult


PowerGraph = dict[str, set[str]]
"""device_id -> set of all upstream power-source device_ids (transitive)."""


DeviceLoader = Callable[[str], Awaitable[DeviceInfo | None]]
TestExecutor = Callable[[TestRequest, DeviceInfo], Awaitable[TestResult]]


class Orchestrator:
    def __init__(
        self,
        config,
        executor: TestExecutor,
        event_queue: asyncio.Queue[Event],
        *,
        device_loader: DeviceLoader,
        power_graph: PowerGraph | None = None,
    ):
        self.config = config
        self._executor = executor
        self.event_queue = event_queue
        self._device_loader = device_loader
        self._power_graph: PowerGraph = power_graph or {}
        self._queue: asyncio.Queue[TestRequest] = asyncio.Queue()
        # device_ids of currently-running tests
        self._running: dict[str, str] = {}  # test_id -> device_id
        self._results: dict[str, TestResult] = {}
        self._lock = asyncio.Lock()
        self._completed_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_power_graph(self, graph: PowerGraph) -> None:
        self._power_graph = graph

    async def submit(self, request: TestRequest) -> None:
        await self._queue.put(request)

    async def wait_for(self, test_id: str, timeout: float | None = None) -> TestResult:
        """Block until a particular test completes. Useful for tests."""
        deadline = None if timeout is None else asyncio.get_event_loop().time() + timeout
        while True:
            if test_id in self._results:
                return self._results[test_id]
            wait = None
            if deadline is not None:
                wait = max(0.0, deadline - asyncio.get_event_loop().time())
                if wait == 0.0:
                    raise asyncio.TimeoutError
            try:
                if wait is None:
                    await self._completed_event.wait()
                else:
                    await asyncio.wait_for(self._completed_event.wait(), wait)
            except asyncio.TimeoutError:
                raise
            self._completed_event.clear()

    async def run(self) -> None:
        """Main scheduling loop. Cancel to stop."""
        while True:
            request = await self._queue.get()
            await self._dispatch_when_clear(request)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _dispatch_when_clear(self, request: TestRequest) -> None:
        """Wait for capacity + non-conflicting state, then launch the test."""
        device = await self._device_loader(request.device_id)
        if device is None:
            result = TestResult(
                test_id=request.test_id,
                device_id=request.device_id,
                test_name=request.test_name,
                status="failed",
                abort_reason=f"unknown device_id: {request.device_id}",
            )
            self._record_result(result)
            return

        while True:
            async with self._lock:
                if self._can_dispatch(request.device_id):
                    self._running[request.test_id] = request.device_id
                    asyncio.create_task(self._run_test(request, device))
                    return
            await asyncio.sleep(0.01)

    def _can_dispatch(self, device_id: str) -> bool:
        if len(self._running) >= self.config.max_concurrent_tests:
            return False
        for other_device_id in self._running.values():
            if self.conflicts(device_id, other_device_id):
                return False
        return True

    def conflicts(self, a: str, b: str) -> bool:
        """Two tests conflict if their devices share any power ancestor or one
        is an ancestor of the other."""
        if a == b:
            return True
        a_anc = self._power_graph.get(a, set())
        b_anc = self._power_graph.get(b, set())
        if a_anc & b_anc:
            return True
        if a in b_anc or b in a_anc:
            return True
        return False

    async def _run_test(self, request: TestRequest, device: DeviceInfo) -> None:
        try:
            result = await self._executor(request, device)
        except Exception as e:  # noqa: BLE001
            result = TestResult(
                test_id=request.test_id,
                device_id=request.device_id,
                test_name=request.test_name,
                status="failed",
                abort_reason=f"executor crash: {type(e).__name__}: {e}",
            )
        finally:
            async with self._lock:
                self._running.pop(request.test_id, None)
            self._record_result(result)

    def _record_result(self, result: TestResult) -> None:
        self._results[result.test_id] = result
        self._completed_event.set()


def build_power_graph_from_connections(
    connections: list[tuple[str, str]],
) -> PowerGraph:
    """Compute the transitive ancestor set for every device.

    Args:
        connections: list of (powered_device_id, power_source_device_id)
            tuples. (i.e., for each edge in the DAG, child → parent.)

    Returns:
        ``{device_id: {ancestor_id, ...}}`` covering every device that
        appears either as a child or as a parent.
    """
    parents: dict[str, set[str]] = {}
    all_nodes: set[str] = set()
    for child, parent in connections:
        parents.setdefault(child, set()).add(parent)
        all_nodes.add(child)
        all_nodes.add(parent)

    ancestors: dict[str, set[str]] = {n: set() for n in all_nodes}

    def walk(node: str, visiting: set[str]) -> set[str]:
        if ancestors[node] or node not in parents:
            return ancestors[node]
        if node in visiting:
            # cycle protection — should never happen in a DAG, but be safe.
            return set()
        visiting.add(node)
        result: set[str] = set()
        for p in parents.get(node, set()):
            result.add(p)
            result.update(walk(p, visiting))
        ancestors[node] = result
        visiting.discard(node)
        return result

    for n in all_nodes:
        walk(n, set())
    return ancestors
