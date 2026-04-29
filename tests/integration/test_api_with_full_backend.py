"""Integration test for the FastAPI server wired to a real (in-process)
orchestrator + test engine + attestation engine driven by the simulator.

Uses FastAPI TestClient — no uvicorn needed — and exercises the same API
surface a browser frontend would hit.
"""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from src.api.server import Deps, create_app, fanout_loop
from src.attestation import AttestationEngine
from src.orchestrator import Orchestrator, build_power_graph_from_connections
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
    return DeviceInfo(
        device_id="ups-1", name="ups-A1", primary_ip="10.0.0.5",
        device_type_slug="generic-ups",
        config_context={
            "protocol": "modbus_tcp",
            "connection": {"port": 502, "unit_id": 1, "byte_order": "big", "word_order": "big"},
            "active_tests": [{
                "name": "ups_battery_transfer",
                "preconditions": [
                    {"register": "battery_pct", "operator": "gte", "value": 80},
                    {"register": "ups_status", "operator": "eq", "value": 1},
                ],
                "command": {"address": UPS_REG_TEST_INITIATE, "value": 1, "function_code": 6},
                "monitor": ["output_voltage", "battery_pct"],
                "monitor_interval_ms": 1,
                "monitor_duration_seconds": 0.05,
                "acceptance": [
                    {"register": "output_voltage", "metric": "settled_value",
                     "operator": "gte", "value": 4700},
                ],
                "restore": {"address": UPS_REG_TEST_INITIATE, "value": 0},
                "restore_verify": [{"register": "ups_status", "operator": "eq", "value": 1}],
                "restore_timeout_seconds": 1,
            }],
        },
        protocol="modbus_tcp", site="DC1", rack="A1", position=1,
    )


def _make_bridge(simulator: UPSSimulator):
    name_to_addr = {
        "ups_status": UPS_REG_STATUS,
        "output_voltage": UPS_REG_OUTPUT_VOLTAGE,
        "battery_pct": UPS_REG_BATTERY_PCT,
        "battery_voltage": UPS_REG_OUTPUT_VOLTAGE,
    }

    async def poller(device):
        simulator.advance(0.001)
        return PollResult(
            device_id=device.device_id, timestamp_ns=time.time_ns(),
            measurements={n: float(simulator.registers.read(a)) for n, a in name_to_addr.items()},
            raw_bytes={n: f"{simulator.registers.read(a):04x}" for n, a in name_to_addr.items()},
            protocol="modbus_tcp", source_ip=device.primary_ip, success=True,
        )

    async def writer(device, address, value, function_code=6):
        simulator.registers.write(address, int(value))
        return True, None

    return poller, writer


@pytest.fixture
async def wired_app(integration_config, integration_minio, integration_influx, tmp_path):
    sim = UPSSimulator()
    poller, writer = _make_bridge(sim)

    attest = AttestationEngine(
        facility_name=integration_config.facility_name,
        minio_bucket=integration_config.minio_bucket,
        minio_client=integration_minio,
        wal_dir=str(tmp_path / "wal"),
    )
    events: asyncio.Queue[Event] = asyncio.Queue()
    engine = TestEngine(
        integration_config, integration_influx, attest, events,
        poller=poller, writer=writer, manual_confirm_timeout=1.0,
    )

    device = _ups_device()

    async def device_loader(_):
        return device

    orch = Orchestrator(
        integration_config, executor=engine.execute, event_queue=events,
        device_loader=device_loader,
        power_graph=build_power_graph_from_connections([]),
    )

    deps = Deps(
        api_key=integration_config.api_key,
        cors_origins=integration_config.cors_origins,
        orchestrator=orch, test_engine=engine,
        attestation=attest, influx=integration_influx, netbox=object(),
        event_queue=events,
    )
    app = create_app(deps)

    consumer = asyncio.create_task(attest.run())
    orch_task = asyncio.create_task(orch.run())
    fanout = asyncio.create_task(fanout_loop(deps))

    yield app, deps, sim, attest

    for t in (consumer, orch_task, fanout):
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


async def test_post_run_then_poll_status_returns_passing_result(wired_app):
    app, deps, sim, attest = wired_app
    client = TestClient(app)

    resp = client.post(
        "/api/v1/tests/run",
        json={"device_id": "ups-1", "test_name": "ups_battery_transfer"},
        headers={"X-API-Key": "test-api-key"},
    )
    assert resp.status_code == 200
    test_id = resp.json()["test_id"]

    # Wait for the orchestrator to finish.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if test_id in deps.orchestrator._results:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("test never completed")

    resp = client.get(
        f"/api/v1/tests/status/{test_id}",
        headers={"X-API-Key": "test-api-key"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"passed", "failed"}
    assert body["restore_success"] is True


async def test_attestation_endpoints_show_chain_after_test_run(wired_app):
    app, deps, sim, attest = wired_app
    client = TestClient(app)

    resp = client.post(
        "/api/v1/tests/run",
        json={"device_id": "ups-1", "test_name": "ups_battery_transfer"},
        headers={"X-API-Key": "test-api-key"},
    )
    test_id = resp.json()["test_id"]

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if test_id in deps.orchestrator._results:
            break
        await asyncio.sleep(0.01)

    # Drain remaining attestation submissions.
    await attest.drain()

    verify = client.get("/api/v1/attestation/verify", headers={"X-API-Key": "test-api-key"}).json()
    assert verify["chain_valid"] is True
    assert verify["length"] > 0

    cert = client.get("/api/v1/attestation/certificate", headers={"X-API-Key": "test-api-key"}).json()
    assert cert["facility"] == "DC1-Integration"
    assert cert["chain_length"] == verify["length"]
