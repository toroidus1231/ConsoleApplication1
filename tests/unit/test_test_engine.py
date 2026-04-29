"""Tests for Module 10 — Active Test Engine.

Drives the state machine end to end with fakes for the poller (reads),
writer (commands/restore), influx, attestation, and event queue. Each test
exercises one or two phases of the state machine plus the relevant edge
cases from spec §5.1 / §5.2.
"""

import asyncio
import time
from dataclasses import dataclass

import pytest

from src.test_engine import TestEngine, _compute_metric, _evaluate
from src.types import (
    AttestationRecord,
    DeviceInfo,
    Event,
    PollResult,
    TestRequest,
)


@dataclass
class FakeConfig:
    pass


class FakeInflux:
    def __init__(self):
        self.writes: list[tuple] = []

    async def write_poll(self, result, test_id=None):
        self.writes.append((result, test_id))


class FakeAttestation:
    def __init__(self):
        self.submitted: list[AttestationRecord] = []

    async def submit(self, record):
        self.submitted.append(record)


def _make_engine(*, manual_timeout=1.0):
    return TestEngine(
        FakeConfig(),
        FakeInflux(),
        FakeAttestation(),
        asyncio.Queue(),
        poller=None,
        writer=None,
        manual_confirm_timeout=manual_timeout,
    ),


def _engine(poller=None, writer=None, manual_timeout=1.0):
    influx = FakeInflux()
    attest = FakeAttestation()
    events: asyncio.Queue[Event] = asyncio.Queue()
    eng = TestEngine(
        FakeConfig(),
        influx,
        attest,
        events,
        poller=poller,
        writer=writer,
        manual_confirm_timeout=manual_timeout,
    )
    return eng, influx, attest, events


def _device(active_tests, identity_register=None):
    ctx = {
        "protocol": "modbus_tcp",
        "connection": {"port": 502, "unit_id": 1},
        "active_tests": active_tests,
    }
    if identity_register:
        ctx["identity_register"] = identity_register
    return DeviceInfo(
        device_id="dev-1",
        name="ups-a",
        primary_ip="10.0.0.5",
        device_type_slug="ups",
        config_context=ctx,
        protocol="modbus_tcp",
        site="DC1",
        rack="A1",
        position=1,
    )


def _ok_poll(measurements, ts=None, raw=None) -> PollResult:
    return PollResult(
        device_id="dev-1",
        timestamp_ns=ts if ts is not None else time.time_ns(),
        measurements=dict(measurements),
        raw_bytes=raw or {k: f"{int(v) if isinstance(v, (int, float)) else 0:04x}" for k, v in measurements.items()},
        protocol="modbus_tcp",
        source_ip="10.0.0.5",
        success=True,
    )


def _fail_poll() -> PollResult:
    return PollResult(
        device_id="dev-1",
        timestamp_ns=time.time_ns(),
        measurements={},
        raw_bytes={},
        protocol="modbus_tcp",
        source_ip="10.0.0.5",
        success=False,
        errors={"_connection": "timeout"},
    )


async def _ok_writer(*args, **kwargs):
    return True, None


async def _fail_writer(*args, **kwargs):
    return False, "write timeout"


def _ups_battery_test_def() -> dict:
    """A representative test definition mirroring spec §4.1."""
    return {
        "name": "ups_battery_transfer",
        "preconditions": [
            {"register": "battery_pct", "operator": "gte", "value": 80},
            {"register": "ups_status", "operator": "eq", "value": 1},
        ],
        "command": {"address": 1234, "value": 1, "function_code": 6},
        "abort_conditions": [
            {"register": "output_voltage", "operator": "lt", "value": 200},
        ],
        "monitor": ["output_voltage", "battery_voltage"],
        "monitor_interval_ms": 0,
        "monitor_duration_seconds": 0.05,  # tight for tests
        "acceptance": [
            {"register": "output_voltage", "metric": "min", "operator": "gte", "value": 228},
        ],
        "restore": {"address": 1234, "value": 0},
        "restore_verify": [
            {"register": "ups_status", "operator": "eq", "value": 1},
        ],
        "restore_timeout_seconds": 1,
    }


# -----------------------------------------------------------------------------
# Test discovery / lookup
# -----------------------------------------------------------------------------


async def test_unknown_test_name_returns_failed():
    device = _device([_ups_battery_test_def()])
    eng, *_ = _engine()

    result = await eng.execute(
        TestRequest(test_id="t1", test_name="does_not_exist"), device
    )

    assert result.status == "failed"
    assert "not found" in result.abort_reason


# -----------------------------------------------------------------------------
# Preconditions
# -----------------------------------------------------------------------------


def _scripted_poller(scripted: list[PollResult], default: PollResult):
    """Returns an async poller that yields scripted results then `default`
    forever. Avoids StopIteration in test loops."""
    it = iter(scripted)

    async def poller(_d):
        try:
            return next(it)
        except StopIteration:
            return default
    return poller


async def test_preconditions_all_pass_then_proceeds_to_test():
    test_def = _ups_battery_test_def()
    device = _device([test_def])

    poll = _scripted_poller(
        [_ok_poll({"battery_pct": 90, "ups_status": 1})],
        _ok_poll({"output_voltage": 240, "battery_voltage": 12.0, "ups_status": 1}),
    )

    eng, _, _, events = _engine(poller=poll, writer=_ok_writer)
    result = await eng.execute(TestRequest(test_id="t1", test_name="ups_battery_transfer"), device)

    assert all(p["passed"] for p in result.precondition_results)
    assert result.status == "passed"


async def test_precondition_failure_short_circuits():
    test_def = _ups_battery_test_def()
    device = _device([test_def])

    poll = _ok_poll({"battery_pct": 50, "ups_status": 1})  # battery too low

    async def poller(d):
        return poll

    eng, _, _, events = _engine(poller=poller, writer=_ok_writer)
    result = await eng.execute(TestRequest(test_id="t1", test_name="ups_battery_transfer"), device)

    assert result.status == "precondition_failed"
    assert len(result.precondition_results) == 1  # short-circuit on first failure
    assert result.precondition_results[0] == {
        "register": "battery_pct", "operator": "gte", "expected": 80,
        "actual": 50, "passed": False,
    }


async def test_precondition_with_missing_register_fails():
    test_def = _ups_battery_test_def()
    device = _device([test_def])

    async def poller(d):
        return _ok_poll({"ups_status": 1})  # battery_pct missing

    eng, *_ = _engine(poller=poller, writer=_ok_writer)
    result = await eng.execute(TestRequest(test_id="t1", test_name="ups_battery_transfer"), device)

    assert result.status == "precondition_failed"


# -----------------------------------------------------------------------------
# Manual confirmation (spec §4.2 downstream_load_isolated)
# -----------------------------------------------------------------------------


async def test_manual_confirmation_waits_until_confirmed():
    test_def = _ups_battery_test_def()
    test_def["manual_confirmation"] = ["downstream_load_isolated"]
    device = _device([test_def])

    poller = _scripted_poller(
        [_ok_poll({"battery_pct": 90, "ups_status": 1})],
        _ok_poll({"output_voltage": 240, "battery_voltage": 12.0, "ups_status": 1}),
    )

    eng, _, _, events = _engine(poller=poller, writer=_ok_writer, manual_timeout=2.0)

    async def confirm_after_delay():
        await asyncio.sleep(0.05)
        eng.confirm_manual("t1", confirmed_by="engineer_1")

    asyncio.create_task(confirm_after_delay())
    result = await eng.execute(
        TestRequest(test_id="t1", test_name="ups_battery_transfer"), device
    )

    assert result.status == "passed"
    assert result.manual_confirmations[0]["prompt"] == "downstream_load_isolated"
    assert result.manual_confirmations[0]["confirmed_by"] == "engineer_1"


async def test_manual_confirmation_timeout_aborts_test():
    test_def = _ups_battery_test_def()
    test_def["manual_confirmation"] = ["downstream_load_isolated"]
    device = _device([test_def])

    async def poller(d):
        return _ok_poll({"battery_pct": 90, "ups_status": 1})

    eng, *_ = _engine(poller=poller, writer=_ok_writer, manual_timeout=0.05)

    result = await eng.execute(
        TestRequest(test_id="t1", test_name="ups_battery_transfer"), device
    )

    assert result.status == "aborted"
    assert result.abort_reason == "manual_confirmation_timeout"


# -----------------------------------------------------------------------------
# Identity verify (spec §5.1 gateway unit_id misroute)
# -----------------------------------------------------------------------------


async def test_identity_mismatch_aborts_before_command():
    test_def = _ups_battery_test_def()
    device = _device(
        [test_def],
        identity_register={"register": "device_model_id", "expected": 4242},
    )

    write_calls = []

    async def writer(*args, **kwargs):
        write_calls.append((args, kwargs))
        return True, None

    async def poller(d):
        # Identity register present in precondition poll but with the WRONG value
        return _ok_poll({"battery_pct": 90, "ups_status": 1, "device_model_id": 9999})

    eng, *_ = _engine(poller=poller, writer=writer)
    result = await eng.execute(
        TestRequest(test_id="t1", test_name="ups_battery_transfer"), device
    )

    assert result.status == "aborted"
    assert "identity_mismatch" in result.abort_reason
    assert write_calls == []  # No command was issued


async def test_identity_match_proceeds():
    test_def = _ups_battery_test_def()
    device = _device(
        [test_def],
        identity_register={"register": "device_model_id", "expected": 4242},
    )

    poller = _scripted_poller(
        [_ok_poll({"battery_pct": 90, "ups_status": 1, "device_model_id": 4242})],
        _ok_poll({"output_voltage": 240, "battery_voltage": 12.0, "ups_status": 1, "device_model_id": 4242}),
    )

    eng, *_ = _engine(poller=poller, writer=_ok_writer)
    result = await eng.execute(
        TestRequest(test_id="t1", test_name="ups_battery_transfer"), device
    )

    assert result.status == "passed"


# -----------------------------------------------------------------------------
# Command write
# -----------------------------------------------------------------------------


async def test_command_write_failure_aborts():
    test_def = _ups_battery_test_def()
    device = _device([test_def])

    async def poller(d):
        return _ok_poll({"battery_pct": 90, "ups_status": 1})

    eng, *_ = _engine(poller=poller, writer=_fail_writer)
    result = await eng.execute(
        TestRequest(test_id="t1", test_name="ups_battery_transfer"), device
    )

    assert result.status == "aborted"
    assert "command_write_failed" in result.abort_reason


# -----------------------------------------------------------------------------
# Monitoring + abort conditions (spec §5.2 coolant spill / GPU thermal)
# -----------------------------------------------------------------------------


async def test_abort_condition_triggers_immediate_restore():
    test_def = _ups_battery_test_def()
    test_def["monitor_duration_seconds"] = 0.5
    device = _device([test_def])

    polls = [
        _ok_poll({"battery_pct": 90, "ups_status": 1}),  # preconditions
        _ok_poll({"output_voltage": 240, "battery_voltage": 12, "ups_status": 1}),
        _ok_poll({"output_voltage": 195, "battery_voltage": 11, "ups_status": 1}),  # below abort threshold
    ] + [_ok_poll({"ups_status": 1, "output_voltage": 240, "battery_voltage": 12.0})] * 10
    it = iter(polls)

    async def poller(d):
        return next(it)

    write_log = []

    async def writer(*args, **kwargs):
        write_log.append(kwargs)
        return True, None

    eng, *_ = _engine(poller=poller, writer=writer)
    result = await eng.execute(
        TestRequest(test_id="t1", test_name="ups_battery_transfer"), device
    )

    assert result.abort_triggered is True
    assert "output_voltage lt 200" in result.abort_reason
    # Two writes: command, restore.
    assert [w["address"] for w in write_log] == [1234, 1234]
    assert result.status == "aborted"


async def test_three_consecutive_read_failures_during_monitoring_abort_unreachable():
    test_def = _ups_battery_test_def()
    test_def["monitor_duration_seconds"] = 1.0
    device = _device([test_def])

    polls = [
        _ok_poll({"battery_pct": 90, "ups_status": 1}),  # preconditions
        _fail_poll(), _fail_poll(), _fail_poll(),
    ] + [_fail_poll()] * 20
    it = iter(polls)

    async def poller(d):
        return next(it)

    eng, *_ = _engine(poller=poller, writer=_ok_writer)
    result = await eng.execute(
        TestRequest(test_id="t1", test_name="ups_battery_transfer"), device
    )

    assert result.abort_triggered is True
    assert result.abort_reason == "device_unreachable"
    assert result.status == "aborted"


async def test_transient_read_failure_does_not_abort():
    test_def = _ups_battery_test_def()
    test_def["monitor_duration_seconds"] = 0.3
    device = _device([test_def])

    polls = [
        _ok_poll({"battery_pct": 90, "ups_status": 1}),  # preconditions
        _fail_poll(),
        _fail_poll(),
        _ok_poll({"output_voltage": 240, "battery_voltage": 12, "ups_status": 1}),  # recovery resets streak
        _fail_poll(),
        _ok_poll({"output_voltage": 240, "battery_voltage": 12, "ups_status": 1}),
    ] + [_ok_poll({"output_voltage": 240, "battery_voltage": 12, "ups_status": 1})] * 30
    it = iter(polls)

    async def poller(d):
        try:
            return next(it)
        except StopIteration:
            return _ok_poll({"output_voltage": 240, "battery_voltage": 12, "ups_status": 1})

    eng, *_ = _engine(poller=poller, writer=_ok_writer)
    result = await eng.execute(
        TestRequest(test_id="t1", test_name="ups_battery_transfer"), device
    )

    assert result.abort_reason != "device_unreachable"
    assert result.abort_triggered is False


async def test_each_monitored_measurement_attested_with_test_id():
    test_def = _ups_battery_test_def()
    test_def["monitor_duration_seconds"] = 0.05
    device = _device([test_def])

    polls = [
        _ok_poll({"battery_pct": 90, "ups_status": 1}),  # preconditions
    ] + [
        _ok_poll({"output_voltage": 240, "battery_voltage": 12, "ups_status": 1})
        for _ in range(20)
    ]
    it = iter(polls)

    async def poller(d):
        try:
            return next(it)
        except StopIteration:
            return polls[-1]

    eng, _, attest, _ = _engine(poller=poller, writer=_ok_writer)
    await eng.execute(TestRequest(test_id="t1", test_name="ups_battery_transfer"), device)

    # All attestations come from monitoring (preconditions are not attested).
    assert all(r.test_id == "t1" for r in attest.submitted)
    assert all(r.measurement in {"output_voltage", "battery_voltage"} for r in attest.submitted)


# -----------------------------------------------------------------------------
# Restore / restore_verify (spec §5.2 restore failure)
# -----------------------------------------------------------------------------


async def test_restore_verify_timeout_marks_restore_failure():
    test_def = _ups_battery_test_def()
    test_def["restore_timeout_seconds"] = 0.2
    test_def["restore_verify"] = [{"register": "ups_status", "operator": "eq", "value": 999}]
    device = _device([test_def])

    polls = [
        _ok_poll({"battery_pct": 90, "ups_status": 1}),  # preconditions
    ] + [
        _ok_poll({"output_voltage": 240, "battery_voltage": 12, "ups_status": 1})
        for _ in range(50)
    ]
    it = iter(polls)

    async def poller(d):
        try:
            return next(it)
        except StopIteration:
            return polls[-1]

    eng, *_ = _engine(poller=poller, writer=_ok_writer)
    result = await eng.execute(
        TestRequest(test_id="t1", test_name="ups_battery_transfer"), device
    )

    assert result.restore_success is False
    assert result.status == "restore_failure"


async def test_no_restore_verify_marks_success_immediately():
    test_def = _ups_battery_test_def()
    test_def["restore_verify"] = []
    device = _device([test_def])

    polls = [_ok_poll({"battery_pct": 90, "ups_status": 1})] + [
        _ok_poll({"output_voltage": 240, "battery_voltage": 12, "ups_status": 1})
        for _ in range(20)
    ]
    it = iter(polls)

    async def poller(d):
        try:
            return next(it)
        except StopIteration:
            return polls[-1]

    eng, *_ = _engine(poller=poller, writer=_ok_writer)
    result = await eng.execute(
        TestRequest(test_id="t1", test_name="ups_battery_transfer"), device
    )

    assert result.restore_success is True


# -----------------------------------------------------------------------------
# Acceptance evaluation
# -----------------------------------------------------------------------------


async def test_acceptance_pass_when_min_voltage_above_threshold():
    test_def = _ups_battery_test_def()
    device = _device([test_def])

    polls = [_ok_poll({"battery_pct": 90, "ups_status": 1})] + [
        _ok_poll({"output_voltage": 240, "battery_voltage": 12, "ups_status": 1})
        for _ in range(20)
    ]
    it = iter(polls)

    async def poller(d):
        try:
            return next(it)
        except StopIteration:
            return polls[-1]

    eng, *_ = _engine(poller=poller, writer=_ok_writer)
    result = await eng.execute(
        TestRequest(test_id="t1", test_name="ups_battery_transfer"), device
    )

    assert result.status == "passed"
    assert result.acceptance_results[0]["passed"] is True
    assert result.acceptance_results[0]["actual"] >= 228


async def test_acceptance_fails_when_voltage_dipped_during_monitoring():
    test_def = _ups_battery_test_def()
    # Loosen abort threshold so the test reaches evaluation.
    test_def["abort_conditions"] = [{"register": "output_voltage", "operator": "lt", "value": 100}]
    device = _device([test_def])

    polls = [_ok_poll({"battery_pct": 90, "ups_status": 1})] + [
        _ok_poll({"output_voltage": 220, "battery_voltage": 12, "ups_status": 1})  # below 228
        for _ in range(20)
    ]
    it = iter(polls)

    async def poller(d):
        try:
            return next(it)
        except StopIteration:
            return polls[-1]

    eng, *_ = _engine(poller=poller, writer=_ok_writer)
    result = await eng.execute(
        TestRequest(test_id="t1", test_name="ups_battery_transfer"), device
    )

    assert result.status == "failed"
    assert result.acceptance_results[0]["passed"] is False


# -----------------------------------------------------------------------------
# _evaluate operator coverage
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("op,actual,expected,result", [
    ("eq", 5, 5, True),
    ("eq", 5, 4, False),
    ("neq", 5, 4, True),
    ("gt", 5, 4, True),
    ("gt", 4, 5, False),
    ("lt", 4, 5, True),
    ("gte", 5, 5, True),
    ("lte", 5, 5, True),
    ("eq", None, 5, False),  # missing reading
    ("unknown_op", 5, 5, False),
])
def test_evaluate_operator_truth_table(op, actual, expected, result):
    assert _evaluate(actual, op, expected) == result


# -----------------------------------------------------------------------------
# _compute_metric coverage
# -----------------------------------------------------------------------------


def _polls_with_voltage(values, dt_ns=1_000_000_000):
    base = 1_712_000_000_000_000_000
    return [
        PollResult(
            device_id="d", timestamp_ns=base + i * dt_ns,
            measurements={"v": v}, raw_bytes={}, protocol="modbus_tcp",
            source_ip="1.2.3.4", success=True,
        )
        for i, v in enumerate(values)
    ]


def test_metric_max_min_avg():
    polls = _polls_with_voltage([200, 240, 220, 250, 230])
    vals = [p.measurements["v"] for p in polls]
    assert _compute_metric("max", vals, polls, "v", None) == 250
    assert _compute_metric("min", vals, polls, "v", None) == 200
    assert _compute_metric("avg", vals, polls, "v", None) == pytest.approx(228)


def test_metric_delta_and_settled_value():
    polls = _polls_with_voltage([200, 240, 250, 250, 250])
    vals = [p.measurements["v"] for p in polls]
    assert _compute_metric("delta", vals, polls, "v", None) == 50
    settled = _compute_metric("settled_value", vals, polls, "v", None)
    assert settled == 250


def test_metric_time_to_value_in_ms():
    polls = _polls_with_voltage([100, 150, 220, 240, 250], dt_ns=10_000_000)  # 10 ms apart
    vals = [p.measurements["v"] for p in polls]
    # Threshold 220 first crossed at index 2 → 20ms after start.
    ms = _compute_metric("time_to_value", vals, polls, "v", 220)
    assert ms == pytest.approx(20.0)


def test_metric_time_to_value_never_crossed_returns_inf():
    polls = _polls_with_voltage([100, 150, 200])
    vals = [p.measurements["v"] for p in polls]
    assert _compute_metric("time_to_value", vals, polls, "v", 999) == float("inf")


def test_metric_rate_of_change_units_per_second():
    # 100 -> 200 over 5 seconds total = 20 / sec.
    polls = _polls_with_voltage([100, 200], dt_ns=5_000_000_000)
    vals = [p.measurements["v"] for p in polls]
    assert _compute_metric("rate_of_change", vals, polls, "v", None) == pytest.approx(20.0)


def test_metric_rate_of_change_zero_dt_returns_zero():
    base = 1_712_000_000_000_000_000
    polls = [
        PollResult(device_id="d", timestamp_ns=base, measurements={"v": 1.0},
                   raw_bytes={}, protocol="m", source_ip="x", success=True),
        PollResult(device_id="d", timestamp_ns=base, measurements={"v": 5.0},
                   raw_bytes={}, protocol="m", source_ip="x", success=True),
    ]
    vals = [1.0, 5.0]
    assert _compute_metric("rate_of_change", vals, polls, "v", None) == 0.0


# -----------------------------------------------------------------------------
# Lifecycle events
# -----------------------------------------------------------------------------


async def test_test_started_and_test_completed_events_emitted():
    test_def = _ups_battery_test_def()
    device = _device([test_def])

    polls = [_ok_poll({"battery_pct": 90, "ups_status": 1})] + [
        _ok_poll({"output_voltage": 240, "battery_voltage": 12, "ups_status": 1}) for _ in range(20)
    ]
    it = iter(polls)

    async def poller(d):
        try:
            return next(it)
        except StopIteration:
            return polls[-1]

    eng, _, _, events = _engine(poller=poller, writer=_ok_writer)
    await eng.execute(TestRequest(test_id="t1", test_name="ups_battery_transfer"), device)

    drained = []
    while not events.empty():
        drained.append(events.get_nowait())
    types = [e.event_type for e in drained]
    assert "test_started" in types
    assert "test_completed" in types
