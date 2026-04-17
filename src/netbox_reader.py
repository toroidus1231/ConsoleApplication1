"""Module 2: NetBox Device Reader.

Queries NetBox for all active devices whose Config Context declares a given
protocol. Returns a list of DeviceInfo suitable for passing to a poller.

Handles NetBox API pagination transparently.
"""

from __future__ import annotations

import httpx

from .types import DeviceInfo


async def get_devices_by_protocol(
    netbox_url: str,
    netbox_token: str,
    protocol: str,
    *,
    client: httpx.AsyncClient | None = None,
    page_size: int = 1000,
) -> list[DeviceInfo]:
    """Return DeviceInfo for every active NetBox device whose Config Context
    has ``protocol == <protocol>``.

    Args:
        netbox_url: Base URL of NetBox.
        netbox_token: NetBox API token.
        protocol: Protocol to filter by (e.g. "modbus_tcp").
        client: Optional injected httpx.AsyncClient (for tests).
        page_size: Results per page. NetBox default is 50; we use 1000.
    """
    headers = {"Authorization": f"Token {netbox_token}"}

    if client is None:
        async with httpx.AsyncClient(base_url=netbox_url, timeout=30.0) as c:
            return await _fetch(c, headers, protocol, page_size)
    return await _fetch(client, headers, protocol, page_size)


async def _fetch(
    client: httpx.AsyncClient,
    headers: dict,
    protocol: str,
    page_size: int,
) -> list[DeviceInfo]:
    devices: list[DeviceInfo] = []
    url = "/api/dcim/devices/"
    params: dict | None = {"status": "active", "limit": page_size}

    while url:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        for raw in data.get("results", []):
            info = _to_device_info(raw, protocol)
            if info is not None:
                devices.append(info)

        # NetBox returns a fully-qualified URL in "next". After the first page,
        # params are embedded in that URL, so clear them.
        url = data.get("next")
        params = None

    return devices


def _to_device_info(raw: dict, protocol: str) -> DeviceInfo | None:
    ctx = raw.get("config_context") or {}
    if ctx.get("protocol") != protocol:
        return None

    primary_ip_raw = (raw.get("primary_ip4") or {}).get("address") or ""
    primary_ip = primary_ip_raw.split("/")[0] if primary_ip_raw else ""
    if not primary_ip:
        primary_ip = (ctx.get("connection") or {}).get("host", "")

    device_type = raw.get("device_type") or {}
    site = raw.get("site") or {}
    rack = raw.get("rack") or {}

    return DeviceInfo(
        device_id=str(raw["id"]),
        name=raw.get("name") or "",
        primary_ip=primary_ip,
        device_type_slug=device_type.get("slug", ""),
        config_context=ctx,
        protocol=protocol,
        site=site.get("name", "") if isinstance(site, dict) else "",
        rack=rack.get("name", "") if isinstance(rack, dict) else "",
        position=int(raw.get("position") or 0),
    )
