"""Tests for Module 22 — Digital Twin Simulator.

Drive the simulators in-process via .advance(seconds). No PyModbus needed.
"""

import pytest

from simulator.sim import (
    BRK_REG_CLOSE_CMD,
    BRK_REG_CONTACT_WEAR_PCT,
    BRK_REG_OPS_COUNT,
    BRK_REG_POSITION,
    BRK_REG_SPRING_CHARGED,
    BRK_REG_TRIP_CMD,
    BRK_REG_TRIP_TIME_CYCLES,
    BreakerSimulator,
    HipotSimulator,
    UPS_REG_BATTERY_PCT,
    UPS_REG_OUTPUT_VOLTAGE,
    UPS_REG_STATUS,
    UPS_REG_TEST_INITIATE,
    UPSSimulator,
)


# --- UPS ---------------------------------------------------------------------


def test_ups_starts_in_online_state():
    ups = UPSSimulator()
    assert ups.registers.read(UPS_REG_STATUS) == 1
    assert ups.registers.read(UPS_REG_OUTPUT_VOLTAGE) == 4800
    assert ups.registers.read(UPS_REG_BATTERY_PCT) == 95


def test_ups_battery_test_dips_voltage_then_recovers():
    ups = UPSSimulator()
    ups.registers.write(UPS_REG_TEST_INITIATE, 1)
    ups.advance(0.001)  # within initial dip window
    assert ups.registers.read(UPS_REG_OUTPUT_VOLTAGE) == 4600

    ups.advance(0.06)   # past recovery, settled on battery
    assert ups.registers.read(UPS_REG_OUTPUT_VOLTAGE) == 4750
    assert ups.registers.read(UPS_REG_STATUS) == 2


def test_ups_battery_drains_during_test():
    ups = UPSSimulator(drain_pct_per_cycle=1.0)  # accelerated for the test
    ups.registers.write(UPS_REG_TEST_INITIATE, 1)
    for _ in range(10):
        ups.advance(0.1)  # 10 cycles
    after = ups.registers.read(UPS_REG_BATTERY_PCT)
    assert after < 95


def test_ups_returns_to_online_when_test_cleared():
    ups = UPSSimulator()
    ups.registers.write(UPS_REG_TEST_INITIATE, 1)
    ups.advance(0.06)
    assert ups.registers.read(UPS_REG_STATUS) == 2
    ups.registers.write(UPS_REG_TEST_INITIATE, 0)
    ups.advance(0.001)
    assert ups.registers.read(UPS_REG_STATUS) == 1
    assert ups.registers.read(UPS_REG_OUTPUT_VOLTAGE) == 4800


# --- Breaker -----------------------------------------------------------------


def test_breaker_starts_closed_with_spring_charged():
    brk = BreakerSimulator()
    assert brk.registers.read(BRK_REG_POSITION) == 1
    assert brk.registers.read(BRK_REG_SPRING_CHARGED) == 1
    assert brk.registers.read(BRK_REG_OPS_COUNT) == 0
    assert brk.registers.read(BRK_REG_CONTACT_WEAR_PCT) == 0


def test_breaker_trip_opens_within_target_cycles():
    brk = BreakerSimulator(trip_cycles_target=3)  # 3 cycles ≈ 50 ms at 60 Hz
    brk.registers.write(BRK_REG_TRIP_CMD, 1)
    brk.advance(0.06)  # past 3 cycles

    assert brk.registers.read(BRK_REG_POSITION) == 0
    assert brk.registers.read(BRK_REG_OPS_COUNT) == 1
    assert brk.registers.read(BRK_REG_TRIP_TIME_CYCLES) >= 3


def test_breaker_trip_increments_contact_wear():
    brk = BreakerSimulator(wear_per_op_pct=0.5)  # 50 percent units per op
    brk.registers.write(BRK_REG_TRIP_CMD, 1)
    brk.advance(0.06)
    assert brk.registers.read(BRK_REG_CONTACT_WEAR_PCT) == 50


def test_breaker_spring_uncharged_after_trip_then_re_charges():
    brk = BreakerSimulator(spring_charge_seconds=0.2)
    brk.registers.write(BRK_REG_TRIP_CMD, 1)
    brk.advance(0.06)
    assert brk.registers.read(BRK_REG_SPRING_CHARGED) == 0

    brk.advance(0.25)  # > spring charge window
    assert brk.registers.read(BRK_REG_SPRING_CHARGED) == 1


def test_breaker_close_only_works_when_spring_charged_and_open():
    brk = BreakerSimulator(spring_charge_seconds=0.2)
    # Trip first.
    brk.registers.write(BRK_REG_TRIP_CMD, 1)
    brk.advance(0.06)
    assert brk.registers.read(BRK_REG_POSITION) == 0
    assert brk.registers.read(BRK_REG_SPRING_CHARGED) == 0

    # Try to close without spring → no-op.
    brk.registers.write(BRK_REG_CLOSE_CMD, 1)
    brk.advance(0.001)
    assert brk.registers.read(BRK_REG_POSITION) == 0

    # Wait for spring, then close succeeds.
    brk.advance(0.25)
    brk.registers.write(BRK_REG_CLOSE_CMD, 1)
    brk.advance(0.001)
    assert brk.registers.read(BRK_REG_POSITION) == 1


# --- HipotSimulator ----------------------------------------------------------


def test_hipot_idle_idn_query_returns_identification():
    h = HipotSimulator()
    assert h.send_scpi("*IDN?").startswith("Vitrek,95X,SN-")
    assert h.send_scpi(":READ:STATE?") == "IDLE"
    assert h.send_scpi(":READ:VOLT?") == "0.0000"


def test_hipot_voltage_ramps_linearly_to_target():
    h = HipotSimulator(target_kv=2.5, ramp_seconds=5.0, hold_seconds=60.0)
    h.send_scpi(":TEST:START")
    h.advance(2.5)  # halfway through ramp
    v = float(h.send_scpi(":READ:VOLT?"))
    assert v == pytest.approx(1.25, abs=0.01)
    h.advance(2.6)  # past end of ramp
    v = float(h.send_scpi(":READ:VOLT?"))
    assert v == pytest.approx(2.5, abs=0.01)


def test_hipot_passes_when_insulation_is_good():
    h = HipotSimulator(target_kv=2.5, ramp_seconds=1.0, hold_seconds=2.0,
                       insulation_mohm=1000.0, leakage_trip_mA=5.0)
    h.send_scpi(":TEST:START")
    h.advance(0.5)  # ramping
    h.advance(0.7)  # holding
    h.advance(2.5)  # past hold window
    assert h.send_scpi(":READ:STATE?") == "PASSED"
    assert h.send_scpi(":READ:RESULT?") == "PASS"


def test_hipot_fails_on_low_insulation():
    # 0.5 MΩ insulation → at 2.5 kV, leakage = 2500 V / 500_000 Ω = 5 mA exactly.
    # Drop to 0.4 MΩ → 6.25 mA, which trips at 5 mA threshold.
    h = HipotSimulator(target_kv=2.5, ramp_seconds=1.0, hold_seconds=10.0,
                       insulation_mohm=0.4, leakage_trip_mA=5.0)
    h.send_scpi(":TEST:START")
    h.advance(1.5)  # well into hold
    state = h.send_scpi(":READ:STATE?")
    result = h.send_scpi(":READ:RESULT?")
    assert state == "FAILED"
    assert result.startswith("FAIL")
    assert "leakage" in result


def test_hipot_fails_on_breakdown_voltage_exceeded():
    # If the user sets target_kv above the breakdown threshold, ramping
    # will eventually trip. Breakdown 2.0 kV, target 5.0 kV.
    h = HipotSimulator(target_kv=5.0, ramp_seconds=2.0, hold_seconds=1.0,
                       breakdown_kv=2.0, leakage_trip_mA=999.0)
    h.send_scpi(":TEST:START")
    h.advance(1.2)
    result = h.send_scpi(":READ:RESULT?")
    assert result.startswith("FAIL")
    assert "breakdown" in result


def test_hipot_stop_returns_to_idle():
    h = HipotSimulator(ramp_seconds=1.0, hold_seconds=10.0)
    h.send_scpi(":TEST:START")
    h.advance(0.2)
    h.send_scpi(":TEST:STOP")
    assert h.send_scpi(":READ:STATE?") == "IDLE"


def test_hipot_unknown_scpi_returns_err():
    h = HipotSimulator()
    assert h.send_scpi(":NONSENSE?") == "ERR"
