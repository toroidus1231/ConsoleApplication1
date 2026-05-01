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
#
# Keyed on (vendor, model, transport). `transport` lets the same physical
# instrument (e.g., an SEL-751 that supports Modbus, DNP3, and IEC 61850)
# be registered under multiple driver classes, one per protocol the
# operator might use to talk to it. Existing call sites that don't care
# about transport pass transport=None and get back the first match.
# ---------------------------------------------------------------------------


_DRIVERS: dict[tuple[str, str, str | None], type] = {}


def register(driver_cls: type | None = None, *, transport: str | None = None):
    """Register a driver. Usable as a bare decorator (transport=None) or
    parametric (`@register(transport="dnp3")`).

    Backwards compatible with existing callers that wrote `@register`."""
    def _do(cls: type) -> type:
        t = (getattr(cls, "TRANSPORT", None) or transport)
        key = (cls.VENDOR.lower(), cls.MODEL.lower(),
               t.lower() if t else None)
        _DRIVERS[key] = cls
        return cls
    if driver_cls is not None and isinstance(driver_cls, type):
        return _do(driver_cls)
    return _do


def get_driver(vendor: str, model: str,
               transport: str | None = None) -> type | None:
    """Look up a driver class by (vendor, model[, transport]).

    If `transport` is given, returns the driver registered for exactly
    that transport. If `transport` is None, returns whatever was
    registered without a transport tag, falling back to any registered
    driver for that (vendor, model)."""
    v, m = vendor.lower(), model.lower()
    if transport:
        return _DRIVERS.get((v, m, transport.lower()))
    if (v, m, None) in _DRIVERS:
        return _DRIVERS[(v, m, None)]
    for (vv, mm, _t), cls in _DRIVERS.items():
        if vv == v and mm == m:
            return cls
    return None


def list_drivers() -> list[tuple[str, str, str | None]]:
    return sorted(_DRIVERS.keys(), key=lambda k: (k[0], k[1], k[2] or ""))


# Eagerly import each driver module so its @register decorator runs.
def _eager_register():
    from . import (
        megger_mit525, megger_dlro10x, vitrek_95x,
        qualitrol_118itm, vaisala_opt100, sel_751, sel_751_dnp3, cat_emcp,
        doble_f6150, megger_tm1800,
    )
    return [
        megger_mit525, megger_dlro10x, vitrek_95x,
        qualitrol_118itm, vaisala_opt100, sel_751, sel_751_dnp3, cat_emcp,
        doble_f6150, megger_tm1800,
    ]


# Don't auto-import on package import to keep import cost low for tests
# that only need the registry types.
