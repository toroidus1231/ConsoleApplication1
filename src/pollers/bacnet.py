"""Module 7: BACnet/IP Poller.

Reads every object defined in a device's Config Context using BAC0. BAC0's
``read`` takes a single request string of the form ``"IP objectType instance
property"`` (e.g. ``"10.0.0.5 analogInput 1 presentValue"``) and returns the
property value synchronously. We wrap each (blocking) read in
``asyncio.to_thread`` so the poller stays cooperative under the platform's async
event loop. Detects NaN/Inf on float values (usually a misconfigured object or a
device fault) and reports them as per-object errors rather than crashing.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Protocol

from ..types import DeviceInfo, PollResult


class BacnetClientLike(Protocol):
    def read(self, request: str) -> Any: ...


# Lazily-built default client (BAC0.lite() opens a UDP socket / native stack).
# Cached so repeated polls reuse one BACnet application instance.
_bacnet: BacnetClientLike | None = None


def _default_client() -> BacnetClientLike:
    """Build (and cache) the default BAC0 client.

    BAC0 is a heavy native dependency, so it is imported lazily here — only when
    no client is injected. Tests always inject a fake client and therefore never
    import BAC0.
    """
    global _bacnet
    if _bacnet is None:
        import BAC0  # noqa: PLC0415 — lazy: BAC0 is a heavy optional native dep

        _bacnet = BAC0.lite()
    return _bacnet


async def poll_device(
    device: DeviceInfo,
    *,
    client: BacnetClientLike | None = None,
) -> PollResult:
    """Poll all objects defined in ``device.config_context``.

    Args:
        device: Device to poll.
        client: Optional pre-built BACnet client (tests inject a fake here).
            When None, a BAC0 ``lite`` client is constructed lazily. ``client.read``
            is synchronous and is always invoked via ``asyncio.to_thread``.
    """
    conn = device.config_context.get("connection", {}) or {}
    host = device.primary_ip or conn.get("host", "")

    if client is None:
        client = _default_client()

    measurements: dict = {}
    raw_bytes: dict = {}
    errors: dict = {}

    for obj in device.config_context.get("objects", []) or []:
        name = obj["name"]
        try:
            value, raw, err = await _read_object(client, host, obj)
            if err is not None:
                errors[name] = err
                continue
            raw_bytes[name] = raw
            measurements[name] = value
        except Exception as e:  # noqa: BLE001 — any read/parse failure is per-object
            errors[name] = f"{type(e).__name__}: {e}"

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


async def _read_object(
    client: BacnetClientLike,
    host: str,
    obj: dict,
) -> tuple[Any, str, str | None]:
    """Read a single BACnet object. Returns (value, raw_string, error)."""
    obj_type = obj["type"]
    instance = obj["instance"]
    prop = obj.get("property", "presentValue")
    scale = float(obj.get("scale", 1.0))

    request = f"{host} {obj_type} {instance} {prop}"
    val = await asyncio.to_thread(client.read, request)

    raw = str(val)
    value = float(val)

    if math.isnan(value) or math.isinf(value):
        return (
            None,
            raw,
            f"NaN/Inf detected. Likely a misconfigured object or device fault. Raw: {raw}",
        )

    value = value * scale

    return value, raw, None
