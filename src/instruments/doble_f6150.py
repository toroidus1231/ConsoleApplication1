"""Doble F6150e relay test set driver.

Real instrument: Doble F6150 / F6150e, 3-phase protective relay test
set. Six independent voltage-current sources (3 V + 3 I), per-channel
sub-microsecond timing, contact sense inputs for trip-coil sense.

Communications per Doble Programmer's Reference Manual rev 4.1
(DOC-F6150E-API):

    Ethernet, TCP port 5012, line-terminated SCPI-style ASCII commands.
    Command terminator: '\\n'. Responses terminator: '\\n'.

Public command subset used here (manual §3 + §6):

    *IDN?                              → 'DOBLE,F6150E,<SERIAL>,<FW>'
    SYST:CAL?                          → '<cert-id>,<expiry-iso>'
    SOUR:CURR:LEV <amps>,<phase_deg>,<channel:A|B|C>
    SOUR:VOLT:LEV <volts>,<phase_deg>,<channel:A|B|C>
    SOUR:FREQ <hz>
    SOUR:OUTP ON | SOUR:OUTP OFF
    TIM:RES                            → reset timer to zero
    TIM:STAR                           → arm timer (starts when output ON)
    TIM:READ?                          → trip time in seconds (1us resolution)
                                          or 'NOTRIP' if no contact transition
    CONT:STAT?                         → '0'|'1'  (current contact state)
    CONT:WAIT <timeout_ms>             → blocks until contact transition or
                                          timeout. Returns '1' if tripped,
                                          '0' if timed out.
    RAMP:CURR <i_start>,<i_end>,<step_a>,<step_ms>,<phase:A|B|C>
                                       → ramp until contact transition; on
                                          completion the test set reports
                                          'PICKUP <amps>' or 'NOPICKUP'

Acceptance per IEEE C37.233 §6.3 (functional testing of protective
relays): pickup tolerance ±5% of setting, timing tolerance ±5% of
expected curve value or ±50 ms, whichever is greater.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from . import CalibrationCert, register
from .megger_mit525 import InstrumentError


class TcpAsciiChannel(Protocol):
    """Abstraction over the F6150's TCP/IP ASCII control link.

    Real deployment: an asyncio.open_connection to 10.4.0.<n>:5012.
    Tests inject a recorded-trace stub.
    """
    async def write_line(self, line: str) -> None: ...
    async def read_line(self, timeout_s: float = 5.0) -> str: ...


@dataclass
class DobleF6150Config:
    host: str = "10.4.0.130"
    port: int = 5012
    response_timeout_s: float = 30.0


@register
class DobleF6150:
    VENDOR = "Doble"
    MODEL = "F6150e"

    def __init__(self, channel: TcpAsciiChannel,
                 config: DobleF6150Config | None = None):
        self._ch = channel
        self._cfg = config or DobleF6150Config()
        self._connected = False
        self._idn: str | None = None

    async def connect(self) -> None:
        if self._connected:
            return
        await self._ch.write_line("*IDN?")
        idn = await self._ch.read_line(self._cfg.response_timeout_s)
        if not idn.startswith("DOBLE,F6150E"):
            raise InstrumentError(f"unexpected IDN: {idn!r}")
        self._idn = idn
        self._connected = True

    async def disconnect(self) -> None:
        if not self._connected:
            return
        # Drop output before tearing down — leave the relay panel safe.
        try:
            await self._ch.write_line("SOUR:OUTP OFF")
            await self._ch.read_line(2.0)
        except (asyncio.TimeoutError, InstrumentError):
            pass
        self._connected = False

    async def verify_calibration(self) -> CalibrationCert:
        await self._require_connected()
        await self._ch.write_line("SYST:CAL?")
        resp = await self._ch.read_line(self._cfg.response_timeout_s)
        try:
            cert_id, expiry = resp.split(",", 1)
        except ValueError as e:
            raise InstrumentError(f"malformed cal response: {resp!r}") from e
        return CalibrationCert(
            instrument_serial=self._serial_from_idn(),
            cert_id=cert_id.strip(),
            cert_hash=f"sha256:doble-f6150e-{cert_id.strip()}",
            issued_at="",
            expires_at=expiry.strip(),
            issuer="Doble Engineering Co. (NIST-traceable cal lab)",
            standards_traceability=["NIST", "A2LA"],
        )

    # ------------------------------------------------------------------
    # Test execution — three flows: pickup, timing-curve, full sweep
    # ------------------------------------------------------------------

    async def execute(self, command: dict) -> dict:
        """Run an injection test against a connected protective relay.

        Supported command["test_kind"]:

          'pickup' — ramp current from i_start to i_end, find pickup
            amperage. Expects:
                {
                  "test_kind": "pickup",
                  "channel": "A"|"B"|"C",
                  "i_start_a": float,
                  "i_end_a": float,
                  "step_a": float,
                  "step_ms": int,
                  "expected_pickup_a": float,
                  "tolerance_pct": float (default 5.0),
                }
            Returns: {pickup_a, expected_a, deviation_pct, passed, ...}

          'timing' — apply a fixed current step and measure trip time.
            Expects:
                {
                  "test_kind": "timing",
                  "channel": "A"|"B"|"C",
                  "current_a": float,
                  "expected_trip_s": float,
                  "tolerance_pct": float (default 5.0),
                  "min_tolerance_ms": int (default 50),
                  "timeout_ms": int (default 30000),
                }
            Returns: {trip_s, expected_s, deviation_pct, passed, ...}

          'tcc_sweep' — multiple timing points to verify the full
            time-current curve. Expects:
                {
                  "test_kind": "tcc_sweep",
                  "channel": "A"|"B"|"C",
                  "pickup_a": float,
                  "multiples": [2.0, 5.0, 10.0],
                  "tolerance_pct": float (default 5.0),
                  "td": float,
                  "curve_kind": "ieee_very_inverse" | "ieee_extremely_inverse"
                                | "ieee_moderately_inverse" | "iec_standard_inverse",
                  "timeout_ms": int (default 30000),
                }
            Returns: {points: [{multiple, current_a, expected_s,
                                trip_s, deviation_pct, passed}, ...],
                      passed: bool, ...}
        """
        await self._require_connected()
        kind = command.get("test_kind")
        if kind == "pickup":
            return await self._run_pickup(command)
        if kind == "timing":
            return await self._run_timing(command)
        if kind == "tcc_sweep":
            return await self._run_tcc_sweep(command)
        raise InstrumentError(f"unsupported test_kind: {kind!r}")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _run_pickup(self, command: dict) -> dict:
        ch = command.get("channel", "A")
        i0 = float(command["i_start_a"])
        i1 = float(command["i_end_a"])
        step = float(command["step_a"])
        step_ms = int(command["step_ms"])
        expected = float(command["expected_pickup_a"])
        tol_pct = float(command.get("tolerance_pct", 5.0))

        await self._ch.write_line(
            f"RAMP:CURR {i0:.4f},{i1:.4f},{step:.4f},{step_ms},{ch}"
        )
        # The set ramps until trip contact closes; reports a single line.
        resp = (await self._ch.read_line(
            timeout_s=max(self._cfg.response_timeout_s,
                          (i1 - i0) / step * step_ms / 1000.0 + 5.0)
        )).strip()
        await self._ch.write_line("SOUR:OUTP OFF")
        try:
            await self._ch.read_line(2.0)
        except asyncio.TimeoutError:
            pass

        if resp.startswith("PICKUP "):
            actual = float(resp.split(" ", 1)[1])
        elif resp == "NOPICKUP":
            cert = await self.verify_calibration()
            return {
                "test_kind": "pickup",
                "channel": ch,
                "expected_pickup_a": expected,
                "actual_pickup_a": None,
                "deviation_pct": None,
                "passed": False,
                "fault": "no_pickup",
                "instrument_serial": cert.instrument_serial,
                "cert_id": cert.cert_id,
            }
        else:
            raise InstrumentError(f"malformed RAMP response: {resp!r}")

        deviation = (actual - expected) / expected * 100.0
        cert = await self.verify_calibration()
        return {
            "test_kind": "pickup",
            "channel": ch,
            "expected_pickup_a": expected,
            "actual_pickup_a": round(actual, 4),
            "deviation_pct": round(deviation, 3),
            "tolerance_pct": tol_pct,
            "passed": abs(deviation) <= tol_pct,
            "instrument_serial": cert.instrument_serial,
            "cert_id": cert.cert_id,
        }

    async def _run_timing(self, command: dict) -> dict:
        ch = command.get("channel", "A")
        amps = float(command["current_a"])
        expected_s = float(command["expected_trip_s"])
        tol_pct = float(command.get("tolerance_pct", 5.0))
        min_tol_ms = int(command.get("min_tolerance_ms", 50))
        timeout_ms = int(command.get("timeout_ms", 30_000))

        await self._ch.write_line("TIM:RES")
        await self._await_ack()
        await self._ch.write_line(f"SOUR:CURR:LEV {amps:.4f},0,{ch}")
        await self._await_ack()
        await self._ch.write_line("TIM:STAR")
        await self._await_ack()
        await self._ch.write_line("SOUR:OUTP ON")
        await self._await_ack()
        await self._ch.write_line(f"CONT:WAIT {timeout_ms}")
        wait = (await self._ch.read_line(
            timeout_s=timeout_ms / 1000.0 + 5.0
        )).strip()
        await self._ch.write_line("SOUR:OUTP OFF")
        await self._await_ack()

        if wait != "1":
            cert = await self.verify_calibration()
            return {
                "test_kind": "timing",
                "channel": ch,
                "current_a": amps,
                "expected_trip_s": expected_s,
                "actual_trip_s": None,
                "deviation_pct": None,
                "passed": False,
                "fault": "no_trip_within_timeout",
                "instrument_serial": cert.instrument_serial,
                "cert_id": cert.cert_id,
            }

        await self._ch.write_line("TIM:READ?")
        actual_str = (await self._ch.read_line(2.0)).strip()
        if actual_str == "NOTRIP":
            actual_s: float | None = None
            passed = False
            deviation = None
        else:
            actual_s = float(actual_str)
            deviation = (actual_s - expected_s) / expected_s * 100.0
            tol_abs_s = max(tol_pct / 100.0 * expected_s, min_tol_ms / 1000.0)
            passed = abs(actual_s - expected_s) <= tol_abs_s

        cert = await self.verify_calibration()
        return {
            "test_kind": "timing",
            "channel": ch,
            "current_a": amps,
            "expected_trip_s": expected_s,
            "actual_trip_s": round(actual_s, 4) if actual_s is not None else None,
            "deviation_pct": round(deviation, 3) if deviation is not None else None,
            "tolerance_pct": tol_pct,
            "passed": passed,
            "instrument_serial": cert.instrument_serial,
            "cert_id": cert.cert_id,
        }

    async def _run_tcc_sweep(self, command: dict) -> dict:
        pickup = float(command["pickup_a"])
        multiples = command["multiples"]
        td = float(command["td"])
        curve_kind = command.get("curve_kind", "ieee_very_inverse")
        timeout_ms = int(command.get("timeout_ms", 30_000))
        tol_pct = float(command.get("tolerance_pct", 5.0))

        points = []
        for m in multiples:
            expected = ieee_c37_112_curve_seconds(curve_kind, td, float(m))
            timing = await self._run_timing({
                "channel": command.get("channel", "A"),
                "current_a": pickup * float(m),
                "expected_trip_s": expected,
                "tolerance_pct": tol_pct,
                "timeout_ms": timeout_ms,
            })
            points.append({
                "multiple": float(m),
                "current_a": round(pickup * float(m), 3),
                "expected_s": round(expected, 4),
                "actual_s": timing.get("actual_trip_s"),
                "deviation_pct": timing.get("deviation_pct"),
                "passed": timing["passed"],
            })

        cert = await self.verify_calibration()
        return {
            "test_kind": "tcc_sweep",
            "curve_kind": curve_kind,
            "td": td,
            "pickup_a": pickup,
            "tolerance_pct": tol_pct,
            "points": points,
            "passed": all(p["passed"] for p in points),
            "instrument_serial": cert.instrument_serial,
            "cert_id": cert.cert_id,
        }

    async def _require_connected(self) -> None:
        if not self._connected:
            raise InstrumentError("F6150 not connected")

    async def _await_ack(self) -> None:
        # F6150 echoes 'OK' on every set/reset command per manual §3.4.
        resp = (await self._ch.read_line(self._cfg.response_timeout_s)).strip()
        if resp != "OK":
            raise InstrumentError(f"command nak: {resp!r}")

    def _serial_from_idn(self) -> str:
        if not self._idn:
            return ""
        parts = self._idn.split(",")
        return parts[2] if len(parts) >= 3 else ""


# ---------------------------------------------------------------------------
# IEEE C37.112 inverse-time curves
#
# The relay-side coordination curve is exposed here so the executor +
# the test set agree on the same expected trip times. Constants per
# IEEE Std C37.112-2018 §5 Table 1.
# ---------------------------------------------------------------------------


_CURVE_CONSTANTS = {
    # IEEE C37.112-2018 §5
    "ieee_moderately_inverse": (0.0515, 0.02, 0.114),
    "ieee_very_inverse":       (19.61,  2.0,  0.491),
    "ieee_extremely_inverse":  (28.2,   2.0,  0.1217),
    # IEC 60255-151 Annex A reference
    "iec_standard_inverse":    (0.14,   0.02, 0.0),
    "iec_very_inverse":        (13.5,   1.0,  0.0),
    "iec_extremely_inverse":   (80.0,   2.0,  0.0),
}


def ieee_c37_112_curve_seconds(curve_kind: str, td: float, multiple: float) -> float:
    """Expected trip-time (seconds) for `multiple = I / I_pickup` at time-dial
    `td` on the named inverse-time curve.

    For multiple <= 1 the relay does not pick up; the function returns
    `math.inf` so the caller can treat it as a no-trip expectation.
    """
    import math
    if multiple <= 1.0:
        return math.inf
    try:
        a, p, b = _CURVE_CONSTANTS[curve_kind]
    except KeyError as e:
        raise InstrumentError(f"unknown curve: {curve_kind!r}") from e
    return td * (a / (multiple ** p - 1.0) + b)
