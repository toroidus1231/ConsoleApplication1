"""Tests for Module 10 — Active Test Engine.

Drives the full state machine with scripted fakes: a poller returning canned
PollResults, an in-memory attestation engine, an InfluxDB stub, a recording
command function, and a no-op sleep — so every path (preconditions, manual
confirm + timeout, identity guard, monitoring, abort, watchdog, acceptance,
restore) is exercised deterministically with no device and no real time.
"""

import asyncio

import pytest

from src.test_engine import TestEngine, compute_metric, evaluate
from src.types import DeviceInfo, PollResult, TestRequest


class FakeAttestation:
    def __init__(self):
        self.submitted = []

    async def submit(self, record):
        # The real engine fills nonce; emulate so evidence_hashes populates.
        record.nonce = f"n-{len(self.submitted) + 1}"
        self.submitted.append(record)


class FakeInflux:
    def __init__(self):
        self.writes = []

    async def write_poll(self, result, test_id=None):
        self.writes.append((result, test_id))


async def _noop_sleep(_):
    return None


def _pr(measurements, *, success=True, raw=None):
    return PollResult(
        device_id="dev-1",
        timestamp_ns=1_712_847_600_000_000_000,
        measurements=measurements,
        raw_bytes=raw or {k: "00" for k in measurements},
        protocol="modbus_tcp",
        source_ip="10.0.0.5",
        success=success,
    )


def _seq_poller(results):
    """Return an async poll_fn yielding results in order, repeating the last."""
    box = {"i": 0}

    async def poll_fn(device):
        i = min(box["i"], len(results) - 1)
        box["i"] += 1
        return results[i]

    return poll_fn


def _device(active_tests, *, identity_register=None):
    ctx = {"protocol": "modbus_tcp", "connection": {"port": 502, "unit_id": 1}, "active_tests": active_tests}
    if identity_register:
        ctx["identity_register"] = identity_register
    return DeviceInfo(
        device_id="dev-1", name="ups-1", primary_ip="10.0.0.5", device_type_slug="ups",
        config_context=ctx, protocol="modbus_tcp", site="DC1", rack="A1", position=1,
    )


def _engine(poll_fn, *, command_fn=None, commands=None, event_queue=None, manual_timeout=1800.0):
    if command_fn is None:
        commands = commands if commands is not None else []

        async def command_fn(device, command):  # noqa: ANN001
            commands.append(command)

    return TestEngine(
        attestation=FakeAttestation(),
        influx=FakeInflux(),
        event_queue=event_queue,
        poll_fn=poll_fn,
        command_fn=command_fn,
        sleep_fn=_noop_sleep,
        manual_timeout_seconds=manual_timeout,
    )


# --- Pure helpers ------------------------------------------------------------

def test_evaluate_operators():
    assert evaluate(5, "gt", 3) is True
    assert evaluate(5, "lt", 3) is False
    assert evaluate(5, "gte", 5) is True
    assert evaluate(5, "lte", 4) is False
    assert evaluate(5, "eq", 5) is True
    assert evaluate(5, "neq", 5) is False
    assert evaluate(None, "gt", 3) is False
    assert evaluate(5, "unknown_op", 3) is False


def test_compute_metric_variants():
    collected = [
        PollResult("d", 1_000_000_000, {"v": 10.0}, {}, "modbus_tcp", "ip", True),
        PollResult("d", 1_500_000_000, {"v": 20.0}, {}, "modbus_tcp", "ip", True),
        PollResult("d", 2_000_000_000, {"v": 30.0}, {}, "modbus_tcp", "ip", True),
    ]
    values = [10.0, 20.0, 30.0]
    assert compute_metric("max", values, collected, "v") == 30.0
    assert compute_metric("min", values, collected, "v") == 10.0
    assert compute_metric("avg", values, collected, "v") == 20.0
    assert compute_metric("delta", values, collected, "v") == 20.0
    assert compute_metric("settled_value", values, collected, "v") == 30.0
    # crosses >=25 at the third sample → 1000ms after the first.
    assert compute_metric("time_to_value", values, collected, "v", threshold=25) == pytest.approx(1000.0)
    # 20 units over 1 second.
    assert compute_metric("rate_of_change", values, collected, "v") == pytest.approx(20.0)
    assert compute_metric("max", [], collected, "v") is None


# --- State machine -----------------------------------------------------------

async def test_unknown_test_name_fails():
    eng = _engine(_seq_poller([_pr({})]))
    result = await eng.execute(TestRequest(device_id="dev-1", test_name="nope"), _device([]))
    assert result.status == "failed"
    assert "not found" in result.abort_reason


async def test_precondition_failure_blocks_command():
    commands = []
    test_def = {
        "name": "battery_transfer",
        "preconditions": [{"register": "battery_percent", "operator": "gte", "value": 80}],
        "command": {"address": 3, "value": 1, "function_code": 6},
    }
    # battery at 50% → precondition fails.
    eng = _engine(_seq_poller([_pr({"battery_percent": 50.0})]), commands=commands)
    result = await eng.execute(TestRequest(device_id="dev-1", test_name="battery_transfer"), _device([test_def]))
    assert result.status == "precondition_failed"
    assert result.precondition_results[0]["passed"] is False
    assert commands == []  # never commanded


async def test_identity_mismatch_aborts_before_write():
    commands = []
    test_def = {"name": "t", "command": {"address": 3, "value": 1, "function_code": 6}}
    device = _device([test_def], identity_register={"register": "model_id", "expected": 2000})
    eng = _engine(_seq_poller([_pr({"model_id": 9999.0})]), commands=commands)
    result = await eng.execute(TestRequest(device_id="dev-1", test_name="t"), device)
    assert result.status == "aborted"
    assert "Identity mismatch" in result.abort_reason
    assert commands == []


async def test_happy_path_passes_with_acceptance_and_restore():
    commands = []
    test_def = {
        "name": "battery_transfer",
        "command": {"address": 3, "value": 1, "function_code": 6},
        "monitor": ["output_voltage"],
        "monitor_interval_ms": 1000,
        "monitor_duration_seconds": 3,  # → 3 samples
        "acceptance": [{"register": "output_voltage", "metric": "min", "operator": "gte", "value": 228}],
        "restore": {"address": 3, "value": 0, "function_code": 6},
        "restore_verify": [{"register": "ups_status", "operator": "eq", "value": 2}],
        "restore_timeout_seconds": 5,
    }
    # 3 monitoring samples (all >=228), then restore-verify sees status 2.
    polls = [
        _pr({"output_voltage": 240.0}),
        _pr({"output_voltage": 235.0}),
        _pr({"output_voltage": 230.0}),
        _pr({"ups_status": 2.0}),
    ]
    att = FakeAttestation()
    influx = FakeInflux()
    eng = TestEngine(attestation=att, influx=influx, poll_fn=_seq_poller(polls),
                     command_fn=lambda d, c: commands.append(c) or _ok(), sleep_fn=_noop_sleep)
    result = await eng.execute(TestRequest(device_id="dev-1", test_name="battery_transfer"), _device([test_def]))
    assert result.status == "passed"
    assert result.acceptance_results[0]["actual"] == 230.0
    assert result.restore_success is True
    assert commands == [test_def["command"], test_def["restore"]]
    # InfluxDB got each monitoring sample tagged with the test_id.
    assert len(influx.writes) == 3
    assert all(tid == result.test_id for _, tid in influx.writes)
    # One attestation record per monitored sample, with evidence captured.
    assert len(att.submitted) == 3
    assert len(result.evidence_hashes) == 3


async def test_acceptance_failure_marks_failed():
    test_def = {
        "name": "t", "command": {"address": 1, "value": 1, "function_code": 6},
        "monitor": ["v"], "monitor_interval_ms": 1000, "monitor_duration_seconds": 2,
        "acceptance": [{"register": "v", "metric": "min", "operator": "gte", "value": 228}],
    }
    eng = _engine(_seq_poller([_pr({"v": 200.0}), _pr({"v": 199.0})]))
    result = await eng.execute(TestRequest(device_id="dev-1", test_name="t"), _device([test_def]))
    assert result.status == "failed"
    assert result.acceptance_results[0]["passed"] is False


async def test_abort_condition_triggers_restore():
    commands = []
    test_def = {
        "name": "t", "command": {"address": 1, "value": 1, "function_code": 6},
        "monitor": ["output_voltage"], "monitor_interval_ms": 1000, "monitor_duration_seconds": 5,
        "abort_conditions": [{"register": "output_voltage", "operator": "lt", "value": 200}],
        "restore": {"address": 1, "value": 0, "function_code": 6},
    }
    # First sample already below the abort threshold.
    eng = _engine(_seq_poller([_pr({"output_voltage": 150.0})]), commands=commands)
    result = await eng.execute(TestRequest(device_id="dev-1", test_name="t"), _device([test_def]))
    assert result.abort_triggered is True
    assert result.status == "aborted"
    assert "output_voltage lt 200" in result.abort_reason
    assert test_def["restore"] in commands  # restore was issued


async def test_watchdog_aborts_after_three_failed_polls():
    commands = []
    test_def = {
        "name": "t", "command": {"address": 1, "value": 1, "function_code": 6},
        "monitor": ["v"], "monitor_interval_ms": 100, "monitor_duration_seconds": 1,  # 10 samples
        "restore": {"address": 1, "value": 0, "function_code": 6},
    }
    eng = _engine(_seq_poller([_pr({}, success=False)]), commands=commands)
    result = await eng.execute(TestRequest(device_id="dev-1", test_name="t"), _device([test_def]))
    assert result.abort_triggered is True
    assert result.abort_reason == "device_unreachable"
    assert result.status == "aborted"
    assert test_def["restore"] in commands  # blind restore still attempted


async def test_restore_verify_failure_sets_restore_failure():
    test_def = {
        "name": "t", "command": {"address": 1, "value": 1, "function_code": 6},
        "monitor": ["v"], "monitor_interval_ms": 1000, "monitor_duration_seconds": 1,
        "restore": {"address": 1, "value": 0, "function_code": 6},
        "restore_verify": [{"register": "ups_status", "operator": "eq", "value": 2}],
        "restore_timeout_seconds": 2,
    }
    # ups_status never returns to 2 → restore_failure.
    eng = _engine(_seq_poller([_pr({"v": 1.0, "ups_status": 3.0})]))
    result = await eng.execute(TestRequest(device_id="dev-1", test_name="t"), _device([test_def]))
    assert result.restore_success is False
    assert result.status == "restore_failure"


async def test_manual_confirmation_timeout_aborts():
    test_def = {"name": "t", "manual_confirmation": ["downstream_load_isolated"],
                "command": {"address": 1, "value": 1, "function_code": 6}}
    eng = _engine(_seq_poller([_pr({})]), manual_timeout=0.0)  # immediate timeout
    result = await eng.execute(TestRequest(device_id="dev-1", test_name="t"), _device([test_def]))
    assert result.status == "aborted"
    assert "Manual confirmation timed out" in result.abort_reason


async def test_manual_confirmation_proceeds_when_confirmed():
    commands = []
    test_def = {
        "name": "t", "manual_confirmation": ["downstream_load_isolated"],
        "command": {"address": 1, "value": 1, "function_code": 6},
        "monitor": ["v"], "monitor_interval_ms": 1000, "monitor_duration_seconds": 1,
    }
    req = TestRequest(device_id="dev-1", test_name="t")
    eng = _engine(_seq_poller([_pr({"v": 1.0})]), commands=commands, manual_timeout=5.0)
    task = asyncio.create_task(eng.execute(req, _device([test_def])))
    # Wait until the engine is parked on the manual confirmation.
    for _ in range(1000):
        await asyncio.sleep(0)
        if req.test_id in eng.manual_confirmations:
            break
    assert eng.confirm_manual(req.test_id, "engineer_jane") is True
    result = await task
    assert result.status in ("passed", "failed")  # proceeded past manual wait
    assert result.manual_confirmations[0]["confirmed_by"] == "engineer_jane"
    assert commands == [test_def["command"]]


def _ok():
    async def _coro():
        return None
    return _coro()
