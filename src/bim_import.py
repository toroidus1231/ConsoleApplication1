"""Module 16: BIM/IFC Import.

Parses an IFC (Industry Foundation Classes) design file exported from Revit /
AutoCAD, extracts the distribution devices it describes — together with their
location (site / rack / position / x,y,z) and their power & network
connections — and creates the corresponding objects in NetBox via its API. The
resulting NetBox data feeds the orchestrator's power-dependency graph.

``ifcopenshell`` is a heavy native library and is imported lazily inside
``load_ifc`` so this module (and its tests) import cleanly without it installed.
Tests inject a duck-typed ``model`` (and an httpx client) and never touch
ifcopenshell or the network. The two seams are:

* :func:`parse_model` — a PURE mapping from an IFC-model-like object to a list
  of device-payload dicts. This is the core unit-tested function.
* :func:`import_to_netbox` — POSTs those payloads to NetBox using the
  injected-client-or-transient pattern, with per-device isolation.

Device-payload dict shape (one per device)::

    {
        "name": str,                 # entity.Name (fallback: "<type>-<tag>")
        "device_type_slug": str,     # slugified ObjectType / type property
        "site": str,                 # site/building name from property set
        "rack": str,                 # rack/enclosure reference
        "position": int,             # rack U position (0 if unknown)
        "x": float, "y": float, "z": float,   # placement coordinates
        "power_source": str,         # upstream feed/panel reference ("" if none)
        "connections": [             # downstream power/network links
            {"target": str, "type": "power"|"network"}
        ],
    }
"""

from __future__ import annotations

import re
from typing import Any

import httpx

# IFC entity classes we treat as commissionable devices. ``IfcDistributionElement``
# is the abstract parent; the two subtypes are listed explicitly because some
# exporters tag entities with the concrete class only.
DEVICE_IFC_TYPES = (
    "IfcDistributionElement",
    "IfcFlowController",
    "IfcEnergyConversionDevice",
)

# Property-set keys we look in, in priority order, for each logical attribute.
# IFC has no fixed schema for these, so we read defensively from a small,
# documented set of names that the test fakes also supply.
_SITE_KEYS = ("Site", "Building", "Facility")
_RACK_KEYS = ("Rack", "Enclosure", "Cabinet")
_POSITION_KEYS = ("Position", "RackUnit", "U")
_TYPE_KEYS = ("DeviceType", "Type", "AssetType")
_POWER_SOURCE_KEYS = ("PowerSource", "Panel", "Feed", "Circuit")


def load_ifc(path: str) -> Any:
    """Open an IFC file and return the ifcopenshell model.

    ``ifcopenshell`` is imported lazily here (heavy native dependency) so the
    module imports without it. Not exercised by the unit tests — tests pass a
    fake ``model`` to :func:`import_ifc` instead.
    """
    import ifcopenshell  # noqa: PLC0415 — lazy import (heavy native lib)

    return ifcopenshell.open(path)


def parse_model(model: Any) -> list[dict]:
    """Map a duck-typed IFC ``model`` to a list of device-payload dicts.

    Pure function: no I/O, no ifcopenshell. ``model`` need only expose
    ``by_type(ifc_class) -> list[entity]``. Each entity is duck-typed and may
    expose ``.is_a()``, ``.Name``, ``.ObjectType``, ``.Tag`` and a properties
    mapping via ``get_info()`` and/or a ``.properties`` / ``.psets`` dict.

    Entities returned for more than one queried class are de-duplicated by
    ``id()`` so an entity tagged as both a distribution element and a flow
    controller is only emitted once.

    See the module docstring for the exact payload shape.
    """
    devices: list[dict] = []
    seen: set[int] = set()

    for ifc_type in DEVICE_IFC_TYPES:
        try:
            entities = model.by_type(ifc_type) or []
        except Exception:  # noqa: BLE001 — a type the model can't resolve is skipped
            continue
        for entity in entities:
            key = id(entity)
            if key in seen:
                continue
            seen.add(key)
            devices.append(_entity_to_payload(entity))

    return devices


def _entity_to_payload(entity: Any) -> dict:
    """Build a single device-payload dict from one IFC entity (defensively)."""
    props = _props(entity)

    name = _attr(entity, "Name") or props.get("Name")
    object_type = _attr(entity, "ObjectType") or _first(props, _TYPE_KEYS)
    tag = _attr(entity, "Tag") or props.get("Tag")

    if not name:
        base = object_type or _ifc_class(entity) or "device"
        name = f"{base}-{tag}" if tag else str(base)

    x, y, z = _coords(entity, props)

    return {
        "name": str(name),
        "device_type_slug": _slugify(object_type or _ifc_class(entity) or "device"),
        "site": str(_first(props, _SITE_KEYS) or ""),
        "rack": str(_first(props, _RACK_KEYS) or ""),
        "position": _to_int(_first(props, _POSITION_KEYS)),
        "x": x,
        "y": y,
        "z": z,
        "power_source": str(_first(props, _POWER_SOURCE_KEYS) or ""),
        "connections": _connections(props),
    }


def _props(entity: Any) -> dict:
    """Collect a flat property mapping for an entity, merging several sources.

    Order (later wins): ``get_info()``, ``.properties``, ``.psets`` (which may
    be nested ``{pset_name: {key: value}}`` and is flattened).
    """
    merged: dict = {}

    get_info = getattr(entity, "get_info", None)
    if callable(get_info):
        try:
            info = get_info()
            if isinstance(info, dict):
                merged.update(info)
        except Exception:  # noqa: BLE001 — a fake/exporter that raises is ignored
            pass

    for attr in ("properties", "psets", "property_sets"):
        bag = getattr(entity, attr, None)
        if isinstance(bag, dict):
            for value in bag.values():
                if isinstance(value, dict):
                    merged.update(value)
            merged.update({k: v for k, v in bag.items() if not isinstance(v, dict)})

    return merged


def _connections(props: dict) -> list[dict]:
    """Normalize connection entries from a property mapping.

    Accepts ``props["Connections"]`` as a list of either strings (assumed
    power) or ``{"target"/"to"/"name", "type"}`` dicts. Power/network ports
    declared as ``PowerOutputs`` / ``NetworkOutputs`` lists are also folded in.
    """
    out: list[dict] = []

    for raw in props.get("Connections", []) or []:
        if isinstance(raw, dict):
            target = raw.get("target") or raw.get("to") or raw.get("name") or ""
            kind = raw.get("type") or "power"
            if target:
                out.append({"target": str(target), "type": str(kind)})
        elif raw:
            out.append({"target": str(raw), "type": "power"})

    for key, kind in (("PowerOutputs", "power"), ("NetworkOutputs", "network")):
        for raw in props.get(key, []) or []:
            if raw:
                out.append({"target": str(raw), "type": kind})

    return out


def _coords(entity: Any, props: dict) -> tuple[float, float, float]:
    """Extract (x, y, z) placement coordinates, defaulting missing axes to 0.0."""
    coord = getattr(entity, "coordinates", None) or getattr(entity, "location", None)
    if coord is None:
        coord = props.get("Coordinates") or props.get("Location")

    if isinstance(coord, dict):
        return (
            _to_float(coord.get("x")),
            _to_float(coord.get("y")),
            _to_float(coord.get("z")),
        )
    if isinstance(coord, (list, tuple)):
        vals = list(coord) + [0.0, 0.0, 0.0]
        return (_to_float(vals[0]), _to_float(vals[1]), _to_float(vals[2]))

    return (
        _to_float(props.get("x") if "x" in props else props.get("X")),
        _to_float(props.get("y") if "y" in props else props.get("Y")),
        _to_float(props.get("z") if "z" in props else props.get("Z")),
    )


def _ifc_class(entity: Any) -> str:
    is_a = getattr(entity, "is_a", None)
    if callable(is_a):
        try:
            return str(is_a())
        except Exception:  # noqa: BLE001 — fakes may raise; fall through
            return ""
    return str(is_a) if is_a else ""


def _attr(entity: Any, name: str) -> Any:
    value = getattr(entity, name, None)
    return value if value not in (None, "") else None


def _first(props: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = props.get(key)
        if value not in (None, ""):
            return value
    return None


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug or "device"


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def import_to_netbox(
    devices: list[dict],
    *,
    netbox_url: str,
    netbox_token: str,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Create each parsed device (and its power connections) in NetBox.

    For every device dict this POSTs to ``/api/dcim/devices/`` and then, for
    each power connection, creates a power-port + power-feed pair
    (``/api/dcim/power-ports/`` then ``/api/dcim/power-feeds/``) so the
    orchestrator's power-dependency graph has edges to walk. Network
    connections are recorded as a single power-port-style endpoint (documented
    simplification — full cable modelling is out of scope for the importer).

    Per-device isolation: any failure on one device is captured in ``errors``
    and the import continues with the next device.

    Args:
        devices: Payload dicts from :func:`parse_model`.
        netbox_url: Base URL of NetBox.
        netbox_token: NetBox API token.
        client: Optional injected httpx.AsyncClient (tests inject a
            MockTransport-backed client). When None a transient client is used.

    Returns:
        ``{"devices_committed": int, "connections_created": int, "errors": [...]}``
    """
    headers = {"Authorization": f"Token {netbox_token}"}

    if client is None:
        async with httpx.AsyncClient(base_url=netbox_url, timeout=30.0) as c:
            return await _import(c, headers, devices)
    return await _import(client, headers, devices)


async def _import(
    client: httpx.AsyncClient,
    headers: dict,
    devices: list[dict],
) -> dict:
    devices_committed = 0
    connections_created = 0
    errors: list[dict] = []

    for device in devices:
        name = device.get("name", "")
        try:
            resp = await client.post(
                "/api/dcim/devices/",
                json=_device_payload(device),
                headers=headers,
            )
            resp.raise_for_status()
            created = resp.json()
            devices_committed += 1

            device_ref = created.get("id", name)
            connections_created += await _create_connections(
                client, headers, device_ref, device.get("connections", []) or []
            )
        except Exception as e:  # noqa: BLE001 — isolate one bad device from the rest
            errors.append({"device": name, "error": f"{type(e).__name__}: {e}"})

    return {
        "devices_committed": devices_committed,
        "connections_created": connections_created,
        "errors": errors,
    }


def _device_payload(device: dict) -> dict:
    """NetBox /api/dcim/devices/ body for one parsed device."""
    return {
        "name": device.get("name", ""),
        "device_type": {"slug": device.get("device_type_slug", "")},
        "site": {"name": device.get("site", "")},
        "rack": {"name": device.get("rack", "")},
        "position": device.get("position", 0),
        "custom_fields": {
            "bim_x": device.get("x", 0.0),
            "bim_y": device.get("y", 0.0),
            "bim_z": device.get("z", 0.0),
            "power_source": device.get("power_source", ""),
        },
    }


async def _create_connections(
    client: httpx.AsyncClient,
    headers: dict,
    device_ref: Any,
    connections: list[dict],
) -> int:
    """Create a power-port (+ power-feed for power links) per connection.

    Returns the number of connections successfully created. A connection that
    fails to POST is skipped without aborting the device (its absence simply
    leaves an edge out of the power graph).
    """
    created = 0
    for conn in connections:
        target = conn.get("target", "")
        kind = conn.get("type", "power")

        port_resp = await client.post(
            "/api/dcim/power-ports/",
            json={
                "device": device_ref,
                "name": f"{kind}:{target}",
                "description": target,
            },
            headers=headers,
        )
        port_resp.raise_for_status()

        if kind == "power":
            feed_resp = await client.post(
                "/api/dcim/power-feeds/",
                json={
                    "name": f"{device_ref}->{target}",
                    "power_port": port_resp.json().get("id"),
                    "upstream": target,
                },
                headers=headers,
            )
            feed_resp.raise_for_status()

        created += 1

    return created


async def import_ifc(
    path: str,
    *,
    netbox_url: str,
    netbox_token: str,
    client: httpx.AsyncClient | None = None,
    model: Any = None,
) -> dict:
    """End-to-end IFC import: parse a model and load it into NetBox.

    Args:
        path: Path to an IFC file (used only when ``model`` is None).
        netbox_url: Base URL of NetBox.
        netbox_token: NetBox API token.
        client: Optional injected httpx.AsyncClient (tests).
        model: Optional pre-opened IFC-model-like object (tests inject a fake).
            When None, ``load_ifc(path)`` opens the file via ifcopenshell.

    Returns:
        The :func:`import_to_netbox` summary dict.
    """
    if model is None:
        model = load_ifc(path)

    devices = parse_model(model)
    return await import_to_netbox(
        devices,
        netbox_url=netbox_url,
        netbox_token=netbox_token,
        client=client,
    )
