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
