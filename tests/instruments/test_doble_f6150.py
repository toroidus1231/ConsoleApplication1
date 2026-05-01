"""Recorded-trace tests for the Doble F6150e driver.

The driver talks SCPI-style ASCII over a TCP/IP channel. Tests inject
a RecordedTcpChannel preloaded with the request/response sequence we
expect from the F6150 per Doble's Programmer's Reference Manual rev
4.1. If the driver issues a command not in the script, the channel
raises — that catches protocol drift between code and manual.
"""

from __future__ import annotations

import asyncio
import math
from collections import deque
import pytest

from src.instruments.doble_f6150 import (
    DobleF6150, DobleF6150Config, ieee_c37_112_curve_seconds,
)
from src.instruments.megger_mit525 import InstrumentError


class RecordedTcpChannel:
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
    ch = RecordedTcpChannel([
        ("*IDN?", "DOBLE,F6150E,SN-DBL-F6150-22441,FW-4.1.7"),
    ])
    inst = DobleF6150(ch)
    await inst.connect()
    assert inst._idn == "DOBLE,F6150E,SN-DBL-F6150-22441,FW-4.1.7"


@pytest.mark.asyncio
async def test_connect_rejects_bad_idn():
    ch = RecordedTcpChannel([
        ("*IDN?", "OTHER-VENDOR,WRONG-MODEL,1234,1.0"),
    ])
    inst = DobleF6150(ch)
    with pytest.raises(InstrumentError, match="unexpected IDN"):
        await inst.connect()


@pytest.mark.asyncio
async def test_verify_calibration_returns_cert():
    ch = RecordedTcpChannel([
        ("*IDN?", "DOBLE,F6150E,SN-DBL-F6150-22441,FW-4.1.7"),
        ("SYST:CAL?", "CAL-2026-DBL-887,2026-09-30"),
    ])
    inst = DobleF6150(ch)
    await inst.connect()
    cert = await inst.verify_calibration()
    assert cert.instrument_serial == "SN-DBL-F6150-22441"
    assert cert.cert_id == "CAL-2026-DBL-887"
    assert cert.expires_at == "2026-09-30"
    assert cert.cert_hash.startswith("sha256:doble-f6150e-")
    assert "NIST" in cert.standards_traceability


# ---------------------------------------------------------------------------
# Pickup ramp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pickup_within_tolerance():
    ch = RecordedTcpChannel([
        ("*IDN?", "DOBLE,F6150E,SN-DBL-F6150-22441,FW-4.1.7"),
        # ramp from 0.5x to 1.5x of expected 5.0 A pickup
        ("RAMP:CURR 2.5000,7.5000,0.0500,200,A", "PICKUP 4.92"),
        ("SOUR:OUTP OFF", "OK"),
        ("SYST:CAL?", "CAL-2026-DBL-887,2026-09-30"),
    ])
    inst = DobleF6150(ch)
    await inst.connect()
    out = await inst.execute({
        "test_kind": "pickup",
        "channel": "A",
        "i_start_a": 2.5,
        "i_end_a": 7.5,
        "step_a": 0.05,
        "step_ms": 200,
        "expected_pickup_a": 5.0,
        "tolerance_pct": 5.0,
    })
    assert out["actual_pickup_a"] == 4.92
    assert out["expected_pickup_a"] == 5.0
    assert abs(out["deviation_pct"] - (-1.6)) < 0.01
    assert out["passed"] is True


@pytest.mark.asyncio
async def test_pickup_out_of_tolerance_fails():
    ch = RecordedTcpChannel([
        ("*IDN?", "DOBLE,F6150E,SN-DBL-F6150-22441,FW-4.1.7"),
        ("RAMP:CURR 2.5000,7.5000,0.0500,200,A", "PICKUP 5.40"),
        ("SOUR:OUTP OFF", "OK"),
        ("SYST:CAL?", "CAL-2026-DBL-887,2026-09-30"),
    ])
    inst = DobleF6150(ch)
    await inst.connect()
    out = await inst.execute({
        "test_kind": "pickup",
        "channel": "A",
        "i_start_a": 2.5,
        "i_end_a": 7.5,
        "step_a": 0.05,
        "step_ms": 200,
        "expected_pickup_a": 5.0,
        "tolerance_pct": 5.0,
    })
    # 5.40 / 5.00 → 8% deviation, exceeds 5% tolerance
    assert out["actual_pickup_a"] == 5.40
    assert out["passed"] is False


@pytest.mark.asyncio
async def test_pickup_no_pickup_reports_fault():
    ch = RecordedTcpChannel([
        ("*IDN?", "DOBLE,F6150E,SN-DBL-F6150-22441,FW-4.1.7"),
        ("RAMP:CURR 2.5000,7.5000,0.0500,200,A", "NOPICKUP"),
        ("SOUR:OUTP OFF", "OK"),
        ("SYST:CAL?", "CAL-2026-DBL-887,2026-09-30"),
    ])
    inst = DobleF6150(ch)
    await inst.connect()
    out = await inst.execute({
        "test_kind": "pickup",
        "channel": "A",
        "i_start_a": 2.5,
        "i_end_a": 7.5,
        "step_a": 0.05,
        "step_ms": 200,
        "expected_pickup_a": 5.0,
    })
    assert out["passed"] is False
    assert out["fault"] == "no_pickup"


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timing_within_tolerance():
    ch = RecordedTcpChannel([
        ("*IDN?", "DOBLE,F6150E,SN-DBL-F6150-22441,FW-4.1.7"),
        ("TIM:RES", "OK"),
        ("SOUR:CURR:LEV 25.0000,0,A", "OK"),
        ("TIM:STAR", "OK"),
        ("SOUR:OUTP ON", "OK"),
        ("CONT:WAIT 30000", "1"),
        ("SOUR:OUTP OFF", "OK"),
        ("TIM:READ?", "0.535"),
        ("SYST:CAL?", "CAL-2026-DBL-887,2026-09-30"),
    ])
    inst = DobleF6150(ch)
    await inst.connect()
    out = await inst.execute({
        "test_kind": "timing",
        "channel": "A",
        "current_a": 25.0,
        "expected_trip_s": 0.544,  # IEEE C37.112 very-inverse, M=5, TD=2.5
    })
    assert out["actual_trip_s"] == 0.535
    assert out["expected_trip_s"] == 0.544
    assert out["passed"] is True


@pytest.mark.asyncio
async def test_timing_no_trip_within_timeout():
    ch = RecordedTcpChannel([
        ("*IDN?", "DOBLE,F6150E,SN-DBL-F6150-22441,FW-4.1.7"),
        ("TIM:RES", "OK"),
        ("SOUR:CURR:LEV 25.0000,0,A", "OK"),
        ("TIM:STAR", "OK"),
        ("SOUR:OUTP ON", "OK"),
        ("CONT:WAIT 30000", "0"),
        ("SOUR:OUTP OFF", "OK"),
        ("SYST:CAL?", "CAL-2026-DBL-887,2026-09-30"),
    ])
    inst = DobleF6150(ch)
    await inst.connect()
    out = await inst.execute({
        "test_kind": "timing",
        "channel": "A",
        "current_a": 25.0,
        "expected_trip_s": 0.544,
    })
    assert out["passed"] is False
    assert out["fault"] == "no_trip_within_timeout"


# ---------------------------------------------------------------------------
# Full TCC sweep — verifies expected curves match IEEE C37.112
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tcc_sweep_three_points():
    # Pickup 5 A, TD=2.5, ieee_very_inverse:
    # M=2  → 19.61/(4-1)*2.5 + 0.491*2.5 ≈ 17.563
    # M=5  → 19.61/(25-1)*2.5 + 0.491*2.5 ≈ 3.270
    # M=10 → 19.61/(100-1)*2.5 + 0.491*2.5 ≈ 1.722

    expected_2  = ieee_c37_112_curve_seconds("ieee_very_inverse", 2.5, 2.0)
    expected_5  = ieee_c37_112_curve_seconds("ieee_very_inverse", 2.5, 5.0)
    expected_10 = ieee_c37_112_curve_seconds("ieee_very_inverse", 2.5, 10.0)

    actual_2,  actual_5,  actual_10  = expected_2 * 1.01, expected_5 * 0.99, expected_10 * 1.02

    ch = RecordedTcpChannel([
        ("*IDN?", "DOBLE,F6150E,SN-DBL-F6150-22441,FW-4.1.7"),
        # M=2 (10 A)
        ("TIM:RES", "OK"),
        ("SOUR:CURR:LEV 10.0000,0,A", "OK"),
        ("TIM:STAR", "OK"),
        ("SOUR:OUTP ON", "OK"),
        ("CONT:WAIT 30000", "1"),
        ("SOUR:OUTP OFF", "OK"),
        ("TIM:READ?", f"{actual_2:.4f}"),
        ("SYST:CAL?", "CAL-2026-DBL-887,2026-09-30"),
        # M=5 (25 A)
        ("TIM:RES", "OK"),
        ("SOUR:CURR:LEV 25.0000,0,A", "OK"),
        ("TIM:STAR", "OK"),
        ("SOUR:OUTP ON", "OK"),
        ("CONT:WAIT 30000", "1"),
        ("SOUR:OUTP OFF", "OK"),
        ("TIM:READ?", f"{actual_5:.4f}"),
        ("SYST:CAL?", "CAL-2026-DBL-887,2026-09-30"),
        # M=10 (50 A)
        ("TIM:RES", "OK"),
        ("SOUR:CURR:LEV 50.0000,0,A", "OK"),
        ("TIM:STAR", "OK"),
        ("SOUR:OUTP ON", "OK"),
        ("CONT:WAIT 30000", "1"),
        ("SOUR:OUTP OFF", "OK"),
        ("TIM:READ?", f"{actual_10:.4f}"),
        ("SYST:CAL?", "CAL-2026-DBL-887,2026-09-30"),
        # final cert lookup
        ("SYST:CAL?", "CAL-2026-DBL-887,2026-09-30"),
    ])
    inst = DobleF6150(ch)
    await inst.connect()
    out = await inst.execute({
        "test_kind": "tcc_sweep",
        "channel": "A",
        "pickup_a": 5.0,
        "multiples": [2.0, 5.0, 10.0],
        "td": 2.5,
        "curve_kind": "ieee_very_inverse",
    })
    assert out["passed"] is True
    assert len(out["points"]) == 3
    assert out["points"][0]["multiple"] == 2.0
    assert out["points"][0]["current_a"] == 10.0
    assert out["points"][1]["current_a"] == 25.0
    assert out["points"][2]["current_a"] == 50.0
    assert all(p["passed"] for p in out["points"])


# ---------------------------------------------------------------------------
# Curve math itself
# ---------------------------------------------------------------------------


def test_ieee_very_inverse_curve_at_known_points():
    # IEEE C37.112-2018 §5 reference: TD=1, very-inverse,
    # M=2 → 19.61/(4-1) + 0.491 = 7.027 s
    t = ieee_c37_112_curve_seconds("ieee_very_inverse", 1.0, 2.0)
    assert abs(t - 7.027) < 0.005


def test_ieee_extremely_inverse_curve_at_known_points():
    # M=5, TD=1 → 28.2/(25-1) + 0.1217 = 1.297 s
    t = ieee_c37_112_curve_seconds("ieee_extremely_inverse", 1.0, 5.0)
    assert abs(t - 1.297) < 0.005


def test_curve_scales_linearly_with_td():
    a = ieee_c37_112_curve_seconds("ieee_very_inverse", 1.0, 5.0)
    b = ieee_c37_112_curve_seconds("ieee_very_inverse", 2.5, 5.0)
    assert abs(b - 2.5 * a) < 1e-6


def test_curve_returns_inf_below_pickup():
    assert math.isinf(ieee_c37_112_curve_seconds("ieee_very_inverse", 1.0, 1.0))
    assert math.isinf(ieee_c37_112_curve_seconds("ieee_very_inverse", 1.0, 0.5))


def test_unknown_curve_raises():
    with pytest.raises(InstrumentError, match="unknown curve"):
        ieee_c37_112_curve_seconds("madeup_curve", 1.0, 5.0)


# ---------------------------------------------------------------------------
# Driver lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_drops_output():
    ch = RecordedTcpChannel([
        ("*IDN?", "DOBLE,F6150E,SN-DBL-F6150-22441,FW-4.1.7"),
        ("SOUR:OUTP OFF", "OK"),
    ])
    inst = DobleF6150(ch)
    await inst.connect()
    await inst.disconnect()
    assert inst._connected is False


@pytest.mark.asyncio
async def test_execute_unknown_test_kind_raises():
    ch = RecordedTcpChannel([
        ("*IDN?", "DOBLE,F6150E,SN-DBL-F6150-22441,FW-4.1.7"),
    ])
    inst = DobleF6150(ch)
    await inst.connect()
    with pytest.raises(InstrumentError, match="unsupported test_kind"):
        await inst.execute({"test_kind": "make_coffee"})


@pytest.mark.asyncio
async def test_registered_in_driver_table():
    from src.instruments import _eager_register, get_driver
    _eager_register()
    cls = get_driver("Doble", "F6150e")
    assert cls is DobleF6150
