"""Tests for Module 22 — Digital Twin Simulator.

Drives the deterministic ``step()`` seam directly against the pymodbus
holding-register datastore. No TCP server is started and no real time elapses:
every assertion comes from feeding ``dt`` into ``step`` and reading the store
back, mirroring the in-memory style of the Modbus poller tests.
"""

import math

from simulator.sim import PROFILES, UPSSimulator


# Register addresses, named for readability (also asserted against the map).
STATUS = UPSSimulator.ADDR_STATUS
VOLTAGE = UPSSimulator.ADDR_VOLTAGE
BATTERY = UPSSimulator.ADDR_BATTERY
INITIATE = UPSSimulator.ADDR_INITIATE


def test_initial_state_online_nominal():
    sim = UPSSimulator()
    assert sim.read(STATUS)[0] == 2            # online (utility)
    assert sim.read(VOLTAGE)[0] == 4800        # 480.0 V x10
    assert sim.read(BATTERY)[0] == 95
    assert sim.read(INITIATE)[0] == 0


def test_register_name_map():
    sim = UPSSimulator()
    assert sim.registers == {
        0: "ups_status",
        1: "output_voltage_x10",
        2: "battery_percent",
        3: "battery_test_initiate",
    }
    # Property returns a copy — mutating it must not affect the simulator.
    sim.registers[0] = "tampered"
    assert sim.registers[0] == "ups_status"


def test_read_write_round_trip_through_datastore():
    sim = UPSSimulator()
    # Single int writes one register.
    sim.write(VOLTAGE, 4810)
    assert sim.read(VOLTAGE) == [4810]
    # List write spans consecutive registers; read with count reads them back.
    sim.write(STATUS, [3, 4750, 80])
    assert sim.read(STATUS, count=3) == [3, 4750, 80]


def test_battery_transfer_sets_status_dips_voltage_drains_battery():
    sim = UPSSimulator()
    sim.write(INITIATE, 1)

    # First step crosses the edge into battery mode: status flips, the bus
    # collapses into the 460 V region, and the battery starts draining.
    sim.step(0.001)
    assert sim.read(STATUS)[0] == 3            # on battery
    assert sim.read(VOLTAGE)[0] == 4600        # dipped toward 460.0 V
    assert sim.read(BATTERY)[0] == 94          # drained one unit

    # As the transient recovers it climbs back, but stays below nominal 480 V.
    sim.step(0.01)
    voltage = sim.read(VOLTAGE)[0]
    assert 4600 <= voltage < 4800
    assert sim.read(BATTERY)[0] == 93


def test_voltage_recovers_and_settles_at_4750():
    sim = UPSSimulator()
    sim.write(INITIATE, 1)
    # Accumulate well past the 50 ms settle point.
    for _ in range(60):
        sim.step(0.001)
    assert sim.elapsed >= 0.05
    assert sim.read(VOLTAGE)[0] == 4750        # settled at 475.0 V


def test_restore_returns_status_and_nominal_voltage():
    sim = UPSSimulator()
    sim.write(INITIATE, 1)
    sim.step(0.001)
    assert sim.read(STATUS)[0] == 3

    # Clear the test: utility restored.
    sim.write(INITIATE, 0)
    sim.step(0.1)
    assert sim.read(STATUS)[0] == 2            # back online
    assert sim.read(VOLTAGE)[0] == 4800        # snapped back to 480.0 V


def test_voltage_curve_matches_piecewise_definition():
    # <5 ms: flat dip floor.
    assert UPSSimulator._voltage_for(0.0) == 4600
    assert UPSSimulator._voltage_for(0.004) == 4600
    # 5-50 ms: exponential recovery 4600 + 200*(1-exp(-100t)).
    assert UPSSimulator._voltage_for(0.01) == int(4600 + 200 * (1 - math.exp(-1.0)))
    # >=50 ms: settled.
    assert UPSSimulator._voltage_for(0.05) == 4750
    assert UPSSimulator._voltage_for(0.2) == 4750


def test_battery_drain_floors_at_zero():
    sim = UPSSimulator()
    sim.write(BATTERY, 1)
    sim.write(INITIATE, 1)
    sim.step(0.1)
    assert sim.read(BATTERY)[0] == 0           # 1 -> 0 on the entry step
    sim.step(0.1)
    assert sim.read(BATTERY)[0] == 0           # never goes negative


def test_step_is_deterministic_in_dt_not_wallclock():
    # Two simulators fed the same total dt via different step counts land on the
    # same elapsed and voltage — physics depends only on accumulated dt.
    a = UPSSimulator()
    b = UPSSimulator()
    a.write(INITIATE, 1)
    b.write(INITIATE, 1)
    a.step(0.001)
    for _ in range(10):
        a.step(0.001)

    b.step(0.001)
    b.step(0.010)

    assert math.isclose(a.elapsed, b.elapsed, rel_tol=1e-9)
    assert a.read(VOLTAGE)[0] == b.read(VOLTAGE)[0]


def test_no_drain_while_idle():
    sim = UPSSimulator()
    # Stepping without initiating a transfer must not change anything.
    for _ in range(5):
        sim.step(0.1)
    assert sim.read(STATUS)[0] == 2
    assert sim.read(VOLTAGE)[0] == 4800
    assert sim.read(BATTERY)[0] == 95


def test_profiles_registry_exposes_ups_transfer():
    assert "ups_transfer" in PROFILES
    sim = PROFILES["ups_transfer"]()
    assert isinstance(sim, UPSSimulator)
    assert sim.read(STATUS)[0] == 2
