"""Recorded-trace tests for the Megger TM1800 driver.

Verifies every command in the TM1800 User Guide rev 6.0 §8.4 against
a scripted serial channel. Catches protocol drift between the code
and the manual.
"""

from __future__ import annotations

import asyncio
from collections import deque
import pytest

from src.instruments.megger_tm1800 import MeggerTM1800, TM1800Config
from src.instruments.megger_mit525 import InstrumentError


class RecordedSerialChannel:
    def __init__(self, script: list[tuple[str, str]]):
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


# ---------------------------------------------------------------------------
# Identification + cal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_reads_idn():
    ch = RecordedSerialChannel([
        ("*IDN?", "MEGGER,TM1800,SN-TM1800-44219,FW-6.0.4"),
    ])
    inst = MeggerTM1800(ch)
    await inst.connect()
    assert inst._idn == "MEGGER,TM1800,SN-TM1800-44219,FW-6.0.4"


@pytest.mark.asyncio
async def test_connect_rejects_bad_idn():
    ch = RecordedSerialChannel([
        ("*IDN?", "OTHER-VENDOR,WRONG-MODEL,1234,1.0"),
    ])
    inst = MeggerTM1800(ch)
    with pytest.raises(InstrumentError, match="unexpected IDN"):
        await inst.connect()


@pytest.mark.asyncio
async def test_verify_calibration_returns_cert():
    ch = RecordedSerialChannel([
        ("*IDN?", "MEGGER,TM1800,SN-TM1800-44219,FW-6.0.4"),
        ("SYST:CAL?", "CAL-2026-TM-401,2026-08-15"),
    ])
    inst = MeggerTM1800(ch)
    await inst.connect()
    cert = await inst.verify_calibration()
    assert cert.instrument_serial == "SN-TM1800-44219"
    assert cert.cert_id == "CAL-2026-TM-401"
    assert cert.expires_at == "2026-08-15"
    assert "NIST" in cert.standards_traceability


# ---------------------------------------------------------------------------
# Open-only timing
# ---------------------------------------------------------------------------


def _open_only_script() -> list[tuple[str, str]]:
    return [
        ("*IDN?", "MEGGER,TM1800,SN-TM1800-44219,FW-6.0.4"),
        ("TIM:MODE OPEN", "OK"),
        ("TIM:OPER", "STARTED"),
        ("TIM:STAT?", "COMPLETE"),
        ("READ:TIM:A?", "OPEN 33.4"),
        ("READ:TIM:B?", "OPEN 34.1"),
        ("READ:TIM:C?", "OPEN 33.8"),
        ("READ:SIMUL:OPEN?", "0.7"),
        ("READ:STROKE:PEAK?", "102.4"),
        ("READ:STROKE:OVER?", "3.1"),
        ("READ:STROKE:REBOUND?", "1.2"),
        ("READ:SPRING:CHARGE?", "9.4"),
        ("READ:COIL:TRIP:PEAK?", "7.8"),
        ("READ:COIL:CLOSE:PEAK?", "12.4"),
        ("SYST:CAL?", "CAL-2026-TM-401,2026-08-15"),
    ]


@pytest.mark.asyncio
async def test_open_timing_full_capture():
    ch = RecordedSerialChannel(_open_only_script())
    inst = MeggerTM1800(ch)
    await inst.connect()
    out = await inst.execute({"mode": "open"})
    assert out["mode"] == "open"
    assert out["per_pole_ms"] == {"A": 33.4, "B": 34.1, "C": 33.8}
    assert out["max_simultaneity_ms"] == {"open": 0.7}
    assert out["stroke"]["peak_mm"] == 102.4
    assert out["spring_charge_s"] == 9.4
    assert out["coil_peak"]["trip_a"] == 7.8
    assert out["coil_peak"]["close_a"] == 12.4
    assert out["instrument_serial"] == "SN-TM1800-44219"
    assert out["cert_id"] == "CAL-2026-TM-401"


@pytest.mark.asyncio
async def test_open_timing_skips_optional_blocks():
    ch = RecordedSerialChannel([
        ("*IDN?", "MEGGER,TM1800,SN-TM1800-44219,FW-6.0.4"),
        ("TIM:MODE OPEN", "OK"),
        ("TIM:OPER", "STARTED"),
        ("TIM:STAT?", "COMPLETE"),
        ("READ:TIM:A?", "OPEN 33.4"),
        ("READ:TIM:B?", "OPEN 34.1"),
        ("READ:TIM:C?", "OPEN 33.8"),
        ("READ:SIMUL:OPEN?", "0.7"),
        ("SYST:CAL?", "CAL-2026-TM-401,2026-08-15"),
    ])
    inst = MeggerTM1800(ch)
    await inst.connect()
    out = await inst.execute({
        "mode": "open",
        "include_motion": False,
        "include_spring": False,
        "include_coils": False,
    })
    assert "stroke" not in out
    assert "spring_charge_s" not in out
    assert "coil_peak" not in out


# ---------------------------------------------------------------------------
# Open-Close (combined sequence)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_close_combined_sequence():
    ch = RecordedSerialChannel([
        ("*IDN?", "MEGGER,TM1800,SN-TM1800-44219,FW-6.0.4"),
        ("TIM:MODE OPEN-CLOSE", "OK"),
        ("TIM:OPER", "STARTED"),
        ("TIM:STAT?", "RUNNING"),
        ("TIM:STAT?", "COMPLETE"),
        ("READ:TIM:A?", "OPEN 33.4,CLOSE 51.2"),
        ("READ:TIM:B?", "OPEN 34.1,CLOSE 51.6"),
        ("READ:TIM:C?", "OPEN 33.8,CLOSE 51.4"),
        ("READ:SIMUL:OPEN?", "0.7"),
        ("READ:SIMUL:CLOSE?", "0.4"),
        ("READ:STROKE:PEAK?", "102.4"),
        ("READ:STROKE:OVER?", "3.1"),
        ("READ:STROKE:REBOUND?", "1.2"),
        ("READ:SPRING:CHARGE?", "9.4"),
        ("READ:COIL:TRIP:PEAK?", "7.8"),
        ("READ:COIL:CLOSE:PEAK?", "12.4"),
        ("SYST:CAL?", "CAL-2026-TM-401,2026-08-15"),
    ])
    inst = MeggerTM1800(ch)
    await inst.connect()
    out = await inst.execute({"mode": "open_close"})
    assert out["mode"] == "open_close"
    assert out["per_pole_ms"]["A"] == {"open_ms": 33.4, "close_ms": 51.2}
    assert out["per_pole_ms"]["B"] == {"open_ms": 34.1, "close_ms": 51.6}
    assert out["max_simultaneity_ms"] == {"open": 0.7, "close": 0.4}


# ---------------------------------------------------------------------------
# Status timeout / fault
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fault_status_raises():
    ch = RecordedSerialChannel([
        ("*IDN?", "MEGGER,TM1800,SN-TM1800-44219,FW-6.0.4"),
        ("TIM:MODE OPEN", "OK"),
        ("TIM:OPER", "STARTED"),
        ("TIM:STAT?", "FAULT"),
    ])
    inst = MeggerTM1800(ch)
    await inst.connect()
    with pytest.raises(InstrumentError, match="FAULT"):
        await inst.execute({"mode": "open"})


@pytest.mark.asyncio
async def test_unsupported_mode_rejected():
    ch = RecordedSerialChannel([
        ("*IDN?", "MEGGER,TM1800,SN-TM1800-44219,FW-6.0.4"),
    ])
    inst = MeggerTM1800(ch)
    await inst.connect()
    with pytest.raises(InstrumentError, match="unsupported mode"):
        await inst.execute({"mode": "make_coffee"})


@pytest.mark.asyncio
async def test_oper_nak_raises():
    ch = RecordedSerialChannel([
        ("*IDN?", "MEGGER,TM1800,SN-TM1800-44219,FW-6.0.4"),
        ("TIM:MODE OPEN", "OK"),
        ("TIM:OPER", "BUSY"),
    ])
    inst = MeggerTM1800(ch)
    await inst.connect()
    with pytest.raises(InstrumentError, match="TIM:OPER nak"):
        await inst.execute({"mode": "open"})


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registered_in_driver_table():
    from src.instruments import _eager_register, get_driver
    _eager_register()
    cls = get_driver("Megger", "TM1800")
    assert cls is MeggerTM1800
