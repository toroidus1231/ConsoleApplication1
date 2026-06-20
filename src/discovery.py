"""Module 11: Discovery Scanner.

Probes a range of IPs in parallel across four protocols (SNMP, Modbus TCP,
BACnet/IP, Redfish), extracts a device identity string from each response, and
classifies it against NetBox device types by case-insensitive substring match
(preferring exact matches). Unmatched responses are flagged for manual review.

Contracts spec §5.5. Network probes are injected so the scan is fully testable
without touching a wire; CIDR expansion and classification are pure functions.
"""

from __future__ import annotations

import asyncio
import ipaddress
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import httpx

# A probe takes a target IP and returns an identity string (or None on no/failed
# response). Keyed in the ``probes`` dict by protocol name.
ProbeFn = Callable[[str], Awaitable["str | None"]]

PROTOCOLS = ("snmp", "modbus_tcp", "bacnet_ip", "redfish")

# Default TCP ports per protocol (contracts spec §5.5). Used only by the default
# stub probes; tests inject their own fakes and never touch these.
DEFAULT_PORTS = {
    "snmp": 161,
    "modbus_tcp": 502,
    "bacnet_ip": 47808,
    "redfish": 443,
}


@dataclass
class DiscoveryResult:
    scan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    devices_found: int = 0
    devices_classified: int = 0
    devices_unmatched: int = 0
    errors: list = field(default_factory=list)
    devices: list = field(default_factory=list)


def expand_subnets(subnets: list[str]) -> list[str]:
    """Expand a list of CIDR strings (or bare IPs) into individual host IPs.

    Uses the stdlib ``ipaddress`` module. For a network, every usable host
    address is returned (``.hosts()``); a /32 (or bare IP) yields itself.
    """
    ips: list[str] = []
    seen: set[str] = set()
    for entry in subnets:
        entry = entry.strip()
        if not entry:
            continue
        if "/" in entry:
            net = ipaddress.ip_network(entry, strict=False)
            hosts = list(net.hosts())
            # A single-host network (/32, /128) has no .hosts(); use its address.
            candidates = hosts or [net.network_address]
        else:
            candidates = [ipaddress.ip_address(entry)]
        for addr in candidates:
            ip = str(addr)
            if ip not in seen:
                seen.add(ip)
                ips.append(ip)
    return ips


def _manufacturer_name(dt: dict) -> str:
    """Pull the manufacturer name out of a NetBox device-type dict.

    Handles both the nested form ``{"manufacturer": {"name": ...}}`` and the
    flattened form ``{"manufacturer": "..."}``.
    """
    manu = dt.get("manufacturer")
    if isinstance(manu, dict):
        return manu.get("name", "") or ""
    if isinstance(manu, str):
        return manu
    return ""


def _device_type_label(dt: dict) -> str:
    """Build the match target for a device type: "<manufacturer> <model>"."""
    manu = _manufacturer_name(dt).strip()
    model = (dt.get("model") or dt.get("display") or "").strip()
    return f"{manu} {model}".strip()


def classify(identity: str, device_types: list[dict]) -> str | None:
    """Classify an identity string against NetBox device types.

    Match is a case-insensitive substring test of the identity against each
    device type's "<manufacturer> <model>" label. An exact (case-insensitive)
    match is preferred over a substring match. Returns the matched device type
    slug, or None if nothing matches.
    """
    if not identity:
        return None
    ident_lower = identity.strip().lower()

    substring_slug: str | None = None
    for dt in device_types:
        label = _device_type_label(dt).lower()
        if not label:
            continue
        slug = dt.get("slug")
        if not slug:
            continue
        if label == ident_lower:
            return slug  # exact match always wins
        if substring_slug is None and (label in ident_lower or ident_lower in label):
            substring_slug = slug

    return substring_slug


async def _default_probe(protocol: str, ip: str) -> str | None:
    """Stub real probe: attempt a bare TCP connect to the protocol's port.

    This is intentionally minimal — it returns None whether or not the connect
    succeeds, because constructing a real protocol identity is out of scope for
    a connectivity check. Tests inject fakes and never reach this code.
    """
    port = DEFAULT_PORTS[protocol]
    try:
        reader, writer = await asyncio.open_connection(ip, port)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001 — close failures are non-fatal
            pass
    except Exception:  # noqa: BLE001 — unreachable host is the common case
        return None
    return None


def default_probes() -> dict[str, ProbeFn]:
    """Build the default (real) probe table. Not exercised by tests."""
    return {proto: (lambda ip, p=proto: _default_probe(p, ip)) for proto in PROTOCOLS}


async def _fetch_device_types(
    client: httpx.AsyncClient,
    headers: dict,
) -> list[dict]:
    """Fetch all NetBox device types, following pagination like netbox_reader."""
    device_types: list[dict] = []
    url = "/api/dcim/device-types/"
    params: dict | None = {"limit": 1000}

    while url:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        device_types.extend(data.get("results", []))
        # After page 1 the "next" URL embeds params; clear them (see netbox_reader).
        url = data.get("next")
        params = None

    return device_types


async def _probe_ip(
    ip: str,
    protocol: str,
    probe: ProbeFn,
    device_types: list[dict],
    timeout: float,
    semaphore: asyncio.Semaphore,
    errors: list,
) -> dict | None:
    """Run one probe against one IP and build its device record (or None).

    Failures and timeouts are captured into ``errors`` and never propagate.
    """
    async with semaphore:
        try:
            identity = await asyncio.wait_for(probe(ip), timeout=timeout)
        except Exception as e:  # noqa: BLE001 — any probe failure is captured, not fatal
            errors.append(
                {"ip": ip, "protocol": protocol, "error": f"{type(e).__name__}: {e}"}
            )
            return None

    if identity is None:
        return None  # no response on this protocol for this IP

    slug = classify(identity, device_types)
    if slug is None:
        return {
            "ip": ip,
            "protocol": protocol,
            "identity": identity,
            "device_type_slug": "unknown",
            "classified": False,
        }
    return {
        "ip": ip,
        "protocol": protocol,
        "identity": identity,
        "device_type_slug": slug,
        "classified": True,
    }


async def scan(
    subnets: list[str],
    *,
    probes: dict[str, ProbeFn],
    device_types: list[dict] | None = None,
    netbox_url: str = "",
    netbox_token: str = "",
    netbox_client: httpx.AsyncClient | None = None,
    max_concurrent: int = 50,
    timeout: float = 5.0,
) -> DiscoveryResult:
    """Scan ``subnets`` across all injected protocol probes and classify hits.

    Args:
        subnets: CIDRs (or bare IPs) to expand and probe.
        probes: Protocol-name -> probe coroutine. Each returns an identity
            string or None. Tests inject fakes; production uses default_probes().
        device_types: NetBox device types to classify against. If None, they are
            fetched from NetBox using ``netbox_client`` (or a fresh client built
            from ``netbox_url``/``netbox_token``).
        netbox_url: Base URL of NetBox (used only when device_types is None).
        netbox_token: NetBox API token (used only when device_types is None).
        netbox_client: Optional injected httpx.AsyncClient (for tests).
        max_concurrent: Max concurrent probes (asyncio semaphore bound).
        timeout: Per-probe timeout in seconds (injectable so tests run instantly).

    Returns:
        A DiscoveryResult. A single IP may yield multiple device records — one
        per protocol that responds, each classified independently.
    """
    if device_types is None:
        headers = {"Authorization": f"Token {netbox_token}"}
        if netbox_client is None:
            async with httpx.AsyncClient(base_url=netbox_url, timeout=30.0) as c:
                device_types = await _fetch_device_types(c, headers)
        else:
            device_types = await _fetch_device_types(netbox_client, headers)

    ips = expand_subnets(subnets)
    semaphore = asyncio.Semaphore(max_concurrent)
    errors: list = []

    tasks = [
        _probe_ip(ip, protocol, probe, device_types, timeout, semaphore, errors)
        for ip in ips
        for protocol, probe in probes.items()
    ]

    records = await asyncio.gather(*tasks)
    devices = [r for r in records if r is not None]

    classified = sum(1 for r in devices if r["classified"])
    unmatched = len(devices) - classified

    return DiscoveryResult(
        scan_id=str(uuid.uuid4()),
        devices_found=len(devices),
        devices_classified=classified,
        devices_unmatched=unmatched,
        errors=errors,
        devices=devices,
    )
