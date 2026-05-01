"""Instrument driver registry.

Each driver implements a small, uniform protocol:

    class XYZDriver(InstrumentDriver):
        VENDOR = "Megger"
        MODEL = "MIT525"
        async def connect(self) -> None: ...
        async def disconnect(self) -> None: ...
        async def execute(self, command: dict) -> dict: ...
        async def verify_calibration(self) -> CalibrationCert: ...

The TestEngine looks up the driver by (vendor, model) from the test
definition's `instrument` block, calls connect/execute/disconnect, and
captures the result for the EvidenceStore. Adding a new instrument is
one new file in this directory plus a registry entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class CalibrationCert:
    """Calibration certificate metadata returned by an instrument."""
    instrument_serial: str
    cert_id: str
    cert_hash: str
    issued_at: str
    expires_at: str
    issuer: str
    standards_traceability: list[str]


class InstrumentDriver(Protocol):
    VENDOR: str
    MODEL: str

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def execute(self, command: dict) -> dict: ...
    async def verify_calibration(self) -> CalibrationCert: ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_DRIVERS: dict[tuple[str, str], type] = {}


def register(driver_cls: type) -> type:
    """Decorator that registers a driver class by (VENDOR, MODEL)."""
    _DRIVERS[(driver_cls.VENDOR.lower(), driver_cls.MODEL.lower())] = driver_cls
    return driver_cls


def get_driver(vendor: str, model: str) -> type | None:
    return _DRIVERS.get((vendor.lower(), model.lower()))


def list_drivers() -> list[tuple[str, str]]:
    return sorted(_DRIVERS.keys())


# Eagerly import each driver module so its @register decorator runs.
def _eager_register():
    from . import (
        megger_mit525, megger_dlro10x, vitrek_95x,
        qualitrol_118itm, vaisala_opt100, sel_751, cat_emcp,
    )
    return [
        megger_mit525, megger_dlro10x, vitrek_95x,
        qualitrol_118itm, vaisala_opt100, sel_751, cat_emcp,
    ]


# Don't auto-import on package import to keep import cost low for tests
# that only need the registry types.
