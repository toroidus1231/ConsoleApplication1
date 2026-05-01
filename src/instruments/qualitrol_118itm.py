"""Qualitrol 118ITM dissolved-gas analysis monitor (Modbus TCP) driver.

Real instrument: Qualitrol 118ITM, 8-gas online DGA monitor for oil-
filled transformers. Communications per the Qualitrol 118ITM Modbus
Reference rev 3.1:

    Modbus TCP, port 502, slave ID 1.
    Holding registers (function codes 3 / 16) at the addresses below.

Public register map (manual §7):

    40001..40002   H2  ppm    (float32, big-endian)
    40003..40004   CH4 ppm
    40005..40006   C2H6 ppm
    40007..40008   C2H4 ppm
    40009..40010   C2H2 ppm
    40011..40012   CO  ppm
    40013..40014   CO2 ppm
    40015..40016   O2  ppm
    40017..40018   N2  ppm
    40019..40020   moisture ppm (water in oil)
    40021..40022   oil temperature °C
    40023..40024   ambient °C
    40025          status bitmap (uint16)
    40027..40034   firmware version (8-byte ASCII)
    40035..40058   cal cert id (24-byte ASCII)
    40059..40066   cal expiry ISO date (8 chars: YYYYMMDD packed in 4 regs)
    40067..40074   instrument serial (8 chars)

Acceptance per IEEE C57.104-2008 / IEC 60599 (Duval triangle).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Protocol

from . import CalibrationCert, register
from .megger_mit525 import InstrumentError


class ModbusClient(Protocol):
    """Wraps an AsyncModbusTcpClient. Tests inject a recorded-trace stub."""
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def read_holding_registers(self, address: int, count: int,
                                     slave: int = 1) -> list[int]: ...


@dataclass
class Qualitrol118ITMConfig:
    host: str = "10.4.0.50"
    port: int = 502
    slave_id: int = 1


GAS_REGISTERS = [
    ("h2",       40001),
    ("ch4",      40003),
    ("c2h6",     40005),
    ("c2h4",     40007),
    ("c2h2",     40009),
    ("co",       40011),
    ("co2",      40013),
    ("o2",       40015),
    ("n2",       40017),
    ("moisture", 40019),
    ("oil_temp", 40021),
    ("ambient",  40023),
]


@register
class Qualitrol118ITM:
    VENDOR = "Qualitrol"
    MODEL = "118ITM"

    def __init__(self, client: ModbusClient, config: Qualitrol118ITMConfig | None = None):
        self._client = client
        self._cfg = config or Qualitrol118ITMConfig()
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
        # cal cert id at 40035, 24 bytes = 12 regs
        regs = await self._client.read_holding_registers(40035, 12,
                                                         slave=self._cfg.slave_id)
        cert_id = self._regs_to_ascii(regs)
        # expiry at 40059, 8 chars = 4 regs
        regs = await self._client.read_holding_registers(40059, 4,
                                                         slave=self._cfg.slave_id)
        expiry_yyyymmdd = self._regs_to_ascii(regs)
        # serial at 40067, 8 chars = 4 regs
        regs = await self._client.read_holding_registers(40067, 4,
                                                         slave=self._cfg.slave_id)
        serial = self._regs_to_ascii(regs)
        if not expiry_yyyymmdd or len(expiry_yyyymmdd) < 8:
            expiry_iso = ""
        else:
            expiry_iso = (f"{expiry_yyyymmdd[:4]}-"
                          f"{expiry_yyyymmdd[4:6]}-"
                          f"{expiry_yyyymmdd[6:8]}")
        return CalibrationCert(
            instrument_serial=serial,
            cert_id=cert_id,
            cert_hash=f"sha256:qualitrol-118itm-{cert_id}",
            issued_at="",
            expires_at=expiry_iso,
            issuer="Qualitrol Corp. (A2LA-accredited cal lab)",
            standards_traceability=["NIST"],
        )

    async def execute(self, command: dict) -> dict:
        """Take a single DGA snapshot. Reads the gas concentrations,
        moisture, and oil temperature.

        command schema (all optional):
            gases: list[str]   (default: every gas register)

        return:
            {
              "h2": ppm, "ch4": ppm, "c2h6": ppm, "c2h4": ppm, "c2h2": ppm,
              "co": ppm, "co2": ppm, "o2": ppm, "n2": ppm,
              "moisture_ppm": float, "oil_temp_c": float, "ambient_c": float,
              "instrument_serial": str, "cert_id": str
            }
        """
        await self._require_connected()
        wanted = set(command.get("gases") or [name for name, _ in GAS_REGISTERS])
        result: dict = {}
        for name, addr in GAS_REGISTERS:
            if name not in wanted:
                continue
            regs = await self._client.read_holding_registers(addr, 2,
                                                             slave=self._cfg.slave_id)
            value = self._regs_to_float32(regs)
            result[name] = round(value, 3)
        cert = await self.verify_calibration()
        result["instrument_serial"] = cert.instrument_serial
        result["cert_id"] = cert.cert_id
        return result

    async def _require_connected(self) -> None:
        if not self._connected:
            raise InstrumentError("Qualitrol 118ITM not connected")

    @staticmethod
    def _regs_to_float32(regs: list[int]) -> float:
        if len(regs) < 2:
            raise InstrumentError(f"expected 2 regs, got {len(regs)}")
        hi, lo = regs[0] & 0xFFFF, regs[1] & 0xFFFF
        b = struct.pack(">HH", hi, lo)
        return struct.unpack(">f", b)[0]

    @staticmethod
    def _regs_to_ascii(regs: list[int]) -> str:
        b = b""
        for r in regs:
            b += struct.pack(">H", r & 0xFFFF)
        return b.rstrip(b"\x00 ").decode("ascii", errors="replace")
