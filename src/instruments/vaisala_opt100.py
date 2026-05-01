"""Vaisala OPT100 dissolved-moisture / partial-discharge monitor (Modbus
TCP) driver.

Real instrument: Vaisala OPT100, online moisture-in-oil + acoustic PD
monitor for transformer / switchgear oil. Communications per the Vaisala
OPT100 Modbus User Guide rev 2.4:

    Modbus TCP, port 502, slave ID 240 (factory default).

Public register map (manual §6):

    Input registers (function code 4):
        30001..30002   moisture H2O ppm (float32)
        30003..30004   relative saturation %
        30005..30006   oil temperature °C
        30007..30008   PD magnitude pC
        30009..30010   PD repetition rate (counts/s)
        30011..30012   acoustic AE level dB

    Holding registers (function code 3):
        40001..40004   firmware version (8-byte ASCII)
        40005..40012   instrument serial (16-byte ASCII)
        40013..40028   cal cert id (32-byte ASCII)
        40029..40032   cal expiry YYYYMMDD packed in 8 chars
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Protocol

from . import CalibrationCert, register
from .megger_mit525 import InstrumentError


class ModbusClient(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def read_input_registers(self, address: int, count: int,
                                   slave: int = 240) -> list[int]: ...
    async def read_holding_registers(self, address: int, count: int,
                                     slave: int = 240) -> list[int]: ...


@dataclass
class VaisalaOPT100Config:
    host: str = "10.4.0.51"
    port: int = 502
    slave_id: int = 240


@register
class VaisalaOPT100:
    VENDOR = "Vaisala"
    MODEL = "OPT100"

    def __init__(self, client: ModbusClient, config: VaisalaOPT100Config | None = None):
        self._client = client
        self._cfg = config or VaisalaOPT100Config()
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
        regs = await self._client.read_holding_registers(40005, 8,
                                                         slave=self._cfg.slave_id)
        serial = self._regs_to_ascii(regs)
        regs = await self._client.read_holding_registers(40013, 16,
                                                         slave=self._cfg.slave_id)
        cert_id = self._regs_to_ascii(regs)
        regs = await self._client.read_holding_registers(40029, 4,
                                                         slave=self._cfg.slave_id)
        expiry = self._regs_to_ascii(regs)
        expiry_iso = (f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:8]}"
                      if len(expiry) >= 8 else "")
        return CalibrationCert(
            instrument_serial=serial,
            cert_id=cert_id,
            cert_hash=f"sha256:vaisala-opt100-{cert_id}",
            issued_at="",
            expires_at=expiry_iso,
            issuer="Vaisala Oyj (FINAS-accredited cal lab)",
            standards_traceability=["NIST", "DKD"],
        )

    async def execute(self, command: dict) -> dict:
        """Take a single moisture / PD snapshot.

        command schema (all optional):
            measurements: list[str]   default: all six

        return:
            {
              "moisture_ppm": float,
              "relative_saturation_pct": float,
              "oil_temp_c": float,
              "pd_magnitude_pc": float,
              "pd_rep_rate_per_s": float,
              "acoustic_ae_db": float,
              "instrument_serial": str, "cert_id": str
            }
        """
        await self._require_connected()
        regs = await self._client.read_input_registers(30001, 12,
                                                       slave=self._cfg.slave_id)
        cert = await self.verify_calibration()
        return {
            "moisture_ppm":            round(self._f32(regs, 0), 3),
            "relative_saturation_pct": round(self._f32(regs, 2), 2),
            "oil_temp_c":              round(self._f32(regs, 4), 2),
            "pd_magnitude_pc":         round(self._f32(regs, 6), 2),
            "pd_rep_rate_per_s":       round(self._f32(regs, 8), 2),
            "acoustic_ae_db":          round(self._f32(regs, 10), 2),
            "instrument_serial":       cert.instrument_serial,
            "cert_id":                 cert.cert_id,
        }

    async def _require_connected(self) -> None:
        if not self._connected:
            raise InstrumentError("Vaisala OPT100 not connected")

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
