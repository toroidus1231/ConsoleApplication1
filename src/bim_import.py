"""Module 16: BIM/IFC Import.

Parses an IFC file (Revit/AutoCAD export) and extracts the devices, racks,
cable runs, and power paths that the design specifies. Each parsed device is
turned into a NetBox device + (where applicable) power-port connection so the
orchestrator (Module 13) and reconciliation engine (Module 14) can use it.

ifcopenshell is heavyweight and platform-specific; tests inject an
``ifc_parser`` callable that yields ParsedEntity records, so neither
ifcopenshell nor a real .ifc file is needed for unit tests.

Pipeline:
    parse(file_path) → List[ParsedEntity]
        → group into ParsedDesign (devices, connections)
        → commit_to_netbox(design, netbox_client) → counts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

import httpx


@dataclass
class ParsedEntity:
    """A device extracted from an IFC IfcDistributionElement / similar."""

    ifc_guid: str
    name: str
    device_type_slug: str
    site: str = ""
    rack: str = ""
    position: int = 0
    coordinates: tuple[float, float, float] | None = None
    # Power wiring. ``power_source_guid`` references another ParsedEntity's
    # ifc_guid — the BIM import has to resolve them after the full file is
    # parsed.
    power_source_guid: str | None = None


@dataclass
class ParsedDesign:
    devices: list[ParsedEntity]
    # Edges are (powered_guid, source_guid) and only present when both ends
    # are in `devices`.
    power_connections: list[tuple[str, str]] = field(default_factory=list)


IFCParser = Callable[[str], Iterable[ParsedEntity]]


def parse_design(file_path: str, *, parser: IFCParser) -> ParsedDesign:
    """Drive the injected parser, then resolve power_source_guid references
    against the parsed entity set so we only emit edges to known devices."""
    devices = list(parser(file_path))
    guids = {d.ifc_guid for d in devices}
    connections: list[tuple[str, str]] = []
    for d in devices:
        if d.power_source_guid and d.power_source_guid in guids:
            connections.append((d.ifc_guid, d.power_source_guid))
    return ParsedDesign(devices=devices, power_connections=connections)


@dataclass
class CommitCounts:
    devices_created: int = 0
    devices_updated: int = 0
    connections_created: int = 0
    errors: list[str] = field(default_factory=list)


async def commit_to_netbox(
    design: ParsedDesign,
    netbox_url: str,
    netbox_token: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> CommitCounts:
    """POST every parsed device into NetBox and create power-port connections.

    Each device is posted to /api/dcim/devices/. If the device already exists
    (matched by ``custom_fields.ifc_guid``), it's PATCH'd instead of recreated.
    The httpx client is injected for tests.
    """
    headers = {"Authorization": f"Token {netbox_token}"}
    counts = CommitCounts()

    if client is None:
        async with httpx.AsyncClient(base_url=netbox_url, timeout=30.0) as c:
            return await _do_commit(c, headers, design, counts)
    return await _do_commit(client, headers, design, counts)


async def _do_commit(
    client: httpx.AsyncClient,
    headers: dict,
    design: ParsedDesign,
    counts: CommitCounts,
) -> CommitCounts:
    guid_to_netbox_id: dict[str, str] = {}

    for entity in design.devices:
        try:
            existing = await _find_by_ifc_guid(client, headers, entity.ifc_guid)
            if existing is None:
                created = await _create_device(client, headers, entity)
                guid_to_netbox_id[entity.ifc_guid] = str(created["id"])
                counts.devices_created += 1
            else:
                await _update_device(client, headers, existing["id"], entity)
                guid_to_netbox_id[entity.ifc_guid] = str(existing["id"])
                counts.devices_updated += 1
        except Exception as e:  # noqa: BLE001
            counts.errors.append(f"{entity.ifc_guid}: {type(e).__name__}: {e}")

    for child_guid, parent_guid in design.power_connections:
        child_id = guid_to_netbox_id.get(child_guid)
        parent_id = guid_to_netbox_id.get(parent_guid)
        if not (child_id and parent_id):
            continue
        try:
            await _create_power_connection(client, headers, child_id, parent_id)
            counts.connections_created += 1
        except Exception as e:  # noqa: BLE001
            counts.errors.append(
                f"{child_guid}→{parent_guid}: {type(e).__name__}: {e}"
            )

    return counts


# ----------------------------------------------------------------------
# NetBox HTTP helpers
# ----------------------------------------------------------------------


async def _find_by_ifc_guid(client, headers, ifc_guid: str) -> dict | None:
    resp = await client.get(
        "/api/dcim/devices/",
        params={"cf_ifc_guid": ifc_guid},
        headers=headers,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return results[0] if results else None


async def _create_device(client, headers, entity: ParsedEntity) -> dict:
    payload = _entity_payload(entity)
    resp = await client.post("/api/dcim/devices/", json=payload, headers=headers)
    resp.raise_for_status()
    return resp.json()


async def _update_device(client, headers, device_id, entity: ParsedEntity) -> dict:
    payload = _entity_payload(entity)
    resp = await client.patch(f"/api/dcim/devices/{device_id}/", json=payload, headers=headers)
    resp.raise_for_status()
    return resp.json()


async def _create_power_connection(client, headers, child_id, parent_id) -> dict:
    payload = {
        "termination_a_type": "dcim.poweroutlet",
        "termination_a_id": parent_id,
        "termination_b_type": "dcim.powerport",
        "termination_b_id": child_id,
    }
    resp = await client.post("/api/dcim/cables/", json=payload, headers=headers)
    resp.raise_for_status()
    return resp.json()


def _entity_payload(entity: ParsedEntity) -> dict:
    payload = {
        "name": entity.name,
        "device_type": {"slug": entity.device_type_slug},
        "site": {"name": entity.site} if entity.site else None,
        "rack": {"name": entity.rack} if entity.rack else None,
        "position": entity.position or None,
        "custom_fields": {"ifc_guid": entity.ifc_guid},
    }
    return {k: v for k, v in payload.items() if v is not None}
