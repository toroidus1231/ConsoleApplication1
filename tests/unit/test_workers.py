"""Tests for Module 6 — generic ProtocolWorker + Modbus worker wiring.

In-memory fakes stand in for the InfluxDB writer, the attestation engine, and
the NetBox device reader, so the poll→attest→write loop is exercised end to end
with no live devices.
"""

import asyncio

from src.types import DeviceInfo, PollResult
from src.workers.base import ProtocolWorker
from src.workers.modbus_worker import make_modbus_worker


class FakeInflux:
    def __init__(self):
        self.writes: list[PollResult] = []

    async def write_poll(self, result, test_id=None):
        self.writes.append(result)


class FakeAttestation:
    def __init__(self):
        self.submitted = []

    async def submit(self, record):
        self.submitted.append(record)


def _device(dev_id="dev-1", interval=30):
    return DeviceInfo(
        device_id=dev_id,
        name=f"name-{dev_id}",
        primary_ip="10.0.0.5",
        device_type_slug="cm2000",
        config_context={"protocol": "modbus_tcp", "poll_interval_seconds": interval},
        protocol="modbus_tcp",
        site="DC1",
        rack="A3",
        position=10,
    )


def _ok_result(dev_id="dev-1"):
    return PollResult(
        device_id=dev_id,
        timestamp_ns=1_712_847_600_000_000_000,
        measurements={"voltage_ab": 480.0, "frequency_hz": 60.0},
        raw_bytes={"voltage_ab": "01e0", "frequency_hz": "003c"},
        protocol="modbus_tcp",
        source_ip="10.0.0.5",
        success=True,
    )


def _fail_result(dev_id="dev-1"):
    return PollResult(
        device_id=dev_id,
        timestamp_ns=1_712_847_600_000_000_000,
        measurements={},
        raw_bytes={},
        protocol="modbus_tcp",
        source_ip="10.0.0.5",
        success=False,
        errors={"_connection": "timeout"},
    )


def _worker(poll_fn, devices, *, influx=None, attestation=None, event_queue=None):
    async def reader(url, token, protocol):
        return list(devices)

    return ProtocolWorker(
        protocol="modbus_tcp",
        poll_fn=poll_fn,
        influx=influx or FakeInflux(),
        attestation=attestation or FakeAttestation(),
        event_queue=event_queue,
        device_reader=reader,
    )


async def test_successful_poll_writes_influx_and_attests_each_measurement():
    influx, att = FakeInflux(), FakeAttestation()
    eq = asyncio.Queue()

    async def poll_fn(device):
        return _ok_result(device.device_id)

    w = _worker(poll_fn, [_device()], influx=influx, attestation=att, event_queue=eq)
    await w.refresh_devices()
    await w.poll_once()

    assert len(influx.writes) == 1
    # One AttestationRecord per measurement, with worker_id left for the engine.
    assert {r.measurement for r in att.submitted} == {"voltage_ab", "frequency_hz"}
    assert all(r.worker_id == "" for r in att.submitted)
    voltage = next(r for r in att.submitted if r.measurement == "voltage_ab")
    assert voltage.value == 480.0
    assert voltage.raw_bytes == "01e0"
    # A poll_result event was emitted.
    evt = eq.get_nowait()
    assert evt.event_type == "poll_result"
    assert evt.data["device_id"] == "dev-1"


async def test_three_consecutive_failures_emit_unreachable_once():
    eq = asyncio.Queue()

    async def poll_fn(device):
        return _fail_result(device.device_id)

    w = _worker(poll_fn, [_device()], event_queue=eq)
    await w.refresh_devices()

    for _ in range(4):  # poll four cycles
        await w.poll_once()

    assert w.failures["dev-1"] == 4
    assert "dev-1" in w.unreachable
    # Exactly one UNREACHABLE event despite four failures.
    health = []
    while not eq.empty():
        health.append(eq.get_nowait())
    unreachable = [e for e in health if e.data.get("status") == "UNREACHABLE"]
    assert len(unreachable) == 1
    assert unreachable[0].event_type == "worker_health"
    assert unreachable[0].data["consecutive_failures"] == 3


async def test_failure_then_recovery_resets_and_emits_recovered():
    eq = asyncio.Queue()
    state = {"fail": True}

    async def poll_fn(device):
        return _fail_result() if state["fail"] else _ok_result()

    w = _worker(poll_fn, [_device()], event_queue=eq)
    await w.refresh_devices()

    for _ in range(3):
        await w.poll_once()
    assert "dev-1" in w.unreachable

    state["fail"] = False
    await w.poll_once()

    assert w.failures["dev-1"] == 0
    assert "dev-1" not in w.unreachable
    statuses = []
    while not eq.empty():
        statuses.append(eq.get_nowait().data.get("status"))
    assert "RECOVERED" in statuses


async def test_failed_poll_does_not_write_or_attest():
    influx, att = FakeInflux(), FakeAttestation()

    async def poll_fn(device):
        return _fail_result()

    w = _worker(poll_fn, [_device()], influx=influx, attestation=att)
    await w.refresh_devices()
    await w.poll_once()

    assert influx.writes == []
    assert att.submitted == []


async def test_poll_once_handles_many_devices():
    devices = [_device(f"dev-{i}") for i in range(10)]

    async def poll_fn(device):
        return _ok_result(device.device_id)

    influx = FakeInflux()
    w = _worker(poll_fn, devices, influx=influx)
    await w.refresh_devices()
    results = await w.poll_once()

    assert len(results) == 10
    assert len(influx.writes) == 10


async def test_min_interval_picks_shortest():
    devices = [_device("a", interval=30), _device("b", interval=5), _device("c", interval=60)]

    async def poll_fn(device):
        return _ok_result(device.device_id)

    w = _worker(poll_fn, devices)
    await w.refresh_devices()
    assert w._min_interval() == 5


def test_make_modbus_worker_binds_protocol_and_config():
    class Cfg:
        netbox_url = "http://nb"
        netbox_token = "tok"
        max_concurrent_polls = 25

    w = make_modbus_worker(Cfg(), FakeInflux(), FakeAttestation(), asyncio.Queue())
    assert w.protocol == "modbus_tcp"
    assert w._max_concurrent_polls == 25
    assert w._netbox_url == "http://nb"
