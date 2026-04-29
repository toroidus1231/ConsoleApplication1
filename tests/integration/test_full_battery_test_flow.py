"""End-to-end: simulated UPS device → Modbus poller → Test Engine →
Attestation Engine → MinIO. Validates that all six modules wired together
produce the expected behavior for the spec §4.1 ups_battery_transfer test.

This is the integration scenario the spec's Module 22 (Digital Twin
Simulator) was built for: physics-driven simulator on one side, full
backend pipeline on the other, no real hardware needed.
"""

import asyncio
import json
import time

import pytest

from src.attestation import AttestationEngine
from src.test_engine import TestEngine
from src.types import DeviceInfo, Event, PollResult, TestRequest

from simulator.sim import (
    UPS_REG_BATTERY_PCT,
    UPS_REG_OUTPUT_VOLTAGE,
    UPS_REG_STATUS,
    UPS_REG_TEST_INITIATE,
    UPSSimulator,
)


def _ups_device() -> DeviceInfo:
    """Config Context for a UPS, register addresses matching the simulator."""
    return DeviceInfo(
        device_id="ups-1",
        name="ups-A1",
        primary_ip="10.0.0.5",
        device_type_slug="generic-ups",
        config_context={
            "protocol": "modbus_tcp",
            "connection": {"port": 502, "unit_id": 1, "byte_order": "big", "word_order": "big"},
            "active_tests": [
                {
                    "name": "ups_battery_transfer",
                    "preconditions": [
                        {"register": "battery_pct", "operator": "gte", "value": 80},
                        {"register": "ups_status", "operator": "eq", "value": 1},
                    ],
                    "command": {
                        "address": UPS_REG_TEST_INITIATE, "value": 1, "function_code": 6,
                    },
                    "abort_conditions": [
                        {"register": "output_voltage", "operator": "lt", "value": 4500},
                    ],
                    "monitor": ["output_voltage", "battery_pct", "ups_status"],
                    "monitor_interval_ms": 1,
                    "monitor_duration_seconds": 0.1,
                    "acceptance": [
                        {"register": "output_voltage", "metric": "settled_value",
                         "operator": "gte", "value": 4700},
                    ],
                    "restore": {"address": UPS_REG_TEST_INITIATE, "value": 0},
                    "restore_verify": [
                        {"register": "ups_status", "operator": "eq", "value": 1},
                    ],
                    "restore_timeout_seconds": 1,
                },
            ],
        },
        protocol="modbus_tcp",
        site="DC1",
        rack="A1",
        position=1,
    )


def _make_poller_and_writer(simulator: UPSSimulator):
    """Bridge the simulator's MemoryRegisters to PollResult/write_register
    callable signatures the rest of the platform expects.
    """
    name_to_addr = {
        "ups_status": UPS_REG_STATUS,
        "output_voltage": UPS_REG_OUTPUT_VOLTAGE,
        "battery_pct": UPS_REG_BATTERY_PCT,
        "battery_voltage": UPS_REG_OUTPUT_VOLTAGE,
    }

    async def poller(device: DeviceInfo) -> PollResult:
        # Tick the simulator's physics by 1ms per poll.
        simulator.advance(0.001)
        meas = {}
        raw = {}
        for name, addr in name_to_addr.items():
            v = simulator.registers.read(addr)
            meas[name] = float(v)
            raw[name] = f"{v:04x}"
        return PollResult(
            device_id=device.device_id,
            timestamp_ns=time.time_ns(),
            measurements=meas,
            raw_bytes=raw,
            protocol="modbus_tcp",
            source_ip=device.primary_ip,
            success=True,
        )

    async def writer(device, address, value, function_code=6):
        simulator.registers.write(address, int(value))
        return True, None

    return poller, writer


async def test_full_ups_battery_transfer_test_against_simulator(
    integration_config, integration_minio, integration_influx
):
    sim = UPSSimulator()
    poller, writer = _make_poller_and_writer(sim)

    attest = AttestationEngine(
        facility_name=integration_config.facility_name,
        minio_bucket=integration_config.minio_bucket,
        minio_client=integration_minio,
        wal_dir="/tmp/wal-integration-1",
    )
    events: asyncio.Queue[Event] = asyncio.Queue()
    engine = TestEngine(
        integration_config, integration_influx, attest, events,
        poller=poller, writer=writer, manual_confirm_timeout=1.0,
    )

    consumer = asyncio.create_task(attest.run())
    try:
        result = await engine.execute(
            TestRequest(test_id="full-1", device_id="ups-1", test_name="ups_battery_transfer"),
            _ups_device(),
        )
    finally:
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass
        await attest.drain()

    # 1. Test ran end to end.
    assert result.test_id == "full-1"
    assert result.precondition_results, "preconditions should have been evaluated"
    assert all(p["passed"] for p in result.precondition_results)

    # 2. Acceptance criterion validates against settled-on-battery voltage.
    assert result.status in {"passed", "failed"}, f"unexpected status {result.status}"
    assert result.acceptance_results
    actual_voltage = result.acceptance_results[0]["actual"]
    assert actual_voltage is not None, "monitoring should have collected voltage readings"

    # 3. Restore brought the UPS back online.
    assert result.restore_success is True
    assert sim.registers.read(UPS_REG_STATUS) == 1

    # 4. Influx received writes during monitoring (one per successful poll).
    test_writes = [w for w in integration_influx.writes if w[1] == "full-1"]
    assert len(test_writes) > 0

    # 5. Attestation chain has records tagged with our test_id and links
    #    correctly across the entire chain.
    attested = sorted(integration_minio.store)
    assert len(attested) >= len(test_writes)  # at least one record per poll

    chain = [json.loads(integration_minio.store[k]) for k in attested]
    assert chain[0]["previous_hash"] == "0" * 64
    for prev, curr in zip(chain, chain[1:]):
        assert curr["previous_hash"] == prev["hash"], "chain link broken in MinIO"

    # 6. At least one record carries our test_id (so post-test queries can
    #    isolate test data from baseline polling).
    test_id_records = [r for r in chain if r.get("test_id") == "full-1"]
    assert len(test_id_records) > 0

    # 7. verify_chain agrees the chain is intact.
    verification = attest.verify_chain()
    assert verification["chain_valid"] is True


async def test_abort_condition_triggers_restore_against_simulator(
    integration_config, integration_minio, integration_influx
):
    """Set an abort_condition that the simulator will hit during the dip,
    and verify the test aborts and runs the restore."""
    sim = UPSSimulator()
    poller, writer = _make_poller_and_writer(sim)

    device = _ups_device()
    # Tighten the abort threshold so the dip to 4600 trips it.
    device.config_context["active_tests"][0]["abort_conditions"] = [
        {"register": "output_voltage", "operator": "lt", "value": 4700},
    ]
    device.config_context["active_tests"][0]["monitor_duration_seconds"] = 0.5

    attest = AttestationEngine(
        facility_name=integration_config.facility_name,
        minio_bucket=integration_config.minio_bucket,
        minio_client=integration_minio,
        wal_dir="/tmp/wal-integration-2",
    )
    events: asyncio.Queue[Event] = asyncio.Queue()
    engine = TestEngine(
        integration_config, integration_influx, attest, events,
        poller=poller, writer=writer, manual_confirm_timeout=1.0,
    )

    consumer = asyncio.create_task(attest.run())
    try:
        result = await engine.execute(
            TestRequest(test_id="abort-1", device_id="ups-1", test_name="ups_battery_transfer"),
            device,
        )
    finally:
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass
        await attest.drain()

    assert result.abort_triggered is True
    assert "output_voltage lt 4700" in result.abort_reason
    assert result.status == "aborted"
    # Simulator should have been told to stop the test.
    assert sim.registers.read(UPS_REG_TEST_INITIATE) == 0
    assert sim.registers.read(UPS_REG_STATUS) == 1


async def test_chain_survives_orchestrator_dispatch(
    integration_config, integration_minio, integration_influx
):
    """Run two tests through the orchestrator on independent devices and
    confirm the chain stays intact."""
    from src.orchestrator import Orchestrator, build_power_graph_from_connections

    sims = [UPSSimulator(), UPSSimulator()]
    pollers = [_make_poller_and_writer(s) for s in sims]

    devices = []
    for i, _ in enumerate(sims):
        d = _ups_device()
        d.device_id = f"ups-{i}"
        d.name = f"ups-{i}"
        d.config_context["active_tests"][0]["monitor_duration_seconds"] = 0.05
        devices.append(d)

    attest = AttestationEngine(
        facility_name=integration_config.facility_name,
        minio_bucket=integration_config.minio_bucket,
        minio_client=integration_minio,
        wal_dir="/tmp/wal-integration-3",
    )
    events: asyncio.Queue[Event] = asyncio.Queue()

    # Pick the right sim for each device by id.
    async def poller(device):
        idx = int(device.device_id.split("-")[1])
        p, _ = pollers[idx]
        return await p(device)

    async def writer(device, address, value, function_code=6):
        idx = int(device.device_id.split("-")[1])
        _, w = pollers[idx]
        return await w(device, address, value, function_code)

    engine = TestEngine(
        integration_config, integration_influx, attest, events,
        poller=poller, writer=writer, manual_confirm_timeout=1.0,
    )

    by_id = {d.device_id: d for d in devices}

    async def device_loader(dev_id):
        return by_id.get(dev_id)

    orch = Orchestrator(
        integration_config,
        executor=engine.execute,
        event_queue=events,
        device_loader=device_loader,
        power_graph=build_power_graph_from_connections([]),  # independent
    )

    consumer = asyncio.create_task(attest.run())
    orch_task = asyncio.create_task(orch.run())
    try:
        await orch.submit(TestRequest(test_id="orch-1", device_id="ups-0", test_name="ups_battery_transfer"))
        await orch.submit(TestRequest(test_id="orch-2", device_id="ups-1", test_name="ups_battery_transfer"))
        r1 = await orch.wait_for("orch-1", timeout=5.0)
        r2 = await orch.wait_for("orch-2", timeout=5.0)
    finally:
        orch_task.cancel()
        consumer.cancel()
        for t in (orch_task, consumer):
            try:
                await t
            except asyncio.CancelledError:
                pass
        await attest.drain()

    assert r1.status in {"passed", "failed"}
    assert r2.status in {"passed", "failed"}
    assert attest.verify_chain()["chain_valid"] is True
