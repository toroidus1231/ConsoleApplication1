"""Tests for Module 13 — Orchestrator.

Power graph construction and conflict detection are pure functions tested
directly. Scheduling is tested by submitting requests to a running orchestrator
task and verifying that conflicting tests are serialized while independent
tests run in parallel up to max_concurrent_tests.
"""

import asyncio
from dataclasses import dataclass

import pytest

from src.orchestrator import (
    Orchestrator,
    build_power_graph_from_connections,
)
from src.types import DeviceInfo, Event, TestRequest, TestResult


@dataclass
class FakeConfig:
    max_concurrent_tests: int = 5


def _device(dev_id: str) -> DeviceInfo:
    return DeviceInfo(
        device_id=dev_id,
        name=f"d{dev_id}",
        primary_ip="10.0.0.1",
        device_type_slug="x",
        config_context={"protocol": "modbus_tcp"},
        protocol="modbus_tcp",
        site="DC1",
        rack="",
        position=0,
    )


# --- Power graph construction ------------------------------------------------


def test_simple_chain_ancestors():
    # gen → utility → ups → pdu → server
    graph = build_power_graph_from_connections([
        ("server", "pdu"),
        ("pdu", "ups"),
        ("ups", "utility"),
        ("utility", "gen"),
    ])
    assert graph["server"] == {"pdu", "ups", "utility", "gen"}
    assert graph["pdu"] == {"ups", "utility", "gen"}
    assert graph["ups"] == {"utility", "gen"}
    assert graph["utility"] == {"gen"}
    assert graph["gen"] == set()


def test_diamond_topology_collapses_ancestors():
    # both ups_a and ups_b feed off the same utility
    graph = build_power_graph_from_connections([
        ("server", "ups_a"),
        ("server", "ups_b"),
        ("ups_a", "utility"),
        ("ups_b", "utility"),
    ])
    assert graph["server"] == {"ups_a", "ups_b", "utility"}


def test_independent_chains_have_disjoint_ancestors():
    graph = build_power_graph_from_connections([
        ("a1", "u1"),
        ("a2", "u2"),
    ])
    assert graph["a1"] == {"u1"}
    assert graph["a2"] == {"u2"}


# --- Conflict detection (spec §5.2) ------------------------------------------


def _orch_with_graph(graph) -> Orchestrator:
    async def loader(_):
        return None  # not used for conflict tests
    return Orchestrator(
        FakeConfig(),
        executor=lambda r, d: None,
        event_queue=asyncio.Queue(),
        device_loader=loader,
        power_graph=graph,
    )


def test_devices_on_same_chain_conflict():
    graph = build_power_graph_from_connections([
        ("server_a", "pdu_1"),
        ("server_b", "pdu_1"),
        ("pdu_1", "ups_1"),
    ])
    o = _orch_with_graph(graph)
    assert o.conflicts("server_a", "server_b") is True   # share pdu_1
    assert o.conflicts("server_a", "pdu_1") is True      # ancestor relation


def test_devices_on_independent_chains_do_not_conflict():
    graph = build_power_graph_from_connections([
        ("server_a", "ups_1"),
        ("server_b", "ups_2"),
    ])
    o = _orch_with_graph(graph)
    assert o.conflicts("server_a", "server_b") is False


def test_unknown_devices_do_not_conflict():
    o = _orch_with_graph({})
    assert o.conflicts("a", "b") is False


def test_same_device_always_conflicts_with_itself():
    o = _orch_with_graph({})
    assert o.conflicts("dev1", "dev1") is True


# --- Scheduling --------------------------------------------------------------


async def _build_orchestrator(power_graph, max_concurrent=5, exec_delay=0.05):
    devices = {f"d{i}": _device(f"d{i}") for i in range(1, 6)}

    async def loader(device_id):
        return devices.get(device_id)

    in_flight: dict[str, int] = {}
    max_seen = {"v": 0}

    async def executor(request, device):
        in_flight[request.test_id] = 1
        max_seen["v"] = max(max_seen["v"], len(in_flight))
        await asyncio.sleep(exec_delay)
        in_flight.pop(request.test_id, None)
        return TestResult(
            test_id=request.test_id,
            device_id=request.device_id,
            test_name=request.test_name,
            status="passed",
        )

    orch = Orchestrator(
        FakeConfig(max_concurrent_tests=max_concurrent),
        executor=executor,
        event_queue=asyncio.Queue(),
        device_loader=loader,
        power_graph=power_graph,
    )
    return orch, max_seen


async def test_independent_tests_run_in_parallel():
    graph = build_power_graph_from_connections([
        ("d1", "u1"), ("d2", "u2"), ("d3", "u3"),
    ])
    orch, max_seen = await _build_orchestrator(graph, max_concurrent=5)
    task = asyncio.create_task(orch.run())
    try:
        for i in range(1, 4):
            await orch.submit(TestRequest(test_id=f"t{i}", device_id=f"d{i}", test_name="x"))
        for i in range(1, 4):
            await orch.wait_for(f"t{i}", timeout=2.0)
        assert max_seen["v"] >= 2
    finally:
        task.cancel()
        with pytest.raises((asyncio.CancelledError, BaseException)):
            await task


async def test_conflicting_tests_serialized():
    # d1 and d2 share ancestor u1.
    graph = build_power_graph_from_connections([
        ("d1", "u1"),
        ("d2", "u1"),
    ])
    orch, max_seen = await _build_orchestrator(graph, max_concurrent=5, exec_delay=0.05)
    task = asyncio.create_task(orch.run())
    try:
        await orch.submit(TestRequest(test_id="t1", device_id="d1", test_name="x"))
        await orch.submit(TestRequest(test_id="t2", device_id="d2", test_name="x"))
        await orch.wait_for("t1", timeout=2.0)
        await orch.wait_for("t2", timeout=2.0)
        # max_seen must be 1: never both running simultaneously.
        assert max_seen["v"] == 1
    finally:
        task.cancel()
        with pytest.raises(BaseException):
            await task


async def test_max_concurrent_tests_enforced():
    # All independent, but cap at 2.
    graph = build_power_graph_from_connections([
        ("d1", "u1"), ("d2", "u2"), ("d3", "u3"), ("d4", "u4"), ("d5", "u5"),
    ])
    orch, max_seen = await _build_orchestrator(graph, max_concurrent=2, exec_delay=0.05)
    task = asyncio.create_task(orch.run())
    try:
        for i in range(1, 6):
            await orch.submit(TestRequest(test_id=f"t{i}", device_id=f"d{i}", test_name="x"))
        for i in range(1, 6):
            await orch.wait_for(f"t{i}", timeout=3.0)
        assert max_seen["v"] <= 2
    finally:
        task.cancel()
        with pytest.raises(BaseException):
            await task


async def test_unknown_device_results_in_failed_test():
    async def loader(device_id):
        return None  # device not in NetBox

    async def executor(req, dev):
        raise AssertionError("executor should not run for unknown device")

    orch = Orchestrator(
        FakeConfig(),
        executor=executor,
        event_queue=asyncio.Queue(),
        device_loader=loader,
        power_graph={},
    )
    task = asyncio.create_task(orch.run())
    try:
        await orch.submit(TestRequest(test_id="t1", device_id="ghost", test_name="x"))
        result = await orch.wait_for("t1", timeout=1.0)
        assert result.status == "failed"
        assert "unknown device_id" in result.abort_reason
    finally:
        task.cancel()
        with pytest.raises(BaseException):
            await task


async def test_executor_crash_is_captured_as_failed_result():
    async def loader(device_id):
        return _device(device_id)

    async def executor(req, dev):
        raise RuntimeError("boom")

    orch = Orchestrator(
        FakeConfig(),
        executor=executor,
        event_queue=asyncio.Queue(),
        device_loader=loader,
        power_graph={},
    )
    task = asyncio.create_task(orch.run())
    try:
        await orch.submit(TestRequest(test_id="t1", device_id="d1", test_name="x"))
        result = await orch.wait_for("t1", timeout=1.0)
        assert result.status == "failed"
        assert "executor crash" in result.abort_reason
    finally:
        task.cancel()
        with pytest.raises(BaseException):
            await task
