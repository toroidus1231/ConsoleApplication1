"""Tests for Module 3 — Modbus TCP Poller.

Uses an in-memory fake Modbus client that matches the PyModbus 3.7 async
client's surface (connect/close, read_holding_registers, read_input_registers).
This avoids needing a live simulator in unit tests.
"""

import pytest

from src.pollers.modbus import poll_device
from src.types import DeviceInfo


class FakeResult:
    def __init__(self, registers=None, error=False):
        self.registers = registers or []
        self._error = error

    def isError(self):
        return self._error

    def __repr__(self):
        return f"FakeResult(error={self._error})"


class FakeClient:
    """In-memory PyModbus-compatible fake.

    `holding` / `input` are dicts {address: [register_values...]}. Each entry is
    the exact list returned for a read at that address. Missing addresses
    return FakeResult(error=True).
    """

    def __init__(self, holding=None, input_=None, connect_ok=True, read_exc=None):
        self.holding = holding or {}
        self.input = input_ or {}
        self.connect_ok = connect_ok
        self.read_exc = read_exc
        self.closed = False
        self.calls: list[tuple] = []

    async def connect(self):
        return self.connect_ok

    def close(self):
        self.closed = True

    async def read_holding_registers(self, address, count, slave):
        self.calls.append(("h", address, count, slave))
        if self.read_exc:
            raise self.read_exc
        if address not in self.holding:
            return FakeResult(error=True)
        regs = self.holding[address]
        return FakeResult(registers=regs[:count])

    async def read_input_registers(self, address, count, slave):
        self.calls.append(("i", address, count, slave))
        if self.read_exc:
            raise self.read_exc
        if address not in self.input:
            return FakeResult(error=True)
        regs = self.input[address]
        return FakeResult(registers=regs[:count])


def _device(registers, connection=None):
    return DeviceInfo(
        device_id="dev-1",
        name="fake",
        primary_ip="10.0.0.5",
        device_type_slug="cm2000",
        config_context={
            "protocol": "modbus_tcp",
            "connection": connection or {"port": 502, "unit_id": 1, "byte_order": "big", "word_order": "big"},
            "registers": registers,
        },
        protocol="modbus_tcp",
        site="DC1",
        rack="A3",
        position=10,
    )


async def test_decodes_uint16_holding_register():
    device = _device([
        {"name": "freq", "address": 1001, "count": 1, "function_code": 3,
         "data_type": "uint16", "unit": "Hz"}
    ])
    # 60 Hz encoded as a single 16-bit register = 0x003C
    fake = FakeClient(holding={1001: [60]})

    result = await poll_device(device, client=fake)

    assert result.success is True
    assert result.measurements["freq"] == 60
    assert result.raw_bytes["freq"] == "003c"
    assert fake.calls == [("h", 1001, 1, 1)]
    # Caller owns the injected client — we do not close it.
    assert fake.closed is False


async def test_decodes_float32_with_word_order_big_big():
    # 480.0 as IEEE-754 big-endian big-word = 0x43F0 0000
    device = _device([
        {"name": "voltage", "address": 32020, "count": 2, "function_code": 3,
         "data_type": "float32", "unit": "V"}
    ])
    fake = FakeClient(holding={32020: [0x43F0, 0x0000]})

    result = await poll_device(device, client=fake)

    assert result.success is True
    assert result.measurements["voltage"] == pytest.approx(480.0)


async def test_nan_on_wrong_word_order_reports_error():
    # Same payload as above, but this device config claims big-endian bytes but
    # little-endian words. Decoding 0x43F0 0000 as little-word interprets it as
    # 0x0000 43F0 which decodes to a denormal ~2.4e-41 (subnormal, not NaN).
    # Use a payload that *will* produce NaN when word-order-flipped.
    # Choose 0x7FC0 0000 (NaN): if we flipped to 0x0000 7FC0, that is a tiny
    # denormal so still not NaN. Instead, pick 0xFFC0 0000 (NaN) which flipped
    # becomes 0x0000 FFC0, still denormal.
    # The reliable NaN case: device returns actual NaN bytes. Craft 0x7FC1 0000
    # which IS NaN when decoded directly. Simulate the "mismatch" case by
    # feeding NaN bytes that the decoder produces NaN for.
    device = _device([
        {"name": "v", "address": 32020, "count": 2, "function_code": 3,
         "data_type": "float32", "unit": "V"}
    ])
    fake = FakeClient(holding={32020: [0x7FC0, 0x0001]})  # qNaN

    result = await poll_device(device, client=fake)

    assert result.success is False
    assert "v" in result.errors
    assert "NaN/Inf" in result.errors["v"]


async def test_function_code_4_hits_input_registers():
    device = _device([
        {"name": "x", "address": 100, "count": 1, "function_code": 4,
         "data_type": "uint16", "unit": "n"}
    ])
    fake = FakeClient(input_={100: [7]})

    result = await poll_device(device, client=fake)

    assert result.measurements["x"] == 7
    assert fake.calls == [("i", 100, 1, 1)]


async def test_unsupported_function_code_reports_error():
    device = _device([
        {"name": "x", "address": 100, "count": 1, "function_code": 99,
         "data_type": "uint16", "unit": "n"}
    ])
    fake = FakeClient(holding={100: [1]})

    result = await poll_device(device, client=fake)

    assert result.success is False
    assert "Unsupported function code" in result.errors["x"]


async def test_connection_failure_returns_connection_error():
    device = _device([
        {"name": "x", "address": 100, "count": 1, "function_code": 3,
         "data_type": "uint16", "unit": "n"}
    ])
    fake = FakeClient(connect_ok=False)

    result = await poll_device(device, client=fake)

    assert result.success is False
    assert "_connection" in result.errors
    assert result.measurements == {}


async def test_modbus_error_response_reports_per_register_error():
    device = _device([
        {"name": "good", "address": 100, "count": 1, "function_code": 3,
         "data_type": "uint16", "unit": "n"},
        {"name": "bad", "address": 200, "count": 1, "function_code": 3,
         "data_type": "uint16", "unit": "n"},
    ])
    fake = FakeClient(holding={100: [42]})  # 200 missing -> error

    result = await poll_device(device, client=fake)

    assert result.success is False
    assert result.measurements == {"good": 42}
    assert "Modbus error" in result.errors["bad"]


async def test_scale_is_applied_to_integer_values():
    device = _device([
        {"name": "voltage_x10", "address": 100, "count": 1, "function_code": 3,
         "data_type": "uint16", "scale": 0.1, "unit": "V"}
    ])
    fake = FakeClient(holding={100: [4800]})  # Raw 4800 * 0.1 = 480.0V

    result = await poll_device(device, client=fake)

    assert result.measurements["voltage_x10"] == pytest.approx(480.0)


async def test_read_exception_captured_as_per_register_error():
    device = _device([
        {"name": "x", "address": 100, "count": 1, "function_code": 3,
         "data_type": "uint16", "unit": "n"}
    ])
    fake = FakeClient(read_exc=TimeoutError("read timeout"))

    result = await poll_device(device, client=fake)

    assert result.success is False
    assert "TimeoutError" in result.errors["x"]


async def test_unknown_data_type_reports_error_without_crashing():
    device = _device([
        {"name": "x", "address": 100, "count": 1, "function_code": 3,
         "data_type": "hexquadruple", "unit": "n"}
    ])
    fake = FakeClient(holding={100: [1]})

    result = await poll_device(device, client=fake)

    assert "Unknown data_type" in result.errors["x"]
