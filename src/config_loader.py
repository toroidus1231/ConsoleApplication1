"""Module 1: Config Context Loader.

Reads a Config Context JSON file from disk and POSTs it into NetBox, attached
to a device type. See spec §2 for the schema this file must conform to.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx


async def load_config_context(
    netbox_url: str,
    netbox_token: str,
    json_path: str | Path,
    device_type_slug: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Load a Config Context JSON file into NetBox for a device type.

    Args:
        netbox_url: Base URL of NetBox, e.g. http://netbox:8080
        netbox_token: NetBox API token.
        json_path: Path to a JSON file matching the Config Context schema.
        device_type_slug: The device type slug this context attaches to.
        client: Optional pre-built AsyncClient (used by tests). If None, a
            transient client is created.

    Returns:
        The NetBox Config Context object that was created (as a dict).

    Raises:
        FileNotFoundError: If `json_path` does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        ValueError: If the device type slug is not found in NetBox.
        httpx.HTTPStatusError: On non-2xx responses from NetBox.
    """
    path = Path(json_path)
    with open(path) as f:
        config_data = json.load(f)

    headers = {"Authorization": f"Token {netbox_token}"}

    if client is None:
        async with httpx.AsyncClient(base_url=netbox_url, timeout=30.0) as c:
            return await _load(c, headers, config_data, device_type_slug, path.stem)
    return await _load(client, headers, config_data, device_type_slug, path.stem)


async def _load(
    client: httpx.AsyncClient,
    headers: dict,
    config_data: dict,
    device_type_slug: str,
    name_hint: str,
) -> dict:
    resp = await client.get(
        "/api/dcim/device-types/",
        params={"slug": device_type_slug},
        headers=headers,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        raise ValueError(f"Device type '{device_type_slug}' not found in NetBox")
    device_type_id = results[0]["id"]

    payload = {
        "name": f"{device_type_slug}-config",
        "weight": 1000,
        "description": f"Loaded from {name_hint}",
        "data": config_data,
        "device_types": [device_type_id],
        "is_active": True,
    }
    resp = await client.post(
        "/api/extras/config-contexts/",
        json=payload,
        headers=headers,
    )
    resp.raise_for_status()
    return resp.json()
