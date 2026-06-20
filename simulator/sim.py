"""Module 22: Digital Twin Simulator.

A PyModbus 3.7 TCP server exposing holding registers whose values evolve via a
deterministic physics ``step`` function, used to exercise pollers and tests
without real hardware.

The reference profile models a UPS battery-transfer event. When the load is
transferred from utility to battery (``battery_test_initiate`` -> 1) the unit
reports ``ups_status`` = 3 (on battery), the output voltage briefly dips toward
460.0 V, then recovers exponentially back toward ~475.0 V and settles, while the
battery percentage drains. Clearing the test (-> 0) restores utility power:
status returns to 2 (online) and the output snaps back to 480.0 V.

The physics is intentionally decoupled from the TCP server and from any event
loop clock: ``step(dt)`` advances state purely from the ``dt`` handed in, so the
battery-transfer transient can be unit-tested by driving ``step`` directly.
"""

from __future__ import annotations

import asyncio
import math
from typing import Callable

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)

# Holding registers are read/written with Modbus function code 3.
_HOLDING_FC = 3


class Simulator:
    """Base digital-twin simulator backed by a pymodbus holding-register store.

    Subclasses define ``REGISTERS`` (address -> name) and ``INITIAL`` (the seed
    register values, ordered by address starting at 0) and implement ``step``.
    The class owns a :class:`ModbusSlaveContext` so the same datastore can be
    driven directly by tests (via ``step``) or served over TCP (via
    ``run_simulator``).
    """

    #: Map of register address -> human-readable name. Overridden per profile.
    REGISTERS: dict[int, str] = {}
    #: Seed holding-register values, ordered by address starting at 0.
    INITIAL: list[int] = []

    def __init__(self) -> None:
        self.elapsed: float = 0.0
        block = ModbusSequentialDataBlock(0, list(self.INITIAL))
        # zero_mode=True so address N maps to register N (0-based, no +1 offset).
        self.context = ModbusSlaveContext(hr=block, zero_mode=True)

    # -- datastore helpers (fc=3 holding registers) -----------------------

    def read(self, address: int, count: int = 1) -> list[int]:
        """Read ``count`` holding registers starting at ``address``."""
        return self.context.getValues(_HOLDING_FC, address, count=count)

    def write(self, address: int, values: int | list[int]) -> None:
        """Write one int or a list of ints into holding registers at ``address``."""
        if isinstance(values, int):
            values = [values]
        self.context.setValues(_HOLDING_FC, address, list(values))

    @property
    def registers(self) -> dict[int, str]:
        """Address -> name map for the registers this profile exposes."""
        return dict(self.REGISTERS)

    # -- physics ----------------------------------------------------------

    def step(self, dt: float) -> None:  # pragma: no cover - overridden
        """Advance physics by ``dt`` seconds based on current register state."""
        raise NotImplementedError


class UPSSimulator(Simulator):
    """UPS profile modelling a utility -> battery transfer transient.

    Register map::

        0  ups_status            2 = online (utility), 3 = on battery
        1  output_voltage_x10    voltage * 10 (4800 = 480.0 V)
        2  battery_percent       remaining battery charge (0-100)
        3  battery_test_initiate write 1 to start transfer, 0 to restore

    ``step`` is deterministic with respect to the ``dt`` passed in: while a
    transfer is active it accumulates ``dt`` into :attr:`elapsed` and derives the
    voltage purely from that accumulator, so no real sleeping or loop clock is
    involved.
    """

    REGISTERS = {
        0: "ups_status",
        1: "output_voltage_x10",
        2: "battery_percent",
        3: "battery_test_initiate",
    }
    # status=online, 480.0V, 95% battery, test not initiated.
    INITIAL = [2, 4800, 95, 0]

    # Named addresses for readability.
    ADDR_STATUS = 0
    ADDR_VOLTAGE = 1
    ADDR_BATTERY = 2
    ADDR_INITIATE = 3

    STATUS_ONLINE = 2
    STATUS_BATTERY = 3
    NOMINAL_VOLTAGE = 4800  # 480.0 V x10

    def __init__(self) -> None:
        super().__init__()
        self._active = False

    @staticmethod
    def _voltage_for(elapsed: float) -> int:
        """Output voltage (x10) at ``elapsed`` seconds into a transfer.

        First 5 ms the bus collapses toward 460.0 V; through 50 ms it recovers
        exponentially toward ~475.0 V; thereafter it has settled at 475.0 V.
        """
        if elapsed < 0.005:
            return 4600
        if elapsed < 0.05:
            return int(4600 + 200 * (1 - math.exp(-elapsed * 100)))
        return 4750

    def step(self, dt: float) -> None:
        """Advance the transfer transient by ``dt`` seconds (deterministic)."""
        initiate = self.read(self.ADDR_INITIATE)[0]

        if initiate == 1 and not self._active:
            # Edge: load transfers from utility to battery. Start the transient.
            self._active = True
            self.elapsed = 0.0
            self.write(self.ADDR_STATUS, self.STATUS_BATTERY)
        elif initiate == 0 and self._active:
            # Edge: utility restored. Snap output back to nominal, clear status.
            self._active = False
            self.elapsed = 0.0
            self.write(self.ADDR_STATUS, self.STATUS_ONLINE)
            self.write(self.ADDR_VOLTAGE, self.NOMINAL_VOLTAGE)
            return

        if self._active:
            self.elapsed += dt
            self.write(self.ADDR_VOLTAGE, self._voltage_for(self.elapsed))
            battery = self.read(self.ADDR_BATTERY)[0]
            self.write(self.ADDR_BATTERY, max(0, battery - 1))


#: Registry of available profiles. Add new ``Simulator`` subclasses here.
PROFILES: dict[str, Callable[[], Simulator]] = {
    "ups_transfer": UPSSimulator,
}


async def run_simulator(
    profile: str = "ups_transfer",
    host: str = "0.0.0.0",
    port: int = 502,
) -> None:
    """Run a simulator profile as a PyModbus TCP server with a physics loop.

    Builds a :class:`ModbusServerContext` around the chosen profile's slave
    context and runs :func:`StartAsyncTcpServer` concurrently with an async loop
    that calls ``step(0.1)`` every 100 ms to evolve the registers.
    """
    # Import the server lazily so the module imports even if the server
    # submodule is unavailable in an analysis-only environment.
    from pymodbus.server import StartAsyncTcpServer

    factory = PROFILES[profile]
    sim = factory()
    server_context = ModbusServerContext(slaves=sim.context, single=True)

    async def physics_loop() -> None:
        while True:
            sim.step(0.1)
            await asyncio.sleep(0.1)

    loop_task = asyncio.create_task(physics_loop())
    try:
        await StartAsyncTcpServer(context=server_context, address=(host, port))
    finally:
        loop_task.cancel()


if __name__ == "__main__":
    import os

    asyncio.run(run_simulator(os.environ.get("SIM_PROFILE", "ups_transfer")))
