"""Module 7: BACnet/IP Poller.

Reads BACnet objects defined in a device's Config Context using BAC0. BAC0 is
synchronous, so reads are run in a thread via ``asyncio.to_thread`` to avoid
blocking the event loop.

The BAC0 stack is lazily initialized at first use and reused across polls
(BAC0.lite() is a heavy global — you want one per process, not one per poll).
Tests can inject a ``reader`` callable to bypass BAC0 entirely.

Config Context shape for BACnet:
    {
      "protocol": "bacnet_ip",
      "objects": [
        {"name": "zone_temp", "type": "analogInput",  "instance": 1, "property": "presentValue"},
        ...
      ]
    }
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable

from ..types import DeviceInfo, PollResult


# (ip, object_type, instance, property) -> str|float returned by BAC0.read()
Reader = Callable[[str, str, int, str], object]


_bacnet_singleton = None


def _default_reader(host: str, obj_type: str, instance: int, prop: str):
    """Lazy BAC0 bridge. Kept out of module import so tests don't need BAC0."""
    global _bacnet_singleton
    if _bacnet_singleton is None:
        import BAC0  # noqa: PLC0415 — deferred to keep test envs light

        _bacnet_singleton = BAC0.lite()
    return _bacnet_singleton.read(f"{host} {obj_type} {instance} {prop}")


async def poll_device(
    device: DeviceInfo,
    *,
    reader: Reader | None = None,
) -> PollResult:
    """Poll every object in ``device.config_context["objects"]``.

    Args:
        device: Device to poll.
        reader: Optional sync callable ``reader(host, obj_type, instance, prop)``.
            Defaults to BAC0. Tests pass a fake.
    """
    reader = reader or _default_reader

    conn = device.config_context.get("connection", {}) or {}
    host = device.primary_ip or conn.get("host", "")

    measurements: dict = {}
    raw_bytes: dict = {}
    errors: dict = {}

    for obj in device.config_context.get("objects", []) or []:
        name = obj["name"]
        obj_type = obj["type"]
        instance = int(obj["instance"])
        prop = obj.get("property", "presentValue")
        scale = float(obj.get("scale", 1.0))

        try:
            value = await asyncio.to_thread(reader, host, obj_type, instance, prop)
        except Exception as e:  # noqa: BLE001 — one bad read shouldn't kill the poll
            errors[name] = f"{type(e).__name__}: {e}"
            continue

        raw_bytes[name] = str(value)

        try:
            measurements[name] = float(value) * scale
        except (TypeError, ValueError) as e:
            errors[name] = f"Non-numeric value '{value}' from BACnet: {e}"

    return PollResult(
        device_id=device.device_id,
        timestamp_ns=time.time_ns(),
        measurements=measurements,
        raw_bytes=raw_bytes,
        protocol="bacnet_ip",
        source_ip=host,
        success=len(errors) == 0,
        errors=errors,
    )
