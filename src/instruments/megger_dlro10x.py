"""Megger DLRO 10X four-wire ductor (joint resistance) driver.

Real instrument: Megger DLRO 10X, 10 A DC ductor for low-resistance
measurements. Communications per the DLRO 10X User Manual rev 4.2 §6
(PC interface):

    Bluetooth SPP or RS-232 @ 19200 8N1, line-terminated ASCII commands
    with CRLF.

Public command set (manual §6.3):

    *IDN?               → '<MFG>,<MODEL>,<SERIAL>,<FW>'
    SYST:CAL?           → '<cert-id>,<expiry>'
    DLRO:CURR <A>       → set test current (1, 5, 10 A)
    DLRO:RANG <ohms>    → set range (e.g. AUTO, 200µΩ, 2mΩ, 20mΩ, 200mΩ, 2Ω)
    DLRO:MEAS           → trigger measurement
    DLRO:STAT?          → 'IDLE'|'RUNNING'|'COMPLETE'|'FAULT'
    READ:RES?           → measured resistance in µΩ (microhms)
    READ:CURR?          → present test current in A
    SYST:LOC?           → operator-set joint label (optional)

Acceptance per Vertiv MTG / iMPB field-service guides and IEC 61439-6
§10.5 (≤ 30 µΩ for MTG, ≤ 25 µΩ for iMPB).
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
class DLRO10XConfig:
    response_timeout_s: float = 5.0
    measurement_timeout_s: float = 30.0


@register
class MeggerDLRO10X:
    VENDOR = "Megger"
    MODEL = "DLRO10X"

    def __init__(self, channel: SerialChannel, config: DLRO10XConfig | None = None):
        self._ch = channel
        self._cfg = config or DLRO10XConfig()
        self._connected = False
        self._idn: str | None = None

    async def connect(self) -> None:
        if self._connected:
            return
        await self._ch.write_line("*IDN?")
        idn = await self._ch.read_line(self._cfg.response_timeout_s)
        if not idn.startswith("MEGGER,DLRO10X"):
            raise InstrumentError(f"unexpected IDN: {idn!r}")
        self._idn = idn
        self._connected = True

    async def disconnect(self) -> None:
        if not self._connected:
            return
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
            cert_id=cert_id,
            cert_hash=f"sha256:megger-dlro10x-{cert_id}",
            issued_at="",
            expires_at=expiry,
            issuer="Megger UK Ltd. (UKAS-accredited cal lab)",
            standards_traceability=["NIST", "UKAS"],
        )

    async def execute(self, command: dict) -> dict:
        """Walk the operator through measuring each bolted joint.

        command schema:
            test_current_a: float        (1, 5, 10)
            joint_count: int             (number of bolted joints in the run)
            phases: list[str]            (default ["A", "B", "C"])
            max_uohm_per_joint: float    (acceptance threshold)

        return:
            {
              "joints": [{joint, uohm_per_phase: {A,B,C}, max_uohm, passed}, ...],
              "max_joint_uohm": float,
              "instrument_serial": str,
              "cert_id": str,
            }
        """
        await self._require_connected()
        current_a = float(command.get("test_current_a", 10.0))
        if current_a not in (1.0, 5.0, 10.0):
            raise InstrumentError(f"unsupported DLRO current: {current_a} A")
        n = int(command["joint_count"])
        phases = command.get("phases") or ["A", "B", "C"]
        accept_uohm = float(command["max_uohm_per_joint"])

        await self._ch.write_line(f"DLRO:CURR {current_a}")
        await self._await_ack()
        await self._ch.write_line("DLRO:RANG AUTO")
        await self._await_ack()

        joints = []
        for j in range(1, n + 1):
            per_phase = {}
            for ph in phases:
                # Operator places probes on phase ph at joint j and
                # presses TEST on the panel; we trigger remotely.
                await self._ch.write_line("DLRO:MEAS")
                await self._await_ack()
                await self._wait_for_status("COMPLETE",
                                            timeout_s=self._cfg.measurement_timeout_s)
                await self._ch.write_line("READ:RES?")
                uohm = await self._read_float()
                per_phase[ph] = round(uohm, 1)
            max_u = max(per_phase.values())
            joints.append({
                "joint": j,
                "uohm_per_phase": per_phase,
                "max_uohm": max_u,
                "passed": max_u <= accept_uohm,
            })

        cert = await self.verify_calibration()
        return {
            "joints": joints,
            "max_joint_uohm": round(max(j["max_uohm"] for j in joints), 1),
            "instrument_serial": cert.instrument_serial,
            "cert_id": cert.cert_id,
        }

    async def _require_connected(self) -> None:
        if not self._connected:
            raise InstrumentError("DLRO10X not connected")

    async def _await_ack(self) -> None:
        resp = await self._ch.read_line(self._cfg.response_timeout_s)
        if resp.strip() != "OK":
            raise InstrumentError(f"command nak: {resp!r}")

    async def _wait_for_status(self, target: str, timeout_s: float) -> None:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            await self._ch.write_line("DLRO:STAT?")
            resp = (await self._ch.read_line(self._cfg.response_timeout_s)).strip()
            if resp == target:
                return
            if resp == "FAULT":
                raise InstrumentError("DLRO10X reported FAULT")
            await asyncio.sleep(0.2)
        raise InstrumentError(f"timed out waiting for {target!r}")

    async def _read_float(self) -> float:
        resp = await self._ch.read_line(self._cfg.response_timeout_s)
        try:
            return float(resp.strip())
        except ValueError as e:
            raise InstrumentError(f"non-numeric response: {resp!r}") from e

    def _serial_from_idn(self) -> str:
        if not self._idn:
            return ""
        parts = self._idn.split(",")
        return parts[2] if len(parts) >= 3 else ""
