"""Tests for Module 6 — Modbus Worker.

The worker wires Modules 2/3/4/5 together. Tests inject fakes for:
  - the device loader (so no NetBox is needed)
  - the Modbus poller (so no simulator is needed)
  - InfluxWriter and AttestationEngine (so no Influx/MinIO is needed)
"""

import asyncio
from dataclasses import dataclass

import pytest

from src.types import AttestationRecord, DeviceInfo, Event, PollResult
from src.workers.modbus_worker import ModbusWorker, UNREACHABLE_THRESHOLD


@dataclass
class FakeConfig:
    netbox_url: str = "http://nb"
    netbox_token: str = "tok"
    max_concurrent_polls: int = 10


class FakeInflux:
    def __init__(self):
        self.writes: list[tuple[PollResult, str | None]] = []

    async def write_poll(self, result, test_id=None):
        self.writes.append((result, test_id))


class FakeAttestation:
    def __init__(self):
        self.submitted: list[AttestationRecord] = []

    async def submit(self, record):
        self.submitted.append(record)


def _device(dev_id: str, poll_interval=30) -> DeviceInfo:
    return DeviceInfo(
        device_id=dev_id,
        name=f"dev-{dev_id}",
        primary_ip=f"10.0.0.{int(dev_id) if dev_id.isdigit() else 1}",
        device_type_slug="cm2000",
        config_context={"poll_interval_seconds": poll_interval, "protocol": "modbus_tcp"},
        protocol="modbus_tcp",
        site="DC1",
        rack="A3",
        position=int(dev_id) if dev_id.isdigit() else 0,
    )


def _poll_ok(device, measurements=None) -> PollResult:
    return PollResult(
        device_id=device.device_id,
        timestamp_ns=1_712_847_600_000_000_000,
        measurements=measurements or {"voltage": 480.0, "current": 50.0},
        raw_bytes={"voltage": "01e0", "current": "0032"},
        protocol="modbus_tcp",
        source_ip=device.primary_ip,
        success=True,
    )


def _poll_fail(device) -> PollResult:
    return PollResult(
        device_id=device.device_id,
        timestamp_ns=1_712_847_600_000_000_000,
        measurements={},
        raw_bytes={},
        protocol="modbus_tcp",
        source_ip=device.primary_ip,
        success=False,
        errors={"_connection": "timeout"},
    )


def _make_worker(devices, poller_fn):
    influx = FakeInflux()
    attest = FakeAttestation()
    events: asyncio.Queue[Event] = asyncio.Queue()
    worker = ModbusWorker(
        FakeConfig(),
        influx,
        attest,
        events,
        poller=poller_fn,
        device_loader=lambda: _devs(devices),
    )
    return worker, influx, attest, events


async def _devs(devices):
    return devices


async def _drain(queue: asyncio.Queue[Event]) -> list[Event]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


# --- Happy path --------------------------------------------------------------


async def test_successful_poll_writes_influx_and_submits_attestations():
    device = _device("1")

    async def poll(d):
        return _poll_ok(d)

    worker, influx, attest, events = _make_worker([device], poll)
    await worker.refresh_devices()
    successes, failures = await worker.poll_all_once()

    assert (successes, failures) == (1, 0)
    assert len(influx.writes) == 1
    assert influx.writes[0][0].device_id == "1"
    # Two measurements => two attestation submissions.
    assert len(attest.submitted) == 2
    assert {r.measurement for r in attest.submitted} == {"voltage", "current"}

    drained = await _drain(events)
    assert len(drained) == 1
    assert drained[0].event_type == "poll_result"
    assert drained[0].data["device_id"] == "1"


async def test_multiple_devices_polled_in_parallel():
    devices = [_device(str(i)) for i in range(5)]
    call_order: list[str] = []

    async def poll(d):
        call_order.append(d.device_id)
        await asyncio.sleep(0)  # yield to exercise concurrency
        return _poll_ok(d)

    worker, influx, _, _ = _make_worker(devices, poll)
    await worker.refresh_devices()
    successes, _ = await worker.poll_all_once()

    assert successes == 5
    assert len(influx.writes) == 5
    assert set(call_order) == {"0", "1", "2", "3", "4"}


async def test_respects_max_concurrent_polls_semaphore():
    devices = [_device(str(i)) for i in range(10)]
    concurrency: list[int] = []
    in_flight = 0

    async def poll(d):
        nonlocal in_flight
        in_flight += 1
        concurrency.append(in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return _poll_ok(d)

    cfg = FakeConfig(max_concurrent_polls=3)
    worker = ModbusWorker(
        cfg,
        FakeInflux(),
        FakeAttestation(),
        asyncio.Queue(),
        poller=poll,
        device_loader=lambda: _devs(devices),
    )
    await worker.refresh_devices()
    await worker.poll_all_once()

    assert max(concurrency) <= 3


# --- Failure handling --------------------------------------------------------


async def test_single_failure_does_not_emit_unreachable():
    device = _device("1")

    async def poll(d):
        return _poll_fail(d)

    worker, _, _, events = _make_worker([device], poll)
    await worker.refresh_devices()
    successes, failures = await worker.poll_all_once()

    assert (successes, failures) == (0, 1)
    assert worker.failures["1"] == 1
    assert await _drain(events) == []


async def test_three_consecutive_failures_emit_unreachable_once():
    device = _device("1")

    async def poll(d):
        return _poll_fail(d)

    worker, _, _, events = _make_worker([device], poll)
    await worker.refresh_devices()

    for _ in range(UNREACHABLE_THRESHOLD):
        await worker.poll_all_once()

    drained = await _drain(events)
    assert len(drained) == 1
    assert drained[0].event_type == "worker_health"
    assert drained[0].data == {"device_id": "1", "status": "UNREACHABLE"}

    # Subsequent failures must not re-emit the event.
    await worker.poll_all_once()
    assert await _drain(events) == []
    assert worker.failures["1"] == UNREACHABLE_THRESHOLD + 1


async def test_recovery_after_unreachable_emits_recovered_event():
    device = _device("1")
    calls = {"n": 0}

    async def poll(d):
        calls["n"] += 1
        return _poll_fail(d) if calls["n"] <= UNREACHABLE_THRESHOLD else _poll_ok(d)

    worker, _, _, events = _make_worker([device], poll)
    await worker.refresh_devices()

    # Go UNREACHABLE
    for _ in range(UNREACHABLE_THRESHOLD):
        await worker.poll_all_once()
    # Then recover
    await worker.poll_all_once()

    drained = await _drain(events)
    types = [(e.event_type, e.data.get("status") or e.data.get("device_id")) for e in drained]
    assert ("worker_health", "UNREACHABLE") in [(t[0], t[1]) for t in types if t[0] == "worker_health"]
    recovered = [e for e in drained if e.event_type == "worker_health" and e.data.get("status") == "RECOVERED"]
    assert len(recovered) == 1
    assert worker.failures["1"] == 0


async def test_transient_failure_before_threshold_resets_silently():
    device = _device("1")
    calls = {"n": 0}

    async def poll(d):
        calls["n"] += 1
        return _poll_fail(d) if calls["n"] == 1 else _poll_ok(d)

    worker, _, _, events = _make_worker([device], poll)
    await worker.refresh_devices()

    await worker.poll_all_once()  # fail 1
    assert worker.failures["1"] == 1
    await worker.poll_all_once()  # recover
    assert worker.failures["1"] == 0

    # No UNREACHABLE/RECOVERED events — just the one poll_result.
    drained = await _drain(events)
    assert [e.event_type for e in drained] == ["poll_result"]


# --- Scheduling --------------------------------------------------------------


async def test_next_interval_uses_shortest_poll_interval():
    devices = [
        _device("a", poll_interval=60),
        _device("b", poll_interval=5),
        _device("c", poll_interval=30),
    ]

    async def poll(d):
        return _poll_ok(d)

    worker, _, _, _ = _make_worker(devices, poll)
    await worker.refresh_devices()
    assert worker._next_interval_seconds() == 5


async def test_next_interval_default_with_no_devices():
    async def poll(d):
        return _poll_ok(d)

    worker, _, _, _ = _make_worker([], poll)
    await worker.refresh_devices()
    assert worker._next_interval_seconds() == 30


# --- Device loading ----------------------------------------------------------


# --- Spec §5.1 firmware mismatch ---------------------------------------------


def _device_with_fw(expected_fw: str) -> DeviceInfo:
    d = _device("1")
    d.config_context["firmware_version"] = expected_fw
    return d


async def test_firmware_match_emits_no_warning():
    # Device config expects FW "321". Device reports 321.0 — match by str(321.0)
    # vs "321". We compare via str() so float(321.0) → "321.0" != "321". Cover
    # that nuance: spec config should declare the expected value the way it
    # comes out of the decoder. Use "321.0" here.
    device = _device_with_fw("321.0")

    async def poll(d):
        return _poll_ok(d, measurements={"firmware_version": 321.0, "voltage": 480.0})

    worker, _, _, events = _make_worker([device], poll)
    await worker.refresh_devices()
    await worker.poll_all_once()

    drained = await _drain(events)
    assert all(e.event_type != "firmware_mismatch" for e in drained)


async def test_firmware_mismatch_emits_warning_once():
    device = _device_with_fw("321.0")

    async def poll(d):
        return _poll_ok(d, measurements={"firmware_version": 200.0, "voltage": 480.0})

    worker, _, _, events = _make_worker([device], poll)
    await worker.refresh_devices()

    await worker.poll_all_once()
    await worker.poll_all_once()
    await worker.poll_all_once()

    drained = await _drain(events)
    fw_events = [e for e in drained if e.event_type == "firmware_mismatch"]
    assert len(fw_events) == 1
    assert fw_events[0].data == {
        "device_id": "1",
        "expected": "321.0",
        "actual": "200.0",
    }


async def test_firmware_change_emits_new_warning():
    device = _device_with_fw("321.0")
    fw_seq = iter([200.0, 200.0, 250.0, 250.0])

    async def poll(d):
        return _poll_ok(d, measurements={"firmware_version": next(fw_seq), "voltage": 480.0})

    worker, _, _, events = _make_worker([device], poll)
    await worker.refresh_devices()

    for _ in range(4):
        await worker.poll_all_once()

    drained = await _drain(events)
    fw_events = [e for e in drained if e.event_type == "firmware_mismatch"]
    assert {e.data["actual"] for e in fw_events} == {"200.0", "250.0"}


async def test_no_expected_firmware_means_no_warning():
    device = _device("1")  # no firmware_version in config_context

    async def poll(d):
        return _poll_ok(d, measurements={"firmware_version": 200.0, "voltage": 480.0})

    worker, _, _, events = _make_worker([device], poll)
    await worker.refresh_devices()
    await worker.poll_all_once()

    drained = await _drain(events)
    assert all(e.event_type != "firmware_mismatch" for e in drained)


async def test_no_actual_firmware_reading_skips_check():
    device = _device_with_fw("321.0")

    async def poll(d):
        return _poll_ok(d, measurements={"voltage": 480.0})  # no firmware reg

    worker, _, _, events = _make_worker([device], poll)
    await worker.refresh_devices()
    await worker.poll_all_once()

    drained = await _drain(events)
    assert all(e.event_type != "firmware_mismatch" for e in drained)


async def test_refresh_devices_calls_device_loader():
    calls = {"n": 0}

    async def loader():
        calls["n"] += 1
        return [_device("1"), _device("2")]

    worker = ModbusWorker(
        FakeConfig(),
        FakeInflux(),
        FakeAttestation(),
        asyncio.Queue(),
        poller=lambda d: _poll_ok(d),
        device_loader=loader,
    )
    await worker.refresh_devices()
    assert calls["n"] == 1
    assert len(worker.devices) == 2
