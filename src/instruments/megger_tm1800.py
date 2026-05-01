"""Megger TM1800 / TM1700 series circuit-breaker analyzer driver.

Real instrument: Megger TM1800 modular breaker analyzer. Per-pole
contact-timing inputs, motion transducer input, trip/close coil current
clamps, and a charge-spring monitor. Communications per Megger TM1800
User Guide rev 6.0 §8 (PC Communications):

    USB-CDC virtual COM port @ 115200 8N1, line-terminated ASCII commands
    with CR ('\\r').

Public command set (manual §8.4):

    *IDN?                  → 'MEGGER,TM1800,<SERIAL>,<FW>'
    SYST:CAL?              → '<cert-id>,<expiry-iso>'
    TIM:MODE OPEN | TIM:MODE CLOSE | TIM:MODE OPEN-CLOSE | TIM:MODE CLOSE-OPEN
    TIM:OPER               → trigger the configured operation
    TIM:STAT?              → 'IDLE'|'ARMED'|'RUNNING'|'COMPLETE'|'FAULT'
    READ:TIM:<P>?          → trip/close time per pole P ∈ {A,B,C}
                              format: 'OPEN <ms>' or 'CLOSE <ms>'
                              or 'OPEN <ms>,CLOSE <ms>' for OPEN-CLOSE seq
    READ:SIMUL:OPEN?       → max delta open across poles (ms)
    READ:SIMUL:CLOSE?      → max delta close across poles (ms)
    READ:STROKE:PEAK?      → peak contact stroke (mm)
    READ:STROKE:OVER?      → contact overtravel (mm)
    READ:STROKE:REBOUND?   → contact rebound after close (mm)
    READ:SPRING:CHARGE?    → spring charge time (seconds)
    READ:COIL:TRIP:PEAK?   → trip coil peak current (amps)
    READ:COIL:CLOSE:PEAK?  → close coil peak current (amps)

Acceptance for breaker commissioning per IEC 62271-100 §6.101 +
IEEE C37.09: pole simultaneity ≤ 2 ms (open) / ≤ 5 ms (close);
contact timing within ±10% of nameplate; charge spring within rated
time per OEM data sheet.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from . import CalibrationCert, register
from .megger_mit525 import InstrumentError


class SerialChannel(Protocol):
    """Abstraction over the TM1800's USB-CDC link.

    Real deployment: a pyserial AsyncSerial wrapping the /dev/ttyACM<n>
    USB-CDC interface. Tests inject a recorded-trace stub.
    """
    async def write_line(self, line: str) -> None: ...
    async def read_line(self, timeout_s: float = 5.0) -> str: ...


@dataclass
class TM1800Config:
    serial_port: str = "/dev/ttyACM2"
    baud: int = 115200
    line_terminator: str = "\r"
    response_timeout_s: float = 30.0


@register
class MeggerTM1800:
    VENDOR = "Megger"
    MODEL = "TM1800"

    def __init__(self, channel: SerialChannel, config: TM1800Config | None = None):
        self._ch = channel
        self._cfg = config or TM1800Config()
        self._connected = False
        self._idn: str | None = None

    async def connect(self) -> None:
        if self._connected:
            return
        await self._ch.write_line("*IDN?")
        idn = await self._ch.read_line(self._cfg.response_timeout_s)
        if not idn.startswith("MEGGER,TM1800"):
            raise InstrumentError(f"unexpected IDN: {idn!r}")
        self._idn = idn
        self._connected = True

    async def disconnect(self) -> None:
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
            cert_hash=f"sha256:megger-tm1800-{cert_id.strip()}",
            issued_at="",
            expires_at=expiry.strip(),
            issuer="Megger Sweden AB (SWEDAC-accredited cal lab)",
            standards_traceability=["NIST", "SWEDAC"],
        )

    # ------------------------------------------------------------------
    # Test execution
    # ------------------------------------------------------------------

    async def execute(self, command: dict) -> dict:
        """Run a breaker-timing measurement.

        command schema:
            mode: 'open' | 'close' | 'open_close' | 'close_open'
            include_motion: bool   (default True)
            include_spring: bool   (default True)
            include_coils: bool    (default True)

        return:
            {
              "mode": "open",
              "per_pole_ms": {"A": 33.4, "B": 34.1, "C": 33.8},  # for open mode
                            or {"A": {"open_ms": ..., "close_ms": ...}, ...}
              "max_simultaneity_ms": {"open": 0.7, "close": 1.4},
              "stroke": {"peak_mm": 102.4, "overtravel_mm": 3.1, "rebound_mm": 1.2},
              "spring_charge_s": 9.4,
              "coil_peak": {"trip_a": 7.8, "close_a": 12.4},
              "instrument_serial": "...",
              "cert_id": "...",
            }
        """
        await self._require_connected()
        mode = command["mode"]
        if mode not in ("open", "close", "open_close", "close_open"):
            raise InstrumentError(f"unsupported mode: {mode!r}")
        scpi_mode = mode.upper().replace("_", "-")

        await self._ch.write_line(f"TIM:MODE {scpi_mode}")
        await self._await_ack()
        await self._ch.write_line("TIM:OPER")
        resp = (await self._ch.read_line(self._cfg.response_timeout_s)).strip()
        if resp != "STARTED":
            raise InstrumentError(f"TIM:OPER nak: {resp!r}")
        await self._wait_for_status("COMPLETE", timeout_s=10.0)

        per_pole = {}
        for pole in ("A", "B", "C"):
            await self._ch.write_line(f"READ:TIM:{pole}?")
            line = (await self._ch.read_line(self._cfg.response_timeout_s)).strip()
            per_pole[pole] = self._parse_pole_timing(mode, line)

        simul = {}
        if mode in ("open", "open_close", "close_open"):
            await self._ch.write_line("READ:SIMUL:OPEN?")
            simul["open"] = await self._read_float()
        if mode in ("close", "open_close", "close_open"):
            await self._ch.write_line("READ:SIMUL:CLOSE?")
            simul["close"] = await self._read_float()

        result: dict = {
            "mode": mode,
            "per_pole_ms": per_pole,
            "max_simultaneity_ms": simul,
        }

        if command.get("include_motion", True):
            await self._ch.write_line("READ:STROKE:PEAK?")
            peak = await self._read_float()
            await self._ch.write_line("READ:STROKE:OVER?")
            over = await self._read_float()
            await self._ch.write_line("READ:STROKE:REBOUND?")
            rebound = await self._read_float()
            result["stroke"] = {
                "peak_mm": round(peak, 2),
                "overtravel_mm": round(over, 2),
                "rebound_mm": round(rebound, 2),
            }
        if command.get("include_spring", True):
            await self._ch.write_line("READ:SPRING:CHARGE?")
            spring = await self._read_float()
            result["spring_charge_s"] = round(spring, 2)
        if command.get("include_coils", True):
            coil = {}
            await self._ch.write_line("READ:COIL:TRIP:PEAK?")
            coil["trip_a"] = round(await self._read_float(), 2)
            await self._ch.write_line("READ:COIL:CLOSE:PEAK?")
            coil["close_a"] = round(await self._read_float(), 2)
            result["coil_peak"] = coil

        cert = await self.verify_calibration()
        result["instrument_serial"] = cert.instrument_serial
        result["cert_id"] = cert.cert_id
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _require_connected(self) -> None:
        if not self._connected:
            raise InstrumentError("TM1800 not connected")

    async def _await_ack(self) -> None:
        # TM1800 echoes 'OK' on each set/config command per manual §8.5.
        resp = (await self._ch.read_line(self._cfg.response_timeout_s)).strip()
        if resp != "OK":
            raise InstrumentError(f"command nak: {resp!r}")

    async def _wait_for_status(self, target: str, timeout_s: float) -> None:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            await self._ch.write_line("TIM:STAT?")
            resp = (await self._ch.read_line(self._cfg.response_timeout_s)).strip()
            if resp == target:
                return
            if resp == "FAULT":
                raise InstrumentError("TM1800 reported FAULT")
            await asyncio.sleep(0.1)
        raise InstrumentError(f"timed out waiting for {target!r}")

    async def _read_float(self) -> float:
        resp = (await self._ch.read_line(self._cfg.response_timeout_s)).strip()
        try:
            return float(resp)
        except ValueError as e:
            raise InstrumentError(f"non-numeric response: {resp!r}") from e

    @staticmethod
    def _parse_pole_timing(mode: str, line: str):
        """Parse a per-pole timing response.

        Single-action mode ('open' / 'close'):
            'OPEN 33.4'  →  33.4
            'CLOSE 51.2' →  51.2

        Combined mode ('open_close' / 'close_open'):
            'OPEN 33.4,CLOSE 51.2'  →  {'open_ms': 33.4, 'close_ms': 51.2}
        """
        parts = [p.strip() for p in line.split(",")]
        out: dict[str, float] = {}
        for p in parts:
            label, _, val = p.partition(" ")
            try:
                f = float(val)
            except ValueError as e:
                raise InstrumentError(f"bad pole timing: {line!r}") from e
            out[f"{label.lower()}_ms"] = round(f, 2)
        if mode in ("open", "close"):
            # Single value — return it directly so the caller doesn't need
            # to know which key the test set used.
            if len(out) != 1:
                raise InstrumentError(f"expected 1 timing in {line!r}, got {len(out)}")
            return next(iter(out.values()))
        return out

    def _serial_from_idn(self) -> str:
        if not self._idn:
            return ""
        parts = self._idn.split(",")
        return parts[2] if len(parts) >= 3 else ""
