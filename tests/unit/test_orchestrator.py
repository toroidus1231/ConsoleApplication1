"""Tests for Module 13 — Orchestrator.

A concurrency-recording fake test engine lets us assert the scheduler's two
core guarantees deterministically: power-conflicting tests serialize (peak
concurrency 1) while independent tests run in parallel (peak concurrency 2),
plus priority ordering and the max_concurrent cap.
"""

import asyncio

from src.orchestrator import Orchestrator, build_power_graph
from src.types import DeviceInfo, TestRequest, TestResult


class RecordingEngine:
    """Records peak concurrency and start order, yielding cooperatively so
    co-dispatched tasks actually overlap."""

    def __init__(self, yields: int = 3):
        self.active: set[str] = set()
        self.max_active = 0
        self.start_order: list[str] = []
        self._yields = yields

    async def execute(self, request: TestRequest, device: DeviceInfo) -> TestResult:
        self.start_order.append(request.test_id)
        self.active.add(request.test_id)
        self.max_active = max(self.max_active, len(self.active))
        for _ in range(self._yields):
            await asyncio.sleep(0)
            self.max_active = max(self.max_active, len(self.active))
        self.active.discard(request.test_id)
        return TestResult(
            test_id=request.test_id,
            device_id=request.device_id,
            test_name=request.test_name,
            status="passed",
        )


def _dev(device_id):
    return DeviceInfo(
        device_id=device_id, name=f"dev-{device_id}", primary_ip="10.0.0.1",
        device_type_slug="x", config_context={}, protocol="modbus_tcp",
        site="DC1", rack="A1", position=1,
    )


def _req(device_id, *, priority=3, test_id=None, requested_at=""):
    return TestRequest(
        test_id=test_id or f"t-{device_id}",
        device_id=device_id,
        test_name="t",
        priority=priority,
        requested_at=requested_at,
    )


def _orch(engine, device_ids, *, ancestors=None, max_concurrent_tests=5):
    devices = {d: _dev(d) for d in device_ids}
    return Orchestrator(
        test_engine=engine,
        devices=devices,
        ancestors=ancestors,
        max_concurrent_tests=max_concurrent_tests,
    )


# --- Power graph -------------------------------------------------------------

def test_build_power_graph_transitive_ancestors():
    # UPS-A powers PDU-B powers RACK-C.
    graph = build_power_graph([("A", "B"), ("B", "C")])
    assert graph["B"] == {"A"}
    assert graph["C"] == {"A", "B"}


def test_build_power_graph_shared_source():
    # One source A feeds both B and C.
    graph = build_power_graph([("A", "B"), ("A", "C")])
    assert graph["B"] == {"A"}
    assert graph["C"] == {"A"}


def test_conflicts_detects_shared_ancestor():
    orch = _orch(RecordingEngine(), ["B", "C", "D"], ancestors={"B": {"A"}, "C": {"A", "B"}})
    assert orch.conflicts("B", "C") is True   # share A
    assert orch.conflicts("B", "D") is False  # D independent
    assert orch.conflicts("D", "D") is True    # a device always conflicts with itself


# --- Scheduling --------------------------------------------------------------

async def test_independent_tests_run_in_parallel():
    engine = RecordingEngine()
    orch = _orch(engine, ["X", "Y"], ancestors={}, max_concurrent_tests=5)
    await orch.submit(_req("X"))
    await orch.submit(_req("Y"))
    await orch.run_until_idle()

    assert engine.max_active == 2  # ran concurrently
    assert set(orch.results) == {"t-X", "t-Y"}
    assert all(r.status == "passed" for r in orch.results.values())


async def test_power_conflicting_tests_serialize():
    engine = RecordingEngine()
    # B and C share ancestor A → must not run together.
    orch = _orch(engine, ["B", "C"], ancestors={"B": {"A"}, "C": {"A"}}, max_concurrent_tests=5)
    await orch.submit(_req("B"))
    await orch.submit(_req("C"))
    await orch.run_until_idle()

    assert engine.max_active == 1  # never overlapped
    assert set(orch.results) == {"t-B", "t-C"}


async def test_priority_orders_dispatch():
    engine = RecordingEngine()
    orch = _orch(engine, ["a", "b", "c"], ancestors={}, max_concurrent_tests=1)
    await orch.submit(_req("a", priority=3, test_id="low"))
    await orch.submit(_req("b", priority=1, test_id="high"))
    await orch.submit(_req("c", priority=2, test_id="mid"))
    await orch.run_until_idle()

    assert engine.start_order == ["high", "mid", "low"]


async def test_max_concurrent_cap_respected():
    engine = RecordingEngine()
    orch = _orch(engine, ["a", "b", "c", "d"], ancestors={}, max_concurrent_tests=2)
    for d in ["a", "b", "c", "d"]:
        await orch.submit(_req(d))
    await orch.run_until_idle()

    assert engine.max_active == 2  # capped at 2 despite 4 independent tests
    assert len(orch.results) == 4


async def test_missing_device_fails_gracefully():
    engine = RecordingEngine()
    orch = Orchestrator(test_engine=engine, devices={}, ancestors={})
    await orch.submit(_req("ghost"))
    await orch.run_until_idle()

    result = orch.results["t-ghost"]
    assert result.status == "failed"
    assert "not found" in result.abort_reason
