"""Integration test that runs a REAL PyModbus TCP server on localhost and
hits it with the REAL pollers/modbus.poll_device implementation. No fakes
for the poller — this exercises the full library path including
BinaryPayloadDecoder, byte/word ordering, and FC 3 register reads.

PyModbus addressing note: by default ModbusSlaveContext uses 1-based wire
addressing (zero_mode=False). We pass zero_mode=True so client address N
hits block index N — matches what real Schneider/SEL/etc. devices do.
"""

import asyncio
import socket

import pytest
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext, ModbusSlaveContext
from pymodbus.server import StartAsyncTcpServer

from src.pollers.modbus import poll_device, write_register
from src.types import DeviceInfo


def _free_port() -> int:
    """Get an unused TCP port from the OS so parallel test runs don't collide."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _device(port: int, host: str = "127.0.0.1"):
    return DeviceInfo(
        device_id="cm2000-1", name="cm2000-A3-01", primary_ip=host,
        device_type_slug="cm2000",
        config_context={
            "protocol": "modbus_tcp",
            "connection": {
                "port": port, "unit_id": 1, "byte_order": "big", "word_order": "big",
                "timeout_seconds": 2, "retries": 1,
            },
            "registers": [
                {"name": "frequency_hz", "address": 0, "count": 1,
                 "function_code": 3, "data_type": "uint16",
                 "scale": 0.01, "unit": "Hz"},
                {"name": "voltage", "address": 10, "count": 2,
                 "function_code": 3, "data_type": "float32",
                 "unit": "V"},
                {"name": "energy_kwh", "address": 20, "count": 4,
                 "function_code": 3, "data_type": "float64",
                 "unit": "kWh"},
            ],
        },
        protocol="modbus_tcp", site="DC1", rack="A3", position=10,
    )


@pytest.fixture
async def modbus_server():
    """Start a PyModbus TCP server with seeded register values. Yields
    (port, block) so each test can assert against the same datastore."""
    block = ModbusSequentialDataBlock(0, [0] * 200)

    # Seed registers via the same setValues PyModbus's clients see.
    block.setValues(0, [6000])  # 60.00 Hz with scale 0.01
    # float32 480.0V big-endian big-word = 0x43F0_0000
    block.setValues(10, [0x43F0, 0x0000])
    # float64 12345.6789 kWh
    import struct
    raw = struct.pack(">d", 12345.6789)
    regs = [int.from_bytes(raw[i:i + 2], "big") for i in (0, 2, 4, 6)]
    block.setValues(20, regs)

    # zero_mode=True → client address N maps to block index N (no -1 offset).
    slave = ModbusSlaveContext(hr=block, zero_mode=True)
    context = ModbusServerContext(slaves=slave, single=True)

    port = _free_port()
    server_task = asyncio.create_task(
        StartAsyncTcpServer(context=context, address=("127.0.0.1", port))
    )
    await asyncio.sleep(0.2)
    try:
        yield port, block
    finally:
        server_task.cancel()
        try:
            await server_task
        except BaseException:
            pass
        await asyncio.sleep(0.05)


async def test_poll_real_modbus_server_decodes_uint16_float32_float64(modbus_server):
    """The whole point: actually exercise PyModbus's TCP client + server
    round trip, decoder, byte/word ordering, and scaling on real wire bytes."""
    port, _ = modbus_server
    result = await poll_device(_device(port=port))

    assert result.success is True, f"errors: {result.errors}"
    assert result.measurements["frequency_hz"] == pytest.approx(60.00, abs=0.01)
    assert result.measurements["voltage"] == pytest.approx(480.0, abs=0.01)
    assert result.measurements["energy_kwh"] == pytest.approx(12345.6789, abs=0.0001)


async def test_real_modbus_write_then_read_round_trip(modbus_server):
    """Write a register via write_register, then read it back via poll_device."""
    port, _ = modbus_server
    ok, err = await write_register(_device(port=port), address=0, value=5990, function_code=6)
    assert ok is True, err

    result = await poll_device(_device(port=port))
    # 5990 * 0.01 = 59.9
    assert result.measurements["frequency_hz"] == pytest.approx(59.90, abs=0.01)


async def test_real_modbus_handles_unreachable_device():
    """Connection to a nonexistent server fails cleanly, no exception."""
    device = _device(port=1)  # privileged port, nothing listening
    result = await poll_device(device)
    assert result.success is False
    assert result.measurements == {}
