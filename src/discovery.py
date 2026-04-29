"""Module 11: Discovery Scanner.

Sweeps a CIDR range, probes each IP for SNMP / Modbus / BACnet / Redfish in
parallel, and classifies responders against the NetBox device-type catalog.

Spec references:
  - §5.5: parallel probes with ``workers.max_concurrent_polls`` semaphore;
    a /16 takes ~110 minutes at 50 concurrent.
  - 5-second per-IP overall timeout.
  - Classification: case-insensitive substring of identity string against
    ``manufacturer + model``. Prefer exact match over substring.
  - Unmatched IPs get placeholder device_type_slug="unknown".

Tests inject ``prober`` and ``device_type_catalog`` callables so neither
the network nor NetBox is needed.
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass
from typing import Awaitable, Callable, Sequence


PROBE_TIMEOUT_SECONDS = 5.0


@dataclass
class ProbeResponse:
    """One protocol probe's outcome for an IP."""

    ip: str
    protocol: str  # modbus_tcp | snmp | bacnet_ip | redfish
    identity: str  # manufacturer-supplied identity string


@dataclass
class DiscoveredDevice:
    ip: str
    protocol: str
    identity: str
    device_type_slug: str  # "unknown" when no match
    matched_exact: bool = False


# A Prober probes ALL protocols for a given IP and returns the responses
# that came back (possibly empty). One IP may respond on multiple protocols.
Prober = Callable[[str], Awaitable[list[ProbeResponse]]]

# A DeviceTypeCatalog returns a list of NetBox device types as
# (slug, manufacturer, model) tuples — usually fetched from NetBox once at
# scan start.
DeviceTypeCatalog = Callable[[], Awaitable[Sequence[tuple[str, str, str]]]]


class DiscoveryScanner:
    def __init__(
        self,
        config,
        *,
        prober: Prober,
        device_type_catalog: DeviceTypeCatalog,
    ):
        self.config = config
        self._prober = prober
        self._catalog = device_type_catalog

    async def scan(self, cidr: str) -> list[DiscoveredDevice]:
        """Scan a CIDR range, return DiscoveredDevice for every responder."""
        ips = _iter_cidr(cidr)
        catalog = await self._catalog()
        sem = asyncio.Semaphore(self.config.max_concurrent_polls)
        per_ip = await asyncio.gather(
            *(self._probe_ip(sem, ip, catalog) for ip in ips)
        )
        # Each IP may yield multiple devices (one per responding protocol).
        flat: list[DiscoveredDevice] = []
        for devices in per_ip:
            flat.extend(devices)
        return flat

    async def _probe_ip(
        self,
        sem: asyncio.Semaphore,
        ip: str,
        catalog: Sequence[tuple[str, str, str]],
    ) -> list[DiscoveredDevice]:
        async with sem:
            try:
                responses = await asyncio.wait_for(
                    self._prober(ip), timeout=PROBE_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                return []
            except Exception:  # noqa: BLE001 — one bad IP shouldn't halt the scan
                return []

        out: list[DiscoveredDevice] = []
        for resp in responses:
            slug, exact = classify(resp.identity, catalog)
            out.append(
                DiscoveredDevice(
                    ip=resp.ip,
                    protocol=resp.protocol,
                    identity=resp.identity,
                    device_type_slug=slug,
                    matched_exact=exact,
                )
            )
        return out


def classify(
    identity: str, catalog: Sequence[tuple[str, str, str]]
) -> tuple[str, bool]:
    """Classify an identity string against ``catalog``.

    Returns ``(slug, was_exact_match)``. Slug is "unknown" when nothing matches.

    Match algorithm (per spec §5.5):
      1. Exact (case-insensitive) match of identity against
         "<manufacturer> <model>". If found, return that slug with exact=True.
      2. Otherwise, the catalog entry whose "<manufacturer> <model>" appears
         as a substring of the identity (case-insensitive). Prefer the
         longest-matching entry to avoid generic vendor name collisions.
      3. Otherwise, "unknown", exact=False.
    """
    needle = identity.casefold()
    exact_slug: str | None = None
    best_substring: tuple[str, int] | None = None  # (slug, length)

    for slug, manufacturer, model in catalog:
        haystack = f"{manufacturer} {model}".strip().casefold()
        if not haystack:
            continue
        if haystack == needle:
            exact_slug = slug
            break
        if haystack in needle:
            length = len(haystack)
            if best_substring is None or length > best_substring[1]:
                best_substring = (slug, length)

    if exact_slug:
        return exact_slug, True
    if best_substring:
        return best_substring[0], False
    return "unknown", False


def _iter_cidr(cidr: str) -> list[str]:
    """Expand ``cidr`` to a list of host IP strings.

    For /32 (single host), returns the single address. Otherwise excludes
    the network address and broadcast (network.hosts()).
    """
    net = ipaddress.ip_network(cidr, strict=False)
    if net.num_addresses == 1:
        return [str(net.network_address)]
    return [str(h) for h in net.hosts()]
