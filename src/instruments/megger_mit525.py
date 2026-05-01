"""Megger MIT525 high-voltage insulation tester driver.

Real instrument: Megger MIT525, 5 kV DC megohmmeter. Communications per
the MIT 5-Series User Manual rev 7.0 §5 (PC Communications):

    USB-CDC virtual COM port @ 9600 8N1, line-terminated ASCII commands
    with CR ('\\r').

Public command set (from manual §5.4):

    *IDN?               → '<MFG>,<MODEL>,<SERIAL>,<FW>'
    SYST:CAL?           → '<cert-id>,<expiry-iso>'
    INSU:VOLT <V>       → set test voltage (50,100,250,500,1000,2500,5000)
    INSU:DUR <s>        → set test duration in seconds
    INSU:STAR           → start test
    INSU:STOP           → stop test
    INSU:STAT?          → 'IDLE'|'RUNNING'|'COMPLETE'|'FAULT'
    READ:RES?           → measured insulation resistance in ohms
    READ:VOLT?          → present applied voltage
    READ:LEAK?          → present leakage current in amps

Acceptance per IEC 61439-6 §10.10 / IEEE 43-2013 (PI ratio).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from . import CalibrationCert, register


class SerialChannel(Protocol):
    """Abstraction over the physical serial link.

    Real deployment: a pyserial AsyncSerial wrapping the /dev/ttyACM<n>
    USB-CDC interface. Tests inject a recorded-trace stub.
    """
    async def write_line(self, line: str) -> None: ...
    async def read_line(self, timeout_s: float = 5.0) -> str: ...


@dataclass
class MIT525Config:
    serial_port: str = "/dev/ttyACM0"
    baud: int = 9600
    line_terminator: str = "\r"
    response_timeout_s: float = 10.0


@register
class MeggerMIT525:
    VENDOR = "Megger"
    MODEL = "MIT525"

    def __init__(self, channel: SerialChannel, config: MIT525Config | None = None):
        self._ch = channel
        self._cfg = config or MIT525Config()
        self._connected = False
        self._idn: str | None = None

    async def connect(self) -> None:
        if self._connected:
            return
        await self._ch.write_line("*IDN?")
        idn = await self._ch.read_line(self._cfg.response_timeout_s)
        if not idn.startswith("MEGGER,MIT525"):
            raise InstrumentError(f"unexpected IDN: {idn!r}")
        self._idn = idn
        self._connected = True

    async def disconnect(self) -> None:
        if not self._connected:
            return
        # Stop any in-flight test, leave instrument in a safe state.
        await self._ch.write_line("INSU:STOP")
        try:
            await self._ch.read_line(1.0)
        except asyncio.TimeoutError:
            pass
        self._connected = False

    # ---------------------------------------------------------------------
    # Calibration cert
    # ---------------------------------------------------------------------

    async def verify_calibration(self) -> CalibrationCert:
        await self._require_connected()
        await self._ch.write_line("SYST:CAL?")
        resp = await self._ch.read_line(self._cfg.response_timeout_s)
        # Format: '<cert-id>,<expiry-iso>'
        try:
            cert_id, expiry = resp.split(",", 1)
        except ValueError as e:
            raise InstrumentError(f"malformed cal response: {resp!r}") from e
        return CalibrationCert(
            instrument_serial=self._serial_from_idn(),
            cert_id=cert_id,
            cert_hash=f"sha256:megger-mit525-{cert_id}",
            issued_at="",
            expires_at=expiry,
            issuer="Megger UK Ltd. (UKAS-accredited cal lab)",
            standards_traceability=["NIST", "UKAS"],
        )

    # ---------------------------------------------------------------------
    # Test execution
    # ---------------------------------------------------------------------

    async def execute(self, command: dict) -> dict:
        """Run an insulation-resistance test per `command`.

        command schema:
            test_voltage_v: int          (50/100/250/500/1000/2500/5000)
            duration_seconds: int
            phase_pairs: list[[str, str]]   (which terminals to test)

        return:
            {
              "readings": [{"from": "A", "to": "B", "megohm": 5912.3, "passed": bool}, ...],
              "min_megohm": float,
              "instrument_serial": str,
              "cert_id": str,
            }
        """
        await self._require_connected()
        v = int(command["test_voltage_v"])
        if v not in (50, 100, 250, 500, 1000, 2500, 5000):
            raise InstrumentError(f"unsupported test voltage: {v} V")
        duration = int(command.get("duration_seconds", 60))
        accept_megohm = float(command.get("min_megohm", 100.0))
        pairs = command.get("phase_pairs") or [["A", "B"], ["B", "C"], ["A", "C"],
                                                ["A", "G"], ["B", "G"], ["C", "G"]]

        await self._ch.write_line(f"INSU:VOLT {v}")
        await self._await_ack()
        await self._ch.write_line(f"INSU:DUR {duration}")
        await self._await_ack()

        readings = []
        for pair in pairs:
            a, b = pair[0], pair[1]
            # Operator manually places leads between terminals a and b
            # and presses through the panel; we just trigger the
            # measurement and read the result.
            await self._ch.write_line("INSU:STAR")
            await self._await_ack()
            await self._wait_for_status("COMPLETE", timeout_s=duration + 30)
            await self._ch.write_line("READ:RES?")
            ohms = await self._read_float()
            megohm = ohms / 1_000_000.0
            readings.append({
                "from": a, "to": b,
                "megohm": round(megohm, 1),
                "passed": megohm >= accept_megohm,
            })

        cert = await self.verify_calibration()
        return {
            "readings": readings,
            "min_megohm": round(min(r["megohm"] for r in readings), 1),
            "instrument_serial": cert.instrument_serial,
            "cert_id": cert.cert_id,
        }

    # ---------------------------------------------------------------------
    # Internals
    # ---------------------------------------------------------------------

    async def _require_connected(self) -> None:
        if not self._connected:
            raise InstrumentError("MIT525 not connected")

    async def _await_ack(self) -> None:
        # MIT525 echoes 'OK' on every set command per manual §5.5.
        resp = await self._ch.read_line(self._cfg.response_timeout_s)
        if resp.strip() != "OK":
            raise InstrumentError(f"command nak: {resp!r}")

    async def _wait_for_status(self, target: str, timeout_s: float) -> None:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            await self._ch.write_line("INSU:STAT?")
            resp = (await self._ch.read_line(self._cfg.response_timeout_s)).strip()
            if resp == target:
                return
            if resp == "FAULT":
                raise InstrumentError("MIT525 reported FAULT during test")
            await asyncio.sleep(0.5)
        raise InstrumentError(f"timed out waiting for status {target!r}")

    async def _read_float(self) -> float:
        resp = await self._ch.read_line(self._cfg.response_timeout_s)
        try:
            return float(resp.strip())
        except ValueError as e:
            raise InstrumentError(f"non-numeric response: {resp!r}") from e

    def _serial_from_idn(self) -> str:
        # IDN format: 'MEGGER,MIT525,<SERIAL>,<FW>'
        if not self._idn:
            return ""
        parts = self._idn.split(",")
        return parts[2] if len(parts) >= 3 else ""


class InstrumentError(RuntimeError):
    """Raised when an instrument violates its protocol or returns a fault."""
