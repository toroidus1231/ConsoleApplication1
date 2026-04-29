"""Tests for Module 23 — serial-SCPI bench instrument poller."""

import pytest

from src.pollers.serial_scpi import poll_device
from src.types import DeviceInfo


class FakeSerial:
    """In-memory serial bus. ``responses`` maps a written SCPI command
    (with the trailing newline stripped) to a sequence of canned reply
    bytes (one per call). Tests can assert against ``writes`` and
    ``opened`` / ``closed`` states.
    """

    def __init__(self, responses=None, write_exc=None, read_exc=None):
        self.responses = {k: list(v) for k, v in (responses or {}).items()}
        self.write_exc = write_exc
        self.read_exc = read_exc
        self.writes: list[bytes] = []
        self.opened = False
        self.closed = False
        self._last_cmd = None

    def open(self):
        self.opened = True

    def close(self):
        self.closed = True

    def write(self, data: bytes):
        if self.write_exc:
            raise self.write_exc
        self.writes.append(data)
        # Remember the last command we saw so readline knows what to reply to
        self._last_cmd = data.decode("utf-8").strip()

    def readline(self) -> bytes:
        if self.read_exc:
            raise self.read_exc
        cmd = self._last_cmd or ""
        replies = self.responses.get(cmd)
        if not replies:
            return b""
        return replies.pop(0)


def _hipot_device(registers=None, instrument=None) -> DeviceInfo:
    return DeviceInfo(
        device_id="hipot-1",
        name="vitrek-95x-bench",
        primary_ip="",
        device_type_slug="vitrek-95x",
        config_context={
            "protocol": "serial_scpi",
            "connection": {"port": "/dev/ttyUSB0", "baud": 9600, "newline": "\r\n"},
            "instrument": instrument or {
                "vendor": "Vitrek", "model": "95X",
                "serial_number": "SN-12345",
                "calibration_cert_hash": "sha256:abc123",
            },
            "registers": registers or [
                {"name": "leakage_current_ma", "scpi": ":READ:LEAK?",
                 "data_type": "float", "unit": "mA"},
                {"name": "test_voltage_kv", "scpi": ":READ:VOLT?",
                 "data_type": "float", "unit": "kV"},
            ],
        },
        protocol="serial_scpi",
        site="bench",
        rack="",
        position=0,
    )


# --- Happy path -------------------------------------------------------------


async def test_reads_two_scpi_registers_and_decodes_floats():
    fake = FakeSerial(responses={
        ":READ:LEAK?": [b"0.3142\r\n"],
        ":READ:VOLT?": [b"2.5000\r\n"],
    })

    result = await poll_device(_hipot_device(), client=fake)

    assert result.success is True
    assert result.protocol == "serial_scpi"
    assert result.measurements["leakage_current_ma"] == pytest.approx(0.3142)
    assert result.measurements["test_voltage_kv"] == pytest.approx(2.5)


async def test_writes_each_scpi_command_with_newline():
    fake = FakeSerial(responses={
        ":READ:LEAK?": [b"0.3\r\n"],
        ":READ:VOLT?": [b"2.5\r\n"],
    })
    await poll_device(_hipot_device(), client=fake)
    assert fake.writes == [b":READ:LEAK?\r\n", b":READ:VOLT?\r\n"]


async def test_raw_bytes_record_command_response_and_calibration_cert():
    """Each register's raw_bytes carries the SCPI command, raw response,
    instrument identity, and calibration cert — that's the legal-trace
    audit data."""
    fake = FakeSerial(responses={
        ":READ:LEAK?": [b"0.3\r\n"],
        ":READ:VOLT?": [b"2.5\r\n"],
    })
    result = await poll_device(_hipot_device(), client=fake)

    raw = result.raw_bytes["leakage_current_ma"]
    assert ":READ:LEAK?" in raw
    assert "0.3" in raw
    assert "Vitrek/95X/SN-12345" in raw
    assert "sha256:abc123" in raw


async def test_source_ip_carries_instrument_identity_when_no_network_ip():
    fake = FakeSerial(responses={":READ:VOLT?": [b"1.0\r\n"]})
    device = _hipot_device(registers=[{"name": "v", "scpi": ":READ:VOLT?", "data_type": "float"}])
    result = await poll_device(device, client=fake)
    assert result.source_ip == "Vitrek/95X/SN-12345"


# --- Decoders ---------------------------------------------------------------


async def test_int_decoder():
    fake = FakeSerial(responses={":READ:OPS?": [b"42\r\n"]})
    device = _hipot_device(registers=[
        {"name": "ops", "scpi": ":READ:OPS?", "data_type": "int"},
    ])
    result = await poll_device(device, client=fake)
    assert result.measurements["ops"] == 42


async def test_string_decoder_for_idn():
    fake = FakeSerial(responses={"*IDN?": [b"Vitrek,95X,SN-1,1.0\r\n"]})
    device = _hipot_device(registers=[
        {"name": "idn", "scpi": "*IDN?", "data_type": "string"},
    ])
    result = await poll_device(device, client=fake)
    assert result.measurements["idn"] == "Vitrek,95X,SN-1,1.0"


async def test_bool_decoder():
    fake = FakeSerial(responses={
        ":READ:LIVE?": [b"1\r\n"],
        ":READ:OFF?": [b"0\r\n"],
    })
    device = _hipot_device(registers=[
        {"name": "is_live", "scpi": ":READ:LIVE?", "data_type": "bool"},
        {"name": "is_off",  "scpi": ":READ:OFF?",  "data_type": "bool"},
    ])
    result = await poll_device(device, client=fake)
    assert result.measurements == {"is_live": True, "is_off": False}


async def test_unknown_data_type_reported_as_per_register_error():
    fake = FakeSerial(responses={":Q?": [b"x\r\n"]})
    device = _hipot_device(registers=[
        {"name": "v", "scpi": ":Q?", "data_type": "fraction"},
    ])
    result = await poll_device(device, client=fake)
    assert result.success is False
    assert "unknown data_type" in result.errors["v"]


async def test_non_numeric_response_when_float_expected():
    fake = FakeSerial(responses={":READ:VOLT?": [b"ERR\r\n"]})
    result = await poll_device(_hipot_device(), client=fake)
    assert result.success is False
    assert "non-float response" in result.errors["test_voltage_kv"]


async def test_scale_applied_to_numeric():
    fake = FakeSerial(responses={":READ:VOLT?": [b"2500\r\n"]})
    device = _hipot_device(registers=[
        {"name": "voltage_kv", "scpi": ":READ:VOLT?",
         "data_type": "float", "scale": 0.001, "unit": "kV"},
    ])
    result = await poll_device(device, client=fake)
    assert result.measurements["voltage_kv"] == pytest.approx(2.5)


# --- Failure modes ----------------------------------------------------------


async def test_write_exception_captured_per_register():
    fake = FakeSerial(write_exc=TimeoutError("serial busy"))
    result = await poll_device(_hipot_device(), client=fake)
    assert result.success is False
    assert "TimeoutError" in result.errors["leakage_current_ma"]
    assert "TimeoutError" in result.errors["test_voltage_kv"]


async def test_read_exception_captured_per_register():
    fake = FakeSerial(
        responses={":READ:LEAK?": [b""], ":READ:VOLT?": [b""]},
        read_exc=OSError("port closed"),
    )
    result = await poll_device(_hipot_device(), client=fake)
    assert result.success is False
    assert "OSError" in result.errors["leakage_current_ma"]


async def test_partial_failure_other_registers_still_succeed():
    fake = FakeSerial(responses={
        ":READ:LEAK?": [b"0.3\r\n"],
        ":READ:VOLT?": [b"NOT_NUMBER\r\n"],
    })
    result = await poll_device(_hipot_device(), client=fake)
    assert result.success is False
    assert result.measurements["leakage_current_ma"] == pytest.approx(0.3)
    assert "test_voltage_kv" in result.errors


# --- Lifecycle --------------------------------------------------------------


async def test_caller_owned_client_not_closed_by_poller():
    fake = FakeSerial(responses={":READ:LEAK?": [b"0\r\n"], ":READ:VOLT?": [b"0\r\n"]})
    await poll_device(_hipot_device(), client=fake)
    assert fake.opened is True
    assert fake.closed is False  # caller injected the client, caller closes
