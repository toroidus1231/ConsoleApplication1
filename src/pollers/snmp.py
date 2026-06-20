"""Module 8: SNMP Poller.

Reads every OID defined in a device's Config Context via SNMP GET. Returns a
PollResult. Each OID is read independently so one bad OID is reported as a
per-OID error rather than failing the whole poll. Detects NaN/Inf on numeric
conversions and reports them as per-OID errors rather than crashing.

``pysnmp`` is a heavy dependency and is imported lazily inside the default
getter so this module (and its tests) import cleanly without it installed.
Tests inject a fake ``getter`` and never touch pysnmp.
"""

from __future__ import annotations

import math
import time
from typing import Awaitable, Callable, NamedTuple

from ..types import DeviceInfo, PollResult


class SnmpResult(NamedTuple):
    """Normalized result of a single SNMP GET.

    ``error`` is None on success. ``value`` carries the SNMP value (numeric or
    otherwise) and ``raw`` its raw string form.
    """

    value: object
    raw: str
    error: str | None


# An injectable async seam performing ONE SNMP GET.
# args: (host, port, community, oid, timeout_s) -> SnmpResult
SnmpVarBind = SnmpResult
SnmpGetter = Callable[[str, int, str, str, float], Awaitable[SnmpVarBind]]


async def poll_device(
    device: DeviceInfo,
    *,
    getter: SnmpGetter | None = None,
) -> PollResult:
    """Poll all OIDs defined in ``device.config_context``.

    Args:
        device: Device to poll.
        getter: Optional async callable performing a single SNMP GET (tests
            inject a fake here). When None, a pysnmp-backed getter is used,
            constructed lazily so pysnmp is only imported when actually polling.
    """
    conn = device.config_context.get("connection", {}) or {}
    host = device.primary_ip or conn.get("host", "")
    port = int(conn.get("port", 161))
    community = conn.get("community", "public")
    timeout_s = float(conn.get("timeout_seconds", 5))

    if getter is None:
        getter = _default_getter

    measurements: dict = {}
    raw_bytes: dict = {}
    errors: dict = {}

    for oid_def in device.config_context.get("oids", []) or []:
        name = oid_def["name"]
        oid = oid_def["oid"]
        scale = float(oid_def.get("scale", 1.0))
        try:
            res = await getter(host, port, community, oid, timeout_s)
            if res.error is not None:
                errors[name] = res.error
                continue
            raw_bytes[name] = res.raw
            value, err = _coerce_value(res.value, scale)
            if err is not None:
                errors[name] = err
                continue
            measurements[name] = value
        except Exception as e:  # noqa: BLE001 — any getter failure is per-OID
            errors[name] = f"{type(e).__name__}: {e}"

    return PollResult(
        device_id=device.device_id,
        timestamp_ns=time.time_ns(),
        measurements=measurements,
        raw_bytes=raw_bytes,
        protocol="snmp",
        source_ip=host,
        success=len(errors) == 0,
        errors=errors,
    )


def _coerce_value(value: object, scale: float) -> tuple[object, str | None]:
    """Convert an SNMP value to a scaled float. Returns (value, error)."""
    if value is None:
        return None, "SNMP value is empty/None"

    try:
        numeric = float(value)
    except (TypeError, ValueError) as e:
        return None, f"Non-numeric SNMP value {value!r}: {e}"

    if math.isnan(numeric) or math.isinf(numeric):
        return None, f"NaN/Inf detected in SNMP value. Raw: {value!r}"

    return numeric * scale, None


async def _default_getter(
    host: str,
    port: int,
    community: str,
    oid: str,
    timeout_s: float,
) -> SnmpResult:
    """pysnmp-backed SNMP GET. Imports pysnmp lazily (heavy dependency)."""
    from pysnmp.hlapi.v3arch.asyncio import (  # noqa: PLC0415 — lazy import
        CommunityData,
        ContextData,
        ObjectIdentity,
        ObjectType,
        SnmpEngine,
        UdpTransportTarget,
        get_cmd,
    )

    error_indication, error_status, _error_index, var_binds = await get_cmd(
        SnmpEngine(),
        CommunityData(community),
        await UdpTransportTarget.create((host, port), timeout=timeout_s, retries=3),
        ContextData(),
        ObjectType(ObjectIdentity(oid)),
    )

    if error_indication:
        return SnmpResult(None, "", str(error_indication))
    if error_status:
        return SnmpResult(None, "", f"SNMP error: {error_status.prettyPrint()}")

    for var_bind in var_binds:
        val = var_bind[1]
        return SnmpResult(val, str(val), None)

    return SnmpResult(None, "", "SNMP returned no varBinds")
