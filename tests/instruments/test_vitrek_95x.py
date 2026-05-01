"""Recorded-trace tests for the Vitrek 95X driver."""

from __future__ import annotations

from collections import deque
import pytest

from src.instruments.vitrek_95x import Vitrek95X
from src.instruments.megger_mit525 import InstrumentError


class RecordedSerialChannel:
    def __init__(self, script):
        self._script = deque(script)

    async def write_line(self, line: str) -> None:
        if not self._script:
            raise AssertionError(f"unexpected write: {line!r}")
        expected, _resp = self._script[0]
        if line.strip() != expected.strip():
            raise AssertionError(f"expected {expected!r}, got {line!r}")

    async def read_line(self, timeout_s: float = 5.0) -> str:
        return self._script.popleft()[1]


@pytest.mark.asyncio
async def test_connect_rejects_bad_idn():
    inst = Vitrek95X(RecordedSerialChannel([("*IDN?", "WRONG,MODEL,SN,FW")]))
    with pytest.raises(InstrumentError, match="unexpected IDN"):
        await inst.connect()


@pytest.mark.asyncio
async def test_execute_normal_pass():
    # 11.04 kV target, 60 s ramp + 600 s dwell, peak 0.082 mA → PASS
    trace_body = ";".join(
        f"{i*60:.1f},{min(11.04, 11.04*i/1):.3f},0.08"
        for i in range(0, 12)  # arbitrary 12-sample trace
    )
    script = [
        ("*IDN?", "VITREK,95X,SN-VTK-95X-12345,FW-5.0"),
        ("HIPot:DC:VOLTage 11.04", "OK"),
        ("HIPot:DC:RAMP 60", "OK"),
        ("HIPot:DC:DWELl 600", "OK"),
        ("HIPot:DC:LIMit:LEAKage 0.5", "OK"),
        ("HIPot:STARt", "OK"),
        ("HIPot:STATus?", "PASS"),
        ("HIPot:RESult?", "0.082,11.04,660.0"),
        ("HIPot:TRACe?", f"12;{trace_body}"),
        ("SYSTem:CALibration?", "VTK-2026-Q2-B112,2026-06-30"),
    ]
    inst = Vitrek95X(RecordedSerialChannel(script))
    await inst.connect()
    out = await inst.execute({"target_kv": 11.04, "ramp_seconds": 60,
                              "hold_seconds": 600, "leakage_trip_ma": 0.5})
    assert out["peak_leakage_ma"] == 0.082
    assert out["tripped"] is False
    assert len(out["trace"]) == 12
    assert out["instrument_serial"] == "SN-VTK-95X-12345"


@pytest.mark.asyncio
async def test_execute_trip_marks_tripped():
    script = [
        ("*IDN?", "VITREK,95X,SN-VTK-95X-12345,FW-5.0"),
        ("HIPot:DC:VOLTage 2.5", "OK"),
        ("HIPot:DC:RAMP 60", "OK"),
        ("HIPot:DC:DWELl 60", "OK"),
        ("HIPot:DC:LIMit:LEAKage 1.0", "OK"),
        ("HIPot:STARt", "OK"),
        ("HIPot:STATus?", "TRIP"),
        ("HIPot:RESult?", "1.42,2.5,42.0"),
        ("HIPot:TRACe?", "1;42.0,2.5,1.42"),
        ("SYSTem:CALibration?", "VTK-2026-Q2-B112,2026-06-30"),
    ]
    inst = Vitrek95X(RecordedSerialChannel(script))
    await inst.connect()
    out = await inst.execute({"target_kv": 2.5, "ramp_seconds": 60,
                              "hold_seconds": 60, "leakage_trip_ma": 1.0})
    assert out["tripped"] is True


@pytest.mark.asyncio
async def test_execute_voltage_out_of_range():
    inst = Vitrek95X(RecordedSerialChannel([
        ("*IDN?", "VITREK,95X,SN-VTK-95X-12345,FW-5.0"),
    ]))
    await inst.connect()
    with pytest.raises(InstrumentError, match="out of range"):
        await inst.execute({"target_kv": 99.0, "ramp_seconds": 60,
                            "hold_seconds": 60, "leakage_trip_ma": 1.0})


@pytest.mark.asyncio
async def test_trace_length_mismatch_raises():
    script = [
        ("*IDN?", "VITREK,95X,SN-VTK-95X-12345,FW-5.0"),
        ("HIPot:DC:VOLTage 2.5", "OK"),
        ("HIPot:DC:RAMP 60", "OK"),
        ("HIPot:DC:DWELl 60", "OK"),
        ("HIPot:DC:LIMit:LEAKage 1.0", "OK"),
        ("HIPot:STARt", "OK"),
        ("HIPot:STATus?", "PASS"),
        ("HIPot:RESult?", "0.0,2.5,60.0"),
        ("HIPot:TRACe?", "5;0,0,0;1,1,0.1"),  # claims 5 samples, only sends 2
        ("SYSTem:CALibration?", "VTK-2026-Q2-B112,2026-06-30"),
    ]
    inst = Vitrek95X(RecordedSerialChannel(script))
    await inst.connect()
    with pytest.raises(InstrumentError, match="trace length mismatch"):
        await inst.execute({"target_kv": 2.5, "ramp_seconds": 60,
                            "hold_seconds": 60, "leakage_trip_ma": 1.0})
