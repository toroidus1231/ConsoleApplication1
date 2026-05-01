"""Tests for src/instruments/detector.py — the hot-plug detector.

The detector orchestrates: (1) transport scanners that find new
candidate addresses, (2) identity probes that ask "what are you?",
(3) the (vendor, model) registry that picks the driver, (4) channel
construction + driver connect.

Tests inject fakes at every seam: scanners that return scripted
candidates, identity probes that return scripted (vendor, model,
serial), and a fake driver class that doesn't talk to real hardware.
"""

from __future__ import annotations

import asyncio
import pytest

from src.instruments import register
from src.instruments.detector import (
    DetectedInstrument, InstrumentDetector, USBSerialScanner,
    ModbusTcpScanner, _parse_idn, _regs_to_ascii,
)


# ---------------------------------------------------------------------------
# IDN + ASCII parsing
# ---------------------------------------------------------------------------


def test_parse_idn_full_format():
    out = _parse_idn("MEGGER,MIT525,SN-MIT-78421,FW-7.0.2")
    assert out == ("MEGGER", "MIT525", "SN-MIT-78421")


def test_parse_idn_strips_whitespace():
    out = _parse_idn("  Vitrek , 95X , SN-VTK-95X-12345 , 5.0 ")
    assert out == ("Vitrek", "95X", "SN-VTK-95X-12345")


def test_parse_idn_rejects_malformed():
    assert _parse_idn("just one field") is None
    assert _parse_idn("only,two") is None
    assert _parse_idn("") is None


def test_regs_to_ascii_strips_padding():
    # 'SN-MIT' = 4 hex pairs of ASCII
    regs = [0x534E, 0x2D4D, 0x4954, 0x0000]  # "SN-MIT" + null
    assert _regs_to_ascii(regs) == "SN-MIT"


# ---------------------------------------------------------------------------
# Fake driver + scanner harness
# ---------------------------------------------------------------------------


class _FakeDriver:
    """A driver registered as ('Acme', 'TestProbe') for detector tests."""
    VENDOR = "Acme"
    MODEL = "TestProbe"

    def __init__(self, channel):
        self._channel = channel
        self.connected = False
        self.disconnected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def execute(self, command):
        return {}

    async def verify_calibration(self):
        from src.instruments import CalibrationCert
        return CalibrationCert(
            instrument_serial="SN-FAKE-1",
            cert_id="CAL-2026-Q1",
            cert_hash="sha256:fake",
            issued_at="2026-01-01",
            expires_at="2026-12-31",
            issuer="Acme Cal Lab",
            standards_traceability=["NIST"],
        )


@pytest.fixture(autouse=True)
def _register_fake_driver():
    register(_FakeDriver)


class _ScriptedScanner:
    def __init__(self, scripts: list[list[tuple[str, str]]]):
        self._scripts = list(scripts)

    async def scan(self):
        if not self._scripts:
            return []
        return self._scripts.pop(0)


def _identity_probes_for(vendor: str, model: str, serial: str):
    async def probe(_address):
        return (vendor, model, serial)
    return {"serial": probe, "modbus_tcp": probe}


def _identity_probes_returning_none():
    async def probe(_address):
        return None
    return {"serial": probe, "modbus_tcp": probe}


# Patch _construct_and_connect to skip real channel I/O for tests.
@pytest.fixture
def patched_construct(monkeypatch):
    async def fake(self, driver_cls, transport, address):
        d = driver_cls(channel=None)
        await d.connect()
        return d
    monkeypatch.setattr(InstrumentDetector, "_construct_and_connect", fake)


# ---------------------------------------------------------------------------
# Scan-once + connection lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_once_detects_one_instrument(patched_construct):
    scanner = _ScriptedScanner([[("serial", "/dev/ttyACM0")]])
    detector = InstrumentDetector(
        [scanner],
        identity_probes=_identity_probes_for("Acme", "TestProbe", "SN-FAKE-1"),
    )
    added = await detector.scan_once()
    assert len(added) == 1
    inst = added[0]
    assert inst.vendor == "Acme"
    assert inst.model == "TestProbe"
    assert inst.address == "/dev/ttyACM0"
    assert inst.cal_cert_id == "CAL-2026-Q1"
    assert detector.connected[0].address == "/dev/ttyACM0"


@pytest.mark.asyncio
async def test_scan_once_skips_unknown_vendor(patched_construct):
    scanner = _ScriptedScanner([[("serial", "/dev/ttyACM0")]])
    detector = InstrumentDetector(
        [scanner],
        identity_probes=_identity_probes_for("UnknownVendor", "X", "SN-1"),
    )
    added = await detector.scan_once()
    assert added == []
    assert detector.connected == []


@pytest.mark.asyncio
async def test_scan_once_skips_when_identity_probe_returns_none(patched_construct):
    scanner = _ScriptedScanner([[("serial", "/dev/ttyACM0")]])
    detector = InstrumentDetector(
        [scanner],
        identity_probes=_identity_probes_returning_none(),
    )
    added = await detector.scan_once()
    assert added == []


@pytest.mark.asyncio
async def test_repeated_scan_does_not_double_add_same_address(patched_construct):
    scanner = _ScriptedScanner([
        [("serial", "/dev/ttyACM0")],
        [("serial", "/dev/ttyACM0")],  # second pass sees same device
    ])
    detector = InstrumentDetector(
        [scanner],
        identity_probes=_identity_probes_for("Acme", "TestProbe", "SN-FAKE-1"),
    )
    await detector.scan_once()
    added2 = await detector.scan_once()
    assert added2 == []
    assert len(detector.connected) == 1


@pytest.mark.asyncio
async def test_get_by_vendor_model(patched_construct):
    scanner = _ScriptedScanner([[("serial", "/dev/ttyACM0")]])
    detector = InstrumentDetector(
        [scanner],
        identity_probes=_identity_probes_for("Acme", "TestProbe", "SN-FAKE-1"),
    )
    await detector.scan_once()
    inst = detector.get_by_vendor_model("acme", "testprobe")  # case-insensitive
    assert inst is not None
    assert inst.serial == "SN-FAKE-1"


@pytest.mark.asyncio
async def test_get_by_vendor_model_returns_none_when_absent(patched_construct):
    detector = InstrumentDetector([_ScriptedScanner([])])
    assert detector.get_by_vendor_model("Acme", "Nope") is None


@pytest.mark.asyncio
async def test_stop_disconnects_all_drivers(patched_construct):
    scanner = _ScriptedScanner([[("serial", "/dev/ttyACM0"),
                                   ("serial", "/dev/ttyACM1")]])
    detector = InstrumentDetector(
        [scanner],
        identity_probes=_identity_probes_for("Acme", "TestProbe", "SN-X"),
    )
    await detector.scan_once()
    drivers = [i.driver for i in detector.connected]
    await detector.stop()
    assert all(d.disconnected for d in drivers)
    assert detector.connected == []


@pytest.mark.asyncio
async def test_start_stop_runs_loop(patched_construct):
    scanner = _ScriptedScanner([[("serial", "/dev/ttyACM0")]])
    detector = InstrumentDetector(
        [scanner],
        identity_probes=_identity_probes_for("Acme", "TestProbe", "SN-X"),
    )
    await detector.start(scan_interval_s=0.05)
    await asyncio.sleep(0.15)  # allow at least one scan
    await detector.stop()
    # No assertion about count — just verify start/stop don't blow up


# ---------------------------------------------------------------------------
# Scanner-level tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usb_scanner_finds_new_devices(tmp_path, monkeypatch):
    # Stub glob.glob so we don't depend on /dev/ contents
    import src.instruments.detector as det_mod
    seen_paths = []

    def fake_glob(pattern):
        if pattern == "/dev/ttyACM*":
            return seen_paths
        return []
    monkeypatch.setattr("glob.glob", fake_glob)

    scanner = USBSerialScanner(patterns=("/dev/ttyACM*",))
    assert await scanner.scan() == []
    seen_paths.append("/dev/ttyACM0")
    new = await scanner.scan()
    assert new == [("serial", "/dev/ttyACM0")]
    # Second scan with same set: no new devices
    assert await scanner.scan() == []


@pytest.mark.asyncio
async def test_modbus_scanner_only_returns_reachable(monkeypatch):
    async def fake_open_connection(host, port):
        if host == "10.4.0.50":
            class FakeReader: pass
            class FakeWriter:
                def close(self): pass
                async def wait_closed(self): pass
            return FakeReader(), FakeWriter()
        raise OSError("connection refused")

    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)
    scanner = ModbusTcpScanner([("10.4.0.50", 502), ("10.4.0.99", 502)])
    out = await scanner.scan()
    assert ("modbus_tcp", "10.4.0.50:502") in out
    assert ("modbus_tcp", "10.4.0.99:502") not in out


@pytest.mark.asyncio
async def test_modbus_scanner_does_not_re_emit(monkeypatch):
    async def fake_open_connection(host, port):
        class FakeReader: pass
        class FakeWriter:
            def close(self): pass
            async def wait_closed(self): pass
        return FakeReader(), FakeWriter()

    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)
    scanner = ModbusTcpScanner([("10.4.0.50", 502)])
    first = await scanner.scan()
    second = await scanner.scan()
    assert first == [("modbus_tcp", "10.4.0.50:502")]
    assert second == []


def test_usb_scanner_forget_re_emits(monkeypatch):
    monkeypatch.setattr("glob.glob",
                         lambda pat: ["/dev/ttyACM0"] if pat == "/dev/ttyACM*" else [])
    scanner = USBSerialScanner(patterns=("/dev/ttyACM*",))
    asyncio.get_event_loop().run_until_complete(scanner.scan())
    scanner.forget("/dev/ttyACM0")
    new = asyncio.get_event_loop().run_until_complete(scanner.scan())
    assert new == [("serial", "/dev/ttyACM0")]


# ---------------------------------------------------------------------------
# DetectedInstrument shape
# ---------------------------------------------------------------------------


def test_detected_instrument_defaults():
    inst = DetectedInstrument(
        transport="serial", address="/dev/ttyACM0",
        vendor="Acme", model="X", serial="S",
        driver=object(),
    )
    assert inst.state == "connected"
    assert inst.cal_cert_id == ""
    assert inst.cal_expires_at == ""
    assert inst.last_seen_ts > 0
