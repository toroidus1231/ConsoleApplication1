"""Module 22: Digital Twin Simulator.

Pure-Python physics models for commissioning tests. The state evolves at
100 ms intervals; reads return the current values, writes trigger state
transitions.

Two simulators are included:
  - UPSSimulator: implements the §4.1 UPS battery transfer dynamics
    (initial dip on transfer, exponential recovery, settled-on-battery
    voltage, battery drain).
  - BreakerSimulator: implements the §4.2 trip timing (cycles to open,
    contact wear accumulation, spring charge cycle).

Each model is fronted by ``MemoryRegisters`` (a dict-of-int register file)
so the same simulator can be exposed via PyModbus, BACnet, or just driven
in unit tests via direct method calls. ``serve_modbus(simulator)`` will
attach a PyModbus server when PyModbus is installed; tests don't need it.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field


@dataclass
class MemoryRegisters:
    """In-process Modbus-like register file. Addresses are 0-based."""

    holding: dict[int, int] = field(default_factory=dict)

    def read(self, address: int) -> int:
        return self.holding.get(address, 0)

    def read_block(self, address: int, count: int) -> list[int]:
        return [self.holding.get(address + i, 0) for i in range(count)]

    def write(self, address: int, value: int) -> None:
        self.holding[address] = int(value)


# ---------------------------------------------------------------------------
# UPS battery transfer (spec §4.1)
# ---------------------------------------------------------------------------


# Register layout. These match the regions used by the test_engine tests
# and the ups_battery_transfer test definition.
UPS_REG_STATUS = 0          # 1 = online, 2 = battery, 0 = fault
UPS_REG_OUTPUT_VOLTAGE = 1  # tenths of a volt (e.g. 4800 = 480.0V)
UPS_REG_BATTERY_PCT = 2     # 0..100
UPS_REG_TEST_INITIATE = 3   # write 1 to start, 0 to stop


@dataclass
class UPSSimulator:
    """Simulates a UPS undergoing a battery transfer test."""

    nominal_voltage_dV: int = 4800   # 480.0V
    on_battery_voltage_dV: int = 4750
    initial_dip_voltage_dV: int = 4600
    dip_duration_s: float = 0.005
    recovery_duration_s: float = 0.05
    drain_pct_per_cycle: float = 0.05
    battery_pct: float = 95.0

    registers: MemoryRegisters = field(default_factory=MemoryRegisters)
    _test_active: bool = False
    _test_started_at: float = 0.0
    _now: float = 0.0  # mockable clock for tests

    def __post_init__(self):
        self.registers.write(UPS_REG_STATUS, 1)
        self.registers.write(UPS_REG_OUTPUT_VOLTAGE, self.nominal_voltage_dV)
        self.registers.write(UPS_REG_BATTERY_PCT, int(self.battery_pct))
        self.registers.write(UPS_REG_TEST_INITIATE, 0)

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        """Run the physics loop forward by ``seconds`` (deterministic)."""
        self._now += seconds
        cmd = self.registers.read(UPS_REG_TEST_INITIATE)
        if cmd == 1 and not self._test_active:
            self._test_active = True
            self._test_started_at = self._now
            self.registers.write(UPS_REG_STATUS, 2)  # on battery

        if cmd == 0 and self._test_active:
            self._test_active = False
            self.registers.write(UPS_REG_STATUS, 1)
            self.registers.write(UPS_REG_OUTPUT_VOLTAGE, self.nominal_voltage_dV)
            return

        if not self._test_active:
            return

        elapsed = self._now - self._test_started_at
        if elapsed < self.dip_duration_s:
            voltage = self.initial_dip_voltage_dV
        elif elapsed < self.recovery_duration_s:
            # Exponential recovery from dip → on-battery
            t = (elapsed - self.dip_duration_s) / (self.recovery_duration_s - self.dip_duration_s)
            voltage = int(
                self.initial_dip_voltage_dV
                + (self.on_battery_voltage_dV - self.initial_dip_voltage_dV)
                * (1.0 - math.exp(-3 * t))
            )
        else:
            voltage = self.on_battery_voltage_dV

        self.registers.write(UPS_REG_OUTPUT_VOLTAGE, voltage)
        # Battery drains while on battery.
        self.battery_pct = max(0.0, self.battery_pct - self.drain_pct_per_cycle * (seconds / 0.1))
        self.registers.write(UPS_REG_BATTERY_PCT, int(self.battery_pct))


# ---------------------------------------------------------------------------
# Breaker trip (spec §4.2)
# ---------------------------------------------------------------------------


BRK_REG_POSITION = 0        # 0 open, 1 closed, 2 tripped
BRK_REG_OPS_COUNT = 1
BRK_REG_TRIP_TIME_CYCLES = 2  # cycles (60 Hz → 16.67ms)
BRK_REG_CONTACT_WEAR_PCT = 3
BRK_REG_SPRING_CHARGED = 4    # 0/1
BRK_REG_TRIP_CMD = 5          # write 1 to trip
BRK_REG_CLOSE_CMD = 6         # write 1 to close


@dataclass
class BreakerSimulator:
    """Simulates a Masterpact MTZ breaker."""

    trip_cycles_target: int = 3   # ~50 ms at 60 Hz
    spring_charge_seconds: float = 5.0
    wear_per_op_pct: float = 0.05

    registers: MemoryRegisters = field(default_factory=MemoryRegisters)
    _trip_armed_at: float | None = None
    _spring_started_charging_at: float | None = None
    _now: float = 0.0

    def __post_init__(self):
        self.registers.write(BRK_REG_POSITION, 1)  # closed
        self.registers.write(BRK_REG_OPS_COUNT, 0)
        self.registers.write(BRK_REG_TRIP_TIME_CYCLES, 0)
        self.registers.write(BRK_REG_CONTACT_WEAR_PCT, 0)
        self.registers.write(BRK_REG_SPRING_CHARGED, 1)
        self.registers.write(BRK_REG_TRIP_CMD, 0)
        self.registers.write(BRK_REG_CLOSE_CMD, 0)

    def advance(self, seconds: float) -> None:
        # Commands are seen at the start of the tick; the breaker then
        # physically evolves over `seconds`. This avoids the "armed at the
        # current instant, elapsed=0" off-by-one.
        cmd_time = self._now

        if self.registers.read(BRK_REG_TRIP_CMD) == 1 and self._trip_armed_at is None:
            self._trip_armed_at = cmd_time

        self._now += seconds

        if self._trip_armed_at is not None:
            elapsed = self._now - self._trip_armed_at
            cycles_elapsed = elapsed * 60.0
            if cycles_elapsed >= self.trip_cycles_target:
                # Open the breaker.
                self.registers.write(BRK_REG_POSITION, 0)
                self.registers.write(BRK_REG_TRIP_TIME_CYCLES, int(cycles_elapsed))
                self.registers.write(BRK_REG_OPS_COUNT, self.registers.read(BRK_REG_OPS_COUNT) + 1)
                wear = self.registers.read(BRK_REG_CONTACT_WEAR_PCT)
                self.registers.write(BRK_REG_CONTACT_WEAR_PCT, int(wear + self.wear_per_op_pct * 100))
                self.registers.write(BRK_REG_SPRING_CHARGED, 0)
                self._spring_started_charging_at = self._now
                self._trip_armed_at = None
                self.registers.write(BRK_REG_TRIP_CMD, 0)

        # Spring re-charge.
        if (
            self._spring_started_charging_at is not None
            and self.registers.read(BRK_REG_SPRING_CHARGED) == 0
            and (self._now - self._spring_started_charging_at) >= self.spring_charge_seconds
        ):
            self.registers.write(BRK_REG_SPRING_CHARGED, 1)
            self._spring_started_charging_at = None

        # Close command.
        if (
            self.registers.read(BRK_REG_CLOSE_CMD) == 1
            and self.registers.read(BRK_REG_SPRING_CHARGED) == 1
            and self.registers.read(BRK_REG_POSITION) == 0
        ):
            self.registers.write(BRK_REG_POSITION, 1)
            self.registers.write(BRK_REG_CLOSE_CMD, 0)


# ---------------------------------------------------------------------------
# Hipot tester (Vitrek 95X / V7X-style SCPI bench instrument)
# ---------------------------------------------------------------------------


@dataclass
class HipotSimulator:
    """Simulates a SCPI-controlled hipot (high-potential) tester.

    Physics model: while a test is running, the instrument ramps voltage
    from 0 to ``target_kv`` over ``ramp_seconds``, then holds for
    ``hold_seconds``. Leakage current is modeled as

        leakage_mA = base_leakage_mA + V_kV / insulation_mohm * 1000

    (i.e. Ohm's law through the device-under-test's insulation
    resistance, with a small base contribution from instrument
    self-leakage). If at any point V exceeds ``breakdown_kv`` or leakage
    crosses ``leakage_trip_mA``, the instrument trips and reports a
    failure.

    Operated via SCPI strings — same interface the serial_scpi poller
    will use. ``send_scpi(command)`` accepts a query and returns the
    response line, or empty for non-query commands.
    """

    target_kv: float = 2.5
    ramp_seconds: float = 5.0
    hold_seconds: float = 60.0
    insulation_mohm: float = 1000.0
    base_leakage_mA: float = 0.05
    breakdown_kv: float = 10.0
    leakage_trip_mA: float = 5.0

    _state: str = "idle"  # idle | ramping | holding | passed | failed
    _started_at: float = 0.0
    _now: float = 0.0
    _last_failure_reason: str = ""

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        """Evolve the test physics forward by ``seconds``."""
        self._now += seconds
        if self._state in ("idle", "passed", "failed"):
            return

        elapsed = self._now - self._started_at
        if elapsed < self.ramp_seconds:
            self._state = "ramping"
        elif elapsed < self.ramp_seconds + self.hold_seconds:
            self._state = "holding"
        else:
            self._state = "passed"
            return

        v = self.current_voltage_kv()
        if v >= self.breakdown_kv:
            self._state = "failed"
            self._last_failure_reason = f"breakdown at {v:.2f} kV"
            return
        leak = self.current_leakage_mA()
        if leak >= self.leakage_trip_mA:
            self._state = "failed"
            self._last_failure_reason = (
                f"leakage {leak:.2f} mA exceeded {self.leakage_trip_mA:.2f} mA at {v:.2f} kV"
            )

    # Physics ---------------------------------------------------------

    def current_voltage_kv(self) -> float:
        if self._state == "idle":
            return 0.0
        if self._state in ("passed", "failed"):
            return self.target_kv if self._state == "passed" else 0.0
        elapsed = self._now - self._started_at
        if elapsed < self.ramp_seconds:
            return self.target_kv * (elapsed / self.ramp_seconds)
        return self.target_kv

    def current_leakage_mA(self) -> float:
        v = self.current_voltage_kv()
        if v <= 0:
            return 0.0
        # Resistive: I = V / R. mA = (V_kV * 1000) / mohm.
        resistive = (v * 1000.0) / self.insulation_mohm
        return self.base_leakage_mA + resistive

    # SCPI shim — what the poller talks to ----------------------------

    def send_scpi(self, command: str) -> str:
        """Handle a SCPI command. Queries (ending in '?') return one
        response line as text. Non-queries return an empty string."""
        cmd = command.strip()
        if cmd.startswith(":TEST:START"):
            self._state = "ramping"
            self._started_at = self._now
            self._last_failure_reason = ""
            return ""
        if cmd.startswith(":TEST:STOP"):
            self._state = "idle"
            return ""
        if cmd == "*IDN?":
            return "Vitrek,95X,SN-12345,1.0.0"
        if cmd == ":READ:VOLT?":
            return f"{self.current_voltage_kv():.4f}"
        if cmd == ":READ:LEAK?":
            return f"{self.current_leakage_mA():.4f}"
        if cmd == ":READ:STATE?":
            return self._state.upper()
        if cmd == ":READ:RESULT?":
            if self._state == "passed":
                return "PASS"
            if self._state == "failed":
                return f"FAIL,{self._last_failure_reason}"
            return "RUNNING"
        return "ERR"


# ---------------------------------------------------------------------------
# Optional PyModbus server bridge
# ---------------------------------------------------------------------------


async def serve_modbus(simulator, host: str = "0.0.0.0", port: int = 502, tick: float = 0.1) -> None:
    """Run the simulator alongside a PyModbus TCP server.

    Imported lazily so unit tests don't need PyModbus running. This function
    is invoked by a separate ``simulator/main.py`` entrypoint, not by the
    platform itself.
    """
    from pymodbus.datastore import (  # noqa: PLC0415
        ModbusSequentialDataBlock,
        ModbusServerContext,
        ModbusSlaveContext,
    )
    from pymodbus.server import StartAsyncTcpServer  # noqa: PLC0415

    block = ModbusSequentialDataBlock(0, [0] * 256)
    slave = ModbusSlaveContext(hr=block)
    context = ModbusServerContext(slaves=slave, single=True)

    async def physics():
        while True:
            simulator.advance(tick)
            for addr, value in simulator.registers.holding.items():
                block.setValues(addr, [value])
            # Mirror writes back into the simulator so commands take effect.
            for addr in [3, 5, 6]:
                v = block.getValues(addr, 1)[0]
                simulator.registers.write(addr, v)
            await asyncio.sleep(tick)

    server_task = StartAsyncTcpServer(context=context, address=(host, port))
    await asyncio.gather(server_task, physics())
