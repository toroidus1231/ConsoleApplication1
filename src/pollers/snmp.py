"""Module 8: SNMP Poller.

Reads OIDs defined in a device's Config Context via pysnmp-lextudio 6.x.
The modern ``pysnmp.hlapi.v3arch.asyncio`` interface is used (the 5.x
``pysnmp.hlapi.asyncio`` path is gone in 6.x) and ``UdpTransportTarget.create``
is awaited.

Config Context shape for SNMP:
    {
      "protocol": "snmp",
      "connection": {
        "port": 161, "community": "public", "timeout_seconds": 5, "retries": 3
      },
      "oids": [
        {"name": "sysUpTime", "oid": "1.3.6.1.2.1.1.3.0", "scale": 0.01},
        ...
      ]
    }

Tests inject a fake ``reader`` to skip pysnmp entirely.
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable

from ..types import DeviceInfo, PollResult


# reader(host, port, community, oid, timeout, retries) -> raw value (str or number)
Reader = Callable[[str, int, str, str, float, int], Awaitable[object]]


async def _default_reader(
    host: str, port: int, community: str, oid: str, timeout: float, retries: int
):
    """pysnmp-lextudio 6.x bridge. Imported lazily to keep tests light."""
    from pysnmp.hlapi.v3arch.asyncio import (  # noqa: PLC0415
        CommunityData,
        ContextData,
        ObjectIdentity,
        ObjectType,
        SnmpEngine,
        UdpTransportTarget,
        get_cmd,
    )

    errorIndication, errorStatus, errorIndex, varBinds = await get_cmd(
        SnmpEngine(),
        CommunityData(community),
        await UdpTransportTarget.create((host, port), timeout=timeout, retries=retries),
        ContextData(),
        ObjectType(ObjectIdentity(oid)),
    )

    if errorIndication:
        raise RuntimeError(str(errorIndication))
    if errorStatus:
        raise RuntimeError(f"SNMP error: {errorStatus.prettyPrint()}")
    if not varBinds:
        raise RuntimeError("SNMP response had no varBinds")
    return varBinds[0][1]


async def poll_device(
    device: DeviceInfo,
    *,
    reader: Reader | None = None,
) -> PollResult:
    """Poll every OID in ``device.config_context["oids"]``."""
    reader = reader or _default_reader

    conn = device.config_context.get("connection", {}) or {}
    host = device.primary_ip or conn.get("host", "")
    port = int(conn.get("port", 161))
    community = conn.get("community", "public")
    timeout = float(conn.get("timeout_seconds", 5))
    retries = int(conn.get("retries", 3))

    measurements: dict = {}
    raw_bytes: dict = {}
    errors: dict = {}

    for oid_def in device.config_context.get("oids", []) or []:
        name = oid_def["name"]
        oid = oid_def["oid"]
        scale = float(oid_def.get("scale", 1.0))

        try:
            value = await reader(host, port, community, oid, timeout, retries)
        except Exception as e:  # noqa: BLE001 — per-OID error, not a poll abort
            errors[name] = f"{type(e).__name__}: {e}"
            continue

        raw_bytes[name] = str(value)

        try:
            measurements[name] = float(value) * scale
        except (TypeError, ValueError) as e:
            errors[name] = f"Non-numeric SNMP value '{value}': {e}"

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
