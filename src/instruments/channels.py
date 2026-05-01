"""Real channel implementations for instrument drivers.

The drivers under src/instruments/ talk through three abstract
channels:

  - SerialChannel       (SCPI / ASCII over USB-CDC or RS-232)
  - ModbusClient        (Modbus TCP / RTU)
  - BluetoothChannel    (BLE/SPP for handheld instruments like DLRO10X)

Production wires these against pyserial-asyncio, pymodbus, and bleak.
Tests inject recorded-trace fakes (see tests/instruments/).

The implementations here are thin: connection lifecycle, line framing,
basic error mapping. Anything richer (auto-reconnect on transient
failure, exponential back-off, retry budgets) belongs in the detector
module which orchestrates these.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from src.instruments.megger_mit525 import InstrumentError


# ---------------------------------------------------------------------------
# SerialChannel — pyserial-asyncio
# ---------------------------------------------------------------------------


@dataclass
class SerialPortConfig:
    port: str
    baud: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 1
    line_terminator: str = "\r"
    read_timeout_s: float = 5.0


class PySerialAsyncSerialChannel:
    """SerialChannel implementation backed by pyserial-asyncio.

    Used for SCPI instruments (Megger MIT525, Vitrek 95X) and for some
    Bluetooth-SPP-over-rfcomm devices that present as /dev/rfcomm0.
    """

    def __init__(self, config: SerialPortConfig):
        self._cfg = config
        self._reader = None
        self._writer = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        try:
            import serial_asyncio  # pyserial-asyncio
        except ImportError as e:
            raise InstrumentError(
                "pyserial-asyncio not installed; "
                "pip install pyserial-asyncio"
            ) from e
        try:
            self._reader, self._writer = await serial_asyncio.open_serial_connection(
                url=self._cfg.port, baudrate=self._cfg.baud,
                bytesize=self._cfg.bytesize,
                parity=self._cfg.parity,
                stopbits=self._cfg.stopbits,
            )
        except Exception as e:
            raise InstrumentError(
                f"could not open {self._cfg.port}: {type(e).__name__}: {e}"
            ) from e

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._writer = None
        self._reader = None

    async def write_line(self, line: str) -> None:
        if self._writer is None:
            raise InstrumentError("serial channel not open")
        async with self._lock:
            payload = (line + self._cfg.line_terminator).encode("ascii")
            self._writer.write(payload)
            await self._writer.drain()

    async def read_line(self, timeout_s: Optional[float] = None) -> str:
        if self._reader is None:
            raise InstrumentError("serial channel not open")
        timeout = timeout_s if timeout_s is not None else self._cfg.read_timeout_s
        try:
            data = await asyncio.wait_for(
                self._reader.readuntil(separator=self._cfg.line_terminator.encode("ascii")),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError(
                f"serial read timed out after {timeout}s on {self._cfg.port}"
            )
        return data.decode("ascii", errors="replace").rstrip("\r\n")


# ---------------------------------------------------------------------------
# ModbusTcpChannel — pymodbus adapter
# ---------------------------------------------------------------------------


@dataclass
class ModbusEndpoint:
    host: str
    port: int = 502
    timeout_s: float = 3.0
    retries: int = 2


class PymodbusTcpClient:
    """ModbusClient implementation backed by pymodbus.

    Used for the four Modbus-TCP instruments: Qualitrol 118ITM,
    Vaisala OPT100, SEL-751, Cat EMCP. Same interface the recorded-
    trace test stub implements; drop-in.
    """

    def __init__(self, endpoint: ModbusEndpoint):
        self._ep = endpoint
        self._client = None

    async def connect(self) -> None:
        try:
            from pymodbus.client import AsyncModbusTcpClient
        except ImportError as e:
            raise InstrumentError(
                "pymodbus not installed; pip install pymodbus"
            ) from e
        self._client = AsyncModbusTcpClient(
            self._ep.host, port=self._ep.port,
            timeout=self._ep.timeout_s, retries=self._ep.retries,
        )
        ok = await self._client.connect()
        if not ok:
            raise InstrumentError(
                f"could not connect to {self._ep.host}:{self._ep.port}"
            )

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    async def read_holding_registers(self, address: int, count: int,
                                      slave: int = 1) -> list[int]:
        self._require_connected()
        result = await self._client.read_holding_registers(
            address=address, count=count, slave=slave,
        )
        if result.isError():
            raise InstrumentError(
                f"read_holding_registers({address}, {count}) failed: {result}"
            )
        return list(result.registers)

    async def read_input_registers(self, address: int, count: int,
                                    slave: int = 1) -> list[int]:
        self._require_connected()
        result = await self._client.read_input_registers(
            address=address, count=count, slave=slave,
        )
        if result.isError():
            raise InstrumentError(
                f"read_input_registers({address}, {count}) failed: {result}"
            )
        return list(result.registers)

    async def write_register(self, address: int, value: int,
                              slave: int = 1) -> None:
        self._require_connected()
        result = await self._client.write_register(
            address=address, value=value, slave=slave,
        )
        if result.isError():
            raise InstrumentError(
                f"write_register({address}, {value}) failed: {result}"
            )

    async def write_coil(self, address: int, value: bool,
                          slave: int = 1) -> None:
        self._require_connected()
        result = await self._client.write_coil(
            address=address, value=value, slave=slave,
        )
        if result.isError():
            raise InstrumentError(
                f"write_coil({address}, {value}) failed: {result}"
            )

    def _require_connected(self) -> None:
        if self._client is None:
            raise InstrumentError("modbus channel not open")


# ---------------------------------------------------------------------------
# BluetoothChannel — bleak adapter (BLE) or rfcomm-as-serial (BR/EDR/SPP)
# ---------------------------------------------------------------------------


@dataclass
class BluetoothPeripheral:
    address: str            # MAC address (XX:XX:XX:XX:XX:XX)
    char_write_uuid: str    # GATT characteristic to write to
    char_notify_uuid: str   # GATT characteristic to subscribe for replies
    line_terminator: str = "\r\n"


class BleakBluetoothChannel:
    """SerialChannel-shaped wrapper for a BLE peripheral.

    Many handheld test instruments (Megger DLRO10X, Fluke handhelds)
    pair as BLE peripherals with a "write characteristic" + "notify
    characteristic" — write a command, get the reply on the notify
    channel. We adapt that to the line-oriented SerialChannel interface
    the drivers expect.

    SPP-over-rfcomm devices (older instruments) bind to /dev/rfcomm0
    via `rfcomm bind` and then use PySerialAsyncSerialChannel.
    """

    def __init__(self, peripheral: BluetoothPeripheral):
        self._peripheral = peripheral
        self._client = None
        self._rx_queue: asyncio.Queue[str] | None = None
        self._buffer = ""

    async def open(self) -> None:
        try:
            from bleak import BleakClient
        except ImportError as e:
            raise InstrumentError(
                "bleak not installed; pip install bleak (Linux/Mac/Win)"
            ) from e
        self._client = BleakClient(self._peripheral.address)
        await self._client.connect()
        self._rx_queue = asyncio.Queue()
        await self._client.start_notify(
            self._peripheral.char_notify_uuid, self._on_notify,
        )

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.stop_notify(self._peripheral.char_notify_uuid)
            except Exception:
                pass
            await self._client.disconnect()
            self._client = None
        self._rx_queue = None
        self._buffer = ""

    async def write_line(self, line: str) -> None:
        if self._client is None:
            raise InstrumentError("bluetooth channel not open")
        payload = (line + self._peripheral.line_terminator).encode("ascii")
        await self._client.write_gatt_char(
            self._peripheral.char_write_uuid, payload, response=True,
        )

    async def read_line(self, timeout_s: float = 5.0) -> str:
        if self._rx_queue is None:
            raise InstrumentError("bluetooth channel not open")
        # Drain any complete lines already in the buffer.
        if self._peripheral.line_terminator in self._buffer:
            line, _, self._buffer = self._buffer.partition(self._peripheral.line_terminator)
            return line
        # Otherwise wait for more bytes.
        deadline_chunk = timeout_s
        while True:
            try:
                chunk = await asyncio.wait_for(self._rx_queue.get(),
                                                timeout=deadline_chunk)
            except asyncio.TimeoutError:
                raise asyncio.TimeoutError(
                    f"bluetooth read timed out after {timeout_s}s "
                    f"on {self._peripheral.address}"
                )
            self._buffer += chunk
            if self._peripheral.line_terminator in self._buffer:
                line, _, self._buffer = self._buffer.partition(self._peripheral.line_terminator)
                return line

    def _on_notify(self, _sender, data: bytes) -> None:
        if self._rx_queue is not None:
            try:
                self._rx_queue.put_nowait(data.decode("ascii", errors="replace"))
            except asyncio.QueueFull:
                pass
