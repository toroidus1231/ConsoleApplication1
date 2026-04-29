"""Module 3: Modbus TCP Poller.

Reads every register defined in a device's Config Context using PyModbus 3.7.
Returns a PollResult. Detects NaN/Inf on float decodes (usually a byte/word
order mismatch) and reports them as per-register errors rather than crashing.
"""

from __future__ import annotations

import math
import time
from typing import Any, Awaitable, Callable, Protocol

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder

from ..types import DeviceInfo, PollResult


ENDIAN_MAP = {"big": Endian.BIG, "little": Endian.LITTLE}

DECODER_MAP: dict[str, Callable[[BinaryPayloadDecoder], Any]] = {
    "uint16": lambda d: d.decode_16bit_uint(),
    "int16": lambda d: d.decode_16bit_int(),
    "uint32": lambda d: d.decode_32bit_uint(),
    "int32": lambda d: d.decode_32bit_int(),
    "float32": lambda d: d.decode_32bit_float(),
    "float64": lambda d: d.decode_64bit_float(),
    "boolean": lambda d: bool(d.decode_16bit_uint()),
    "bitmap": lambda d: d.decode_16bit_uint(),
}


class ModbusClientLike(Protocol):
    async def connect(self) -> bool: ...
    def close(self) -> None: ...
    async def read_holding_registers(self, address: int, count: int, slave: int) -> Any: ...
    async def read_input_registers(self, address: int, count: int, slave: int) -> Any: ...
    async def write_register(self, address: int, value: int, slave: int) -> Any: ...
    async def write_registers(self, address: int, values: list, slave: int) -> Any: ...


ClientFactory = Callable[[DeviceInfo], Awaitable[ModbusClientLike] | ModbusClientLike]


async def poll_device(
    device: DeviceInfo,
    *,
    client: ModbusClientLike | None = None,
) -> PollResult:
    """Poll all registers defined in ``device.config_context``.

    Args:
        device: Device to poll.
        client: Optional pre-built Modbus client (tests inject a fake here).
            When None, an AsyncModbusTcpClient is constructed from the device's
            connection parameters.
    """
    conn = device.config_context.get("connection", {}) or {}
    host = device.primary_ip or conn.get("host", "")
    port = int(conn.get("port", 502))
    unit_id = int(conn.get("unit_id", 1))
    byte_order = ENDIAN_MAP.get(conn.get("byte_order", "big"), Endian.BIG)
    word_order = ENDIAN_MAP.get(conn.get("word_order", "big"), Endian.BIG)
    timeout = float(conn.get("timeout_seconds", 5))
    retries = int(conn.get("retries", 3))

    owns_client = client is None
    if client is None:
        client = AsyncModbusTcpClient(
            host=host, port=port, timeout=timeout, retries=retries
        )

    measurements: dict = {}
    raw_bytes: dict = {}
    errors: dict = {}

    try:
        connected = await client.connect()
        if not connected:
            return PollResult(
                device_id=device.device_id,
                timestamp_ns=time.time_ns(),
                measurements={},
                raw_bytes={},
                protocol="modbus_tcp",
                source_ip=host,
                success=False,
                errors={"_connection": f"Failed to connect to {host}:{port}"},
            )

        for reg in device.config_context.get("registers", []) or []:
            name = reg["name"]
            try:
                value, raw_hex, err = await _read_register(
                    client, reg, unit_id, byte_order, word_order
                )
                if err is not None:
                    errors[name] = err
                    continue
                raw_bytes[name] = raw_hex
                measurements[name] = value
            except Exception as e:  # noqa: BLE001 — any decode failure is per-register
                errors[name] = f"{type(e).__name__}: {e}"
    finally:
        if owns_client:
            try:
                client.close()
            except Exception:  # noqa: BLE001 — close failures are non-fatal
                pass

    return PollResult(
        device_id=device.device_id,
        timestamp_ns=time.time_ns(),
        measurements=measurements,
        raw_bytes=raw_bytes,
        protocol="modbus_tcp",
        source_ip=host,
        success=len(errors) == 0,
        errors=errors,
    )


async def write_register(
    device: DeviceInfo,
    address: int,
    value: int,
    function_code: int = 6,
    *,
    client: ModbusClientLike | None = None,
) -> tuple[bool, str | None]:
    """Write a single register or a block of registers.

    Args:
        device: Device whose connection params we use.
        address: Register address to write.
        value: Value to write.
        function_code: 6 (write single register) or 16 (write multiple).
        client: Optional pre-built Modbus client (tests inject a fake).

    Returns ``(success, error_message)``. On success, error_message is None.
    """
    conn = device.config_context.get("connection", {}) or {}
    host = device.primary_ip or conn.get("host", "")
    port = int(conn.get("port", 502))
    unit_id = int(conn.get("unit_id", 1))
    timeout = float(conn.get("timeout_seconds", 5))
    retries = int(conn.get("retries", 3))

    owns_client = client is None
    if client is None:
        client = AsyncModbusTcpClient(
            host=host, port=port, timeout=timeout, retries=retries
        )

    try:
        connected = await client.connect()
        if not connected:
            return False, f"Failed to connect to {host}:{port}"

        try:
            if function_code == 6:
                result = await client.write_register(address=address, value=int(value), slave=unit_id)
            elif function_code == 16:
                result = await client.write_registers(
                    address=address, values=[int(value)], slave=unit_id
                )
            else:
                return False, f"Unsupported write function code: {function_code}"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"

        if hasattr(result, "isError") and result.isError():
            return False, f"Modbus error: {result}"
        return True, None
    finally:
        if owns_client:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass


async def _read_register(
    client: ModbusClientLike,
    reg: dict,
    unit_id: int,
    byte_order: Endian,
    word_order: Endian,
) -> tuple[Any, str, str | None]:
    """Read and decode a single register. Returns (value, raw_hex, error)."""
    address = int(reg["address"])
    count = int(reg.get("count", 1))
    fc = int(reg.get("function_code", 3))
    data_type = reg["data_type"]
    scale = float(reg.get("scale", 1.0))

    if fc == 3:
        result = await client.read_holding_registers(address=address, count=count, slave=unit_id)
    elif fc == 4:
        result = await client.read_input_registers(address=address, count=count, slave=unit_id)
    else:
        return None, "", f"Unsupported function code: {fc}"

    if result.isError():
        return None, "", f"Modbus error: {result}"

    raw_hex = "".join(f"{r:04x}" for r in result.registers)

    decoder = BinaryPayloadDecoder.fromRegisters(
        result.registers, byteorder=byte_order, wordorder=word_order
    )
    decode_fn = DECODER_MAP.get(data_type)
    if decode_fn is None:
        return None, raw_hex, f"Unknown data_type: {data_type}"

    value = decode_fn(decoder)

    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return (
            None,
            raw_hex,
            f"NaN/Inf detected. Likely byte/word order mismatch. Raw: {raw_hex}",
        )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = value * scale

    return value, raw_hex, None
