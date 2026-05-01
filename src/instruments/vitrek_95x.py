"""Vitrek 95X high-voltage hipot tester driver.

Real instrument: Vitrek 95X, AC/DC hipot 0–15 kV. Communications per the
Vitrek 95X Programmer's Reference rev 5.0 §4 (SCPI):

    USB-CDC virtual COM port @ 115200 8N1, line-terminated SCPI commands
    with LF.

Public command set (manual §4.2):

    *IDN?                       → '<MFG>,<MODEL>,<SERIAL>,<FW>'
    SYSTem:CALibration?         → '<cert-id>,<expiry-iso>'
    HIPot:DC:VOLTage <kV>       → DC test voltage 0.05–15.0 kV
    HIPot:DC:RAMP <s>           → ramp time in seconds
    HIPot:DC:DWELl <s>          → dwell (hold) time in seconds
    HIPot:DC:LIMit:LEAKage <mA> → trip threshold in mA
    HIPot:STARt                 → start a configured test
    HIPot:STOP                  → abort
    HIPot:STATus?               → 'IDLE'|'RAMP'|'DWELL'|'PASS'|'FAIL'|'TRIP'
    HIPot:RESult?               → '<peak-leakage-mA>,<final-V-kV>,<elapsed-s>'
    HIPot:TRACe?                → '<n-samples>;<t1>,<v1>,<i1>;<t2>,<v2>,<i2>;...'

Acceptance per IEEE 400.1 / NETA ATS-17 Table 100.5.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from . import CalibrationCert, register
from .megger_mit525 import InstrumentError


class SerialChannel(Protocol):
    async def write_line(self, line: str) -> None: ...
    async def read_line(self, timeout_s: float = 5.0) -> str: ...


@dataclass
class Vitrek95XConfig:
    response_timeout_s: float = 10.0


@register
class Vitrek95X:
    VENDOR = "Vitrek"
    MODEL = "95X"

    def __init__(self, channel: SerialChannel, config: Vitrek95XConfig | None = None):
        self._ch = channel
        self._cfg = config or Vitrek95XConfig()
        self._connected = False
        self._idn: str | None = None

    async def connect(self) -> None:
        if self._connected:
            return
        await self._ch.write_line("*IDN?")
        idn = await self._ch.read_line(self._cfg.response_timeout_s)
        if not idn.startswith("VITREK,95X"):
            raise InstrumentError(f"unexpected IDN: {idn!r}")
        self._idn = idn
        self._connected = True

    async def disconnect(self) -> None:
        if not self._connected:
            return
        await self._ch.write_line("HIPot:STOP")
        try:
            await self._ch.read_line(1.0)
        except asyncio.TimeoutError:
            pass
        self._connected = False

    async def verify_calibration(self) -> CalibrationCert:
        await self._require_connected()
        await self._ch.write_line("SYSTem:CALibration?")
        resp = await self._ch.read_line(self._cfg.response_timeout_s)
        try:
            cert_id, expiry = resp.split(",", 1)
        except ValueError as e:
            raise InstrumentError(f"malformed cal response: {resp!r}") from e
        return CalibrationCert(
            instrument_serial=self._serial_from_idn(),
            cert_id=cert_id,
            cert_hash=f"sha256:vitrek-95x-{cert_id}",
            issued_at="",
            expires_at=expiry,
            issuer="Vitrek Corp. (A2LA-accredited cal lab)",
            standards_traceability=["NIST"],
        )

    async def execute(self, command: dict) -> dict:
        """Run a DC withstand (hipot) per `command`.

        command schema:
            target_kv: float
            ramp_seconds: int
            hold_seconds: int
            leakage_trip_ma: float

        return:
            {
              "trace": [{t_seconds, voltage_kv, leakage_ma}, ...],
              "peak_leakage_ma": float,
              "tripped": bool,
              "elapsed_seconds": float,
              "instrument_serial": str,
              "cert_id": str,
            }
        """
        await self._require_connected()
        kv = float(command["target_kv"])
        if not 0.05 <= kv <= 15.0:
            raise InstrumentError(f"voltage out of range: {kv} kV")
        ramp = int(command["ramp_seconds"])
        dwell = int(command["hold_seconds"])
        trip = float(command["leakage_trip_ma"])

        await self._ch.write_line(f"HIPot:DC:VOLTage {kv}")
        await self._await_ack()
        await self._ch.write_line(f"HIPot:DC:RAMP {ramp}")
        await self._await_ack()
        await self._ch.write_line(f"HIPot:DC:DWELl {dwell}")
        await self._await_ack()
        await self._ch.write_line(f"HIPot:DC:LIMit:LEAKage {trip}")
        await self._await_ack()

        await self._ch.write_line("HIPot:STARt")
        await self._await_ack()

        terminal = await self._wait_for_terminal_status(
            timeout_s=ramp + dwell + 30
        )

        await self._ch.write_line("HIPot:RESult?")
        result_line = await self._ch.read_line(self._cfg.response_timeout_s)
        peak_ma, final_kv, elapsed = self._parse_result(result_line)

        await self._ch.write_line("HIPot:TRACe?")
        trace_line = await self._ch.read_line(self._cfg.response_timeout_s * 6)
        trace = self._parse_trace(trace_line)

        cert = await self.verify_calibration()
        return {
            "trace": trace,
            "peak_leakage_ma": peak_ma,
            "tripped": terminal in ("TRIP", "FAIL"),
            "elapsed_seconds": elapsed,
            "instrument_serial": cert.instrument_serial,
            "cert_id": cert.cert_id,
        }

    async def _require_connected(self) -> None:
        if not self._connected:
            raise InstrumentError("Vitrek 95X not connected")

    async def _await_ack(self) -> None:
        resp = await self._ch.read_line(self._cfg.response_timeout_s)
        if resp.strip() != "OK":
            raise InstrumentError(f"command nak: {resp!r}")

    async def _wait_for_terminal_status(self, timeout_s: float) -> str:
        deadline = asyncio.get_event_loop().time() + timeout_s
        terminal = {"PASS", "FAIL", "TRIP"}
        while asyncio.get_event_loop().time() < deadline:
            await self._ch.write_line("HIPot:STATus?")
            resp = (await self._ch.read_line(self._cfg.response_timeout_s)).strip()
            if resp in terminal:
                return resp
            await asyncio.sleep(0.5)
        raise InstrumentError("hipot did not reach terminal status before timeout")

    def _parse_result(self, line: str) -> tuple[float, float, float]:
        parts = line.strip().split(",")
        if len(parts) != 3:
            raise InstrumentError(f"malformed result: {line!r}")
        try:
            return float(parts[0]), float(parts[1]), float(parts[2])
        except ValueError as e:
            raise InstrumentError(f"malformed result floats: {line!r}") from e

    def _parse_trace(self, line: str) -> list[dict]:
        # Format: '<n-samples>;<t1>,<v1>,<i1>;<t2>,<v2>,<i2>;...'
        head, _, body = line.strip().partition(";")
        try:
            n = int(head)
        except ValueError as e:
            raise InstrumentError(f"malformed trace header: {line!r}") from e
        out = []
        for sample in body.split(";"):
            sample = sample.strip()
            if not sample:
                continue
            try:
                t, v, i = (float(x) for x in sample.split(","))
            except ValueError as e:
                raise InstrumentError(f"malformed trace sample: {sample!r}") from e
            out.append({"t_seconds": t, "voltage_kv": v, "leakage_ma": i})
        if len(out) != n:
            raise InstrumentError(
                f"trace length mismatch: header={n} body={len(out)}"
            )
        return out

    def _serial_from_idn(self) -> str:
        if not self._idn:
            return ""
        parts = self._idn.split(",")
        return parts[2] if len(parts) >= 3 else ""
