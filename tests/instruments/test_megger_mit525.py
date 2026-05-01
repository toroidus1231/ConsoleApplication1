"""Recorded-trace tests for the Megger MIT525 driver.

The driver talks ASCII SCPI over a SerialChannel. Tests inject a
RecordedSerialChannel preloaded with the request/response sequence we
expect. If the driver issues a command not in the script, the channel
raises — that catches protocol drift between code and manual.
"""

from __future__ import annotations

import asyncio
from collections import deque
import pytest

from src.instruments.megger_mit525 import (
    MeggerMIT525, MIT525Config, InstrumentError,
)


class RecordedSerialChannel:
    def __init__(self, script: list[tuple[str, str]]):
        # script entries: (expected_request, response)
        self._script = deque(script)
        self.history: list[tuple[str, str]] = []

    async def write_line(self, line: str) -> None:
        if not self._script:
            raise AssertionError(f"unexpected write: {line!r} (script exhausted)")
        expected, _resp = self._script[0]
        if line.strip() != expected.strip():
            raise AssertionError(f"expected {expected!r}, got {line!r}")
        self.history.append(("W", line))

    async def read_line(self, timeout_s: float = 5.0) -> str:
        if not self._script:
            raise AssertionError("read with no pending script entry")
        _expected, resp = self._script.popleft()
        if resp is None:
            raise asyncio.TimeoutError("scripted timeout")
        self.history.append(("R", resp))
        return resp


@pytest.mark.asyncio
async def test_connect_reads_idn():
    ch = RecordedSerialChannel([
        ("*IDN?", "MEGGER,MIT525,SN-MIT-78421,FW-7.0.2"),
    ])
    inst = MeggerMIT525(ch)
    await inst.connect()
    assert inst._idn == "MEGGER,MIT525,SN-MIT-78421,FW-7.0.2"


@pytest.mark.asyncio
async def test_connect_rejects_bad_idn():
    ch = RecordedSerialChannel([
        ("*IDN?", "OTHER-VENDOR,WRONG-MODEL,1234,1.0"),
    ])
    inst = MeggerMIT525(ch)
    with pytest.raises(InstrumentError, match="unexpected IDN"):
        await inst.connect()


@pytest.mark.asyncio
async def test_verify_calibration_returns_cert():
    ch = RecordedSerialChannel([
        ("*IDN?", "MEGGER,MIT525,SN-MIT-78421,FW-7.0.2"),
        ("SYST:CAL?", "CAL-2026-Q1-A847,2026-12-31"),
    ])
    inst = MeggerMIT525(ch)
    await inst.connect()
    cert = await inst.verify_calibration()
    assert cert.instrument_serial == "SN-MIT-78421"
    assert cert.cert_id == "CAL-2026-Q1-A847"
    assert cert.expires_at == "2026-12-31"
    assert cert.cert_hash.startswith("sha256:megger-mit525-")
    assert "NIST" in cert.standards_traceability


@pytest.mark.asyncio
async def test_execute_runs_six_phase_pairs():
    ch = RecordedSerialChannel([
        ("*IDN?", "MEGGER,MIT525,SN-MIT-78421,FW-7.0.2"),
        ("INSU:VOLT 1000", "OK"),
        ("INSU:DUR 60", "OK"),
        # 6 pairs × (start, status-poll → COMPLETE, READ:RES?)
        *[
            v
            for _ in range(6)
            for v in [
                ("INSU:STAR", "OK"),
                ("INSU:STAT?", "COMPLETE"),
                ("READ:RES?", "5912000000"),  # 5912 MΩ in ohms
            ]
        ],
        ("SYST:CAL?", "CAL-2026-Q1-A847,2026-12-31"),
    ])
    inst = MeggerMIT525(ch)
    await inst.connect()
    result = await inst.execute({
        "test_voltage_v": 1000,
        "duration_seconds": 60,
        "min_megohm": 100.0,
    })
    assert len(result["readings"]) == 6
    assert all(r["passed"] for r in result["readings"])
    assert result["min_megohm"] == 5912.0
    assert result["instrument_serial"] == "SN-MIT-78421"


@pytest.mark.asyncio
async def test_execute_rejects_invalid_voltage():
    ch = RecordedSerialChannel([
        ("*IDN?", "MEGGER,MIT525,SN-MIT-78421,FW-7.0.2"),
    ])
    inst = MeggerMIT525(ch)
    await inst.connect()
    with pytest.raises(InstrumentError, match="unsupported test voltage"):
        await inst.execute({"test_voltage_v": 9999, "duration_seconds": 60,
                            "min_megohm": 100})


@pytest.mark.asyncio
async def test_execute_handles_fault_status():
    # Voltage applied, then status poll reports FAULT mid-test.
    ch = RecordedSerialChannel([
        ("*IDN?", "MEGGER,MIT525,SN-MIT-78421,FW-7.0.2"),
        ("INSU:VOLT 1000", "OK"),
        ("INSU:DUR 60", "OK"),
        ("INSU:STAR", "OK"),
        ("INSU:STAT?", "FAULT"),
    ])
    inst = MeggerMIT525(ch)
    await inst.connect()
    with pytest.raises(InstrumentError, match="FAULT"):
        await inst.execute({"test_voltage_v": 1000, "min_megohm": 100,
                            "duration_seconds": 60})


@pytest.mark.asyncio
async def test_disconnect_sends_stop():
    ch = RecordedSerialChannel([
        ("*IDN?", "MEGGER,MIT525,SN-MIT-78421,FW-7.0.2"),
        ("INSU:STOP", None),  # response timeout — disconnect tolerates
    ])
    inst = MeggerMIT525(ch)
    await inst.connect()
    await inst.disconnect()
    assert ("W", "INSU:STOP") in ch.history


@pytest.mark.asyncio
async def test_command_nak_raises():
    ch = RecordedSerialChannel([
        ("*IDN?", "MEGGER,MIT525,SN-MIT-78421,FW-7.0.2"),
        ("INSU:VOLT 1000", "ERROR-INVALID-RANGE"),
    ])
    inst = MeggerMIT525(ch)
    await inst.connect()
    with pytest.raises(InstrumentError, match="command nak"):
        await inst.execute({"test_voltage_v": 1000, "min_megohm": 100,
                            "duration_seconds": 60})
