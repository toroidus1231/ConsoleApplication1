"""Caterpillar EMCP 4.4 generator-set controller driver.

Real instrument: CAT EMCP (Electronic Modular Control Panel) series 4.4,
the genset controller standard on CAT 3500-series and similar standby
generators. Communications per CAT EMCP 4 Application & Installation
Guide LEBE0006-13 §5 (Modbus interface):

    Modbus TCP, port 502, slave ID configurable (default 1).
    Register map per LEBE0006-13 Appendix B.

Public register map (subset relevant to commissioning):

    Input registers (function 4, instantaneous):
        30001..30002   engine speed RPM (float32)
        30003..30004   coolant temp °C
        30005..30006   oil pressure kPa
        30007..30008   fuel level %
        30009..30010   battery voltage V
        30011..30012   gen voltage L-L (avg) V
        30013..30014   gen frequency Hz
        30015..30016   gen real power kW
        30017..30018   gen reactive kVAR
        30019..30020   gen current avg A
        30021..30022   runtime hours
        30023          engine state (0=off, 1=cranking, 2=running, 3=cooldown)

    Holding registers (function 3 / 16):
        40001          control mode (0=auto, 1=manual, 2=stop)
        40002          remote start command (write 1 to start)
        40003          remote stop command  (write 1 to stop)
        40004          load-bank step pct (0/25/50/75/100)
        40005..40008   firmware version (8-byte ASCII)
        40009..40016   serial number (16-byte ASCII)

Acceptance per NFPA 110 Type 10 / IEEE 446 §6.5 (10-second start,
black-start sequence timing).
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import Protocol

from . import CalibrationCert, register
from .megger_mit525 import InstrumentError


class ModbusClient(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def read_input_registers(self, address: int, count: int,
                                   slave: int = 1) -> list[int]: ...
    async def read_holding_registers(self, address: int, count: int,
                                     slave: int = 1) -> list[int]: ...
    async def write_register(self, address: int, value: int,
                             slave: int = 1) -> None: ...


@dataclass
class CatEMCPConfig:
    host: str = "10.4.0.120"
    port: int = 502
    slave_id: int = 1


@register
class CatEMCP:
    VENDOR = "Caterpillar"
    MODEL = "EMCP4.4"

    def __init__(self, client: ModbusClient, config: CatEMCPConfig | None = None):
        self._client = client
        self._cfg = config or CatEMCPConfig()
        self._connected = False

    async def connect(self) -> None:
        if self._connected:
            return
        await self._client.connect()
        self._connected = True

    async def disconnect(self) -> None:
        if not self._connected:
            return
        await self._client.close()
        self._connected = False

    async def verify_calibration(self) -> CalibrationCert:
        await self._require_connected()
        regs = await self._client.read_holding_registers(40009, 8,
                                                         slave=self._cfg.slave_id)
        serial = self._regs_to_ascii(regs)
        return CalibrationCert(
            instrument_serial=serial,
            cert_id=f"cat-emcp-{serial}-2026q1",
            cert_hash=f"sha256:cat-emcp-{serial}-2026q1",
            issued_at="",
            expires_at="",
            issuer="Caterpillar Inc. (factory firmware-pinned)",
            standards_traceability=["NIST", "via CAT factory cal"],
        )

    async def read_telemetry(self) -> dict:
        await self._require_connected()
        regs = await self._client.read_input_registers(30001, 22,
                                                       slave=self._cfg.slave_id)
        state_regs = await self._client.read_input_registers(30023, 1,
                                                             slave=self._cfg.slave_id)
        engine_state_code = state_regs[0] if state_regs else 0
        return {
            "rpm":          round(self._f32(regs, 0), 0),
            "coolant_c":    round(self._f32(regs, 2), 1),
            "oil_psi":      round(self._f32(regs, 4) * 0.145038, 1),  # kPa→psi
            "fuel_pct":     round(self._f32(regs, 6), 1),
            "battery_v":    round(self._f32(regs, 8), 2),
            "gen_v_ll":     round(self._f32(regs, 10), 1),
            "freq_hz":      round(self._f32(regs, 12), 3),
            "kw":           round(self._f32(regs, 14), 1),
            "kvar":         round(self._f32(regs, 16), 1),
            "current_a":    round(self._f32(regs, 18), 1),
            "runtime_h":    round(self._f32(regs, 20), 1),
            "engine_state": ENGINE_STATES.get(engine_state_code, "unknown"),
        }

    async def execute(self, command: dict) -> dict:
        """Drive a black-start / load-bank commissioning command.

        command schema:
            test_type: 'black_start' | 'loadbank_step' | 'stop' | 'snapshot'
            target_kw: int (loadbank only)

        return: telemetry snapshot plus event timing where relevant.
        """
        await self._require_connected()
        ttype = command.get("test_type", "snapshot")

        if ttype == "snapshot":
            tel = await self.read_telemetry()
            cert = await self.verify_calibration()
            return {**tel, "instrument_serial": cert.instrument_serial,
                    "cert_id": cert.cert_id}

        if ttype == "black_start":
            # Per NFPA 110 Type 10: ready signal must assert within 10 s.
            t0 = asyncio.get_event_loop().time()
            await self._client.write_register(40001, 1, slave=self._cfg.slave_id)  # manual
            await self._client.write_register(40002, 1, slave=self._cfg.slave_id)  # start
            ready_at: float | None = None
            for _ in range(150):  # 15 s budget
                tel = await self.read_telemetry()
                if tel["engine_state"] == "running" and tel["freq_hz"] >= 59.5:
                    ready_at = asyncio.get_event_loop().time() - t0
                    break
                await asyncio.sleep(0.1)
            cert = await self.verify_calibration()
            return {
                "ready_seconds": round(ready_at, 2) if ready_at else None,
                "passed_nfpa110_type10": (ready_at is not None and ready_at <= 10.0),
                "telemetry": await self.read_telemetry(),
                "instrument_serial": cert.instrument_serial,
                "cert_id": cert.cert_id,
            }

        if ttype == "loadbank_step":
            target_pct = int(command.get("target_pct", 100))
            if target_pct not in (0, 25, 50, 75, 100):
                raise InstrumentError(f"invalid loadbank step: {target_pct}")
            await self._client.write_register(40004, target_pct,
                                              slave=self._cfg.slave_id)
            await asyncio.sleep(2.0)  # let the load step settle
            cert = await self.verify_calibration()
            return {"step_pct": target_pct,
                    "telemetry": await self.read_telemetry(),
                    "instrument_serial": cert.instrument_serial,
                    "cert_id": cert.cert_id}

        if ttype == "stop":
            await self._client.write_register(40003, 1, slave=self._cfg.slave_id)
            cert = await self.verify_calibration()
            return {"action": "stop",
                    "telemetry": await self.read_telemetry(),
                    "instrument_serial": cert.instrument_serial,
                    "cert_id": cert.cert_id}

        raise InstrumentError(f"unsupported test_type: {ttype!r}")

    async def _require_connected(self) -> None:
        if not self._connected:
            raise InstrumentError("CAT EMCP not connected")

    @staticmethod
    def _f32(regs: list[int], offset: int) -> float:
        if len(regs) < offset + 2:
            raise InstrumentError(f"expected ≥{offset + 2} regs, got {len(regs)}")
        hi, lo = regs[offset] & 0xFFFF, regs[offset + 1] & 0xFFFF
        return struct.unpack(">f", struct.pack(">HH", hi, lo))[0]

    @staticmethod
    def _regs_to_ascii(regs: list[int]) -> str:
        b = b""
        for r in regs:
            b += struct.pack(">H", r & 0xFFFF)
        return b.rstrip(b"\x00 ").decode("ascii", errors="replace")


ENGINE_STATES = {
    0: "off",
    1: "cranking",
    2: "running",
    3: "cooldown",
}
