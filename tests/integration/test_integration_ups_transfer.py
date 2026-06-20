"""Integration: the real Active Test Engine (Module 10) driving the real Digital
Twin Simulator (Module 22) through a UPS battery-transfer test.

This is the cross-module check the build rules call for: instead of fakes, the
test engine's read/command seams are wired straight to the simulator's physics
model. A battery-transfer command drives a genuine voltage transient, and the
engine's precondition/monitor/acceptance/restore state machine runs against it.
"""

import asyncio

from simulator.sim import UPSSimulator
from src.test_engine import TestEngine
from src.timeutil import ns_now
from src.types import DeviceInfo, PollResult, TestRequest


def _poll_fn(sim: UPSSimulator):
    """Read the simulator as a poller would, advancing physics 100ms per sample."""
    async def poll_fn(device):
        sim.step(0.1)
        measurements = {
            "ups_status": float(sim.read(UPSSimulator.ADDR_STATUS)[0]),
            "output_voltage": sim.read(UPSSimulator.ADDR_VOLTAGE)[0] / 10.0,  # x10 → volts
            "battery_percent": float(sim.read(UPSSimulator.ADDR_BATTERY)[0]),
        }
        return PollResult(device.device_id, ns_now(), measurements,
                          {k: "" for k in measurements}, "modbus_tcp", "10.0.0.5", True)
    return poll_fn


def _command_fn(sim: UPSSimulator):
    async def command_fn(device, command):
        sim.write(int(command["address"]), int(command["value"]))
    return command_fn


async def _noop_sleep(_):
    return None


def _device(active_test):
    return DeviceInfo(
        device_id="ups-1", name="UPS-1", primary_ip="10.0.0.5", device_type_slug="ups",
        config_context={"protocol": "modbus_tcp", "active_tests": [active_test]},
        protocol="modbus_tcp", site="DC1", rack="A1", position=1,
    )


def _engine(sim):
    return TestEngine(
        attestation=_Att(), influx=_Influx(),
        poll_fn=_poll_fn(sim), command_fn=_command_fn(sim), sleep_fn=_noop_sleep,
    )


class _Att:
    def __init__(self):
        self.submitted = []

    async def submit(self, record):
        record.nonce = f"n{len(self.submitted)}"
        self.submitted.append(record)


class _Influx:
    def __init__(self):
        self.writes = []

    async def write_poll(self, result, test_id=None):
        self.writes.append((result, test_id))


BATTERY_TRANSFER = {
    "name": "battery_transfer",
    "preconditions": [{"register": "battery_percent", "operator": "gte", "value": 80}],
    "command": {"address": UPSSimulator.ADDR_INITIATE, "value": 1, "function_code": 6},
    "monitor": ["output_voltage", "ups_status", "battery_percent"],
    "monitor_interval_ms": 100,
    "monitor_duration_seconds": 1,  # → 10 samples over the transient
    "acceptance": [{"register": "output_voltage", "metric": "min", "operator": "gte", "value": 228}],
    "restore": {"address": UPSSimulator.ADDR_INITIATE, "value": 0, "function_code": 6},
    "restore_verify": [{"register": "ups_status", "operator": "eq", "value": float(UPSSimulator.STATUS_ONLINE)}],
    "restore_timeout_seconds": 3,
}


async def test_ups_battery_transfer_passes_end_to_end():
    sim = UPSSimulator()
    engine = _engine(sim)

    result = await engine.execute(TestRequest(device_id="ups-1", test_name="battery_transfer"),
                                  _device(BATTERY_TRANSFER))

    assert result.status == "passed"
    assert result.precondition_results[0]["passed"] is True
    # The transient really happened: min voltage dipped below nominal 480V but
    # stayed above the 228V acceptance floor.
    acc = result.acceptance_results[0]
    assert 228 <= acc["actual"] < 480
    # Restore returned the UPS to utility.
    assert result.restore_success is True
    assert sim.read(UPSSimulator.ADDR_STATUS)[0] == UPSSimulator.STATUS_ONLINE
    # Telemetry was attested under the test_id for every monitored sample.
    submitted = engine._attestation.submitted
    assert submitted, "expected monitored samples to be attested"
    assert all(r.test_id == result.test_id for r in submitted)
    assert {r.measurement for r in submitted} <= {"output_voltage", "ups_status", "battery_percent"}


async def test_ups_transfer_aborts_when_voltage_breaches_threshold():
    sim = UPSSimulator()
    # At 100ms sampling the sub-5ms dip to 460V is invisible; the observed floor
    # is the settled on-battery voltage (~475V). An abort floor of 478V therefore
    # trips on the first monitoring sample.
    test_def = dict(BATTERY_TRANSFER,
                    abort_conditions=[{"register": "output_voltage", "operator": "lt", "value": 478}])
    engine = _engine(sim)

    result = await engine.execute(TestRequest(device_id="ups-1", test_name="battery_transfer"),
                                  _device(test_def))

    assert result.abort_triggered is True
    assert result.status == "aborted"
    assert "output_voltage lt 478" in result.abort_reason
    # Abort still drove the restore: the UPS is back online.
    assert sim.read(UPSSimulator.ADDR_STATUS)[0] == UPSSimulator.STATUS_ONLINE


async def test_precondition_blocks_transfer_on_low_battery():
    sim = UPSSimulator()
    sim.write(UPSSimulator.ADDR_BATTERY, 50)  # below the 80% interlock
    engine = _engine(sim)

    result = await engine.execute(TestRequest(device_id="ups-1", test_name="battery_transfer"),
                                  _device(BATTERY_TRANSFER))

    assert result.status == "precondition_failed"
    # The interlock held: no battery transfer was ever commanded.
    assert sim.read(UPSSimulator.ADDR_STATUS)[0] == UPSSimulator.STATUS_ONLINE
