"""Module 12 base: REST API connector pattern.

Each external system (Genetec / Milestone / ServiceNow / Maximo) gets its own
connector that fetches events from a vendor API and normalizes them into
``AttestationRecord`` instances submitted to the attestation queue.

The base class handles HTTP, auth, error capture, and attestation submission.
Subclasses override ``endpoint`` and ``parse_events`` to handle the vendor's
JSON shape.

Tests inject an httpx.AsyncClient (via httpx.MockTransport) and a fake
attestation engine, so no external services are ever called.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, Protocol

import httpx

from ..types import AttestationRecord


@dataclass
class NormalizedEvent:
    """Vendor-agnostic event shape produced by parse_events()."""

    timestamp_ns: int
    measurement: str
    value: float
    raw_bytes: str
    source_ip: str


class AttestationLike(Protocol):
    async def submit(self, record: AttestationRecord) -> None: ...


class RESTConnector:
    """Base class. Subclass for each vendor."""

    protocol: str = "rest_api"

    def __init__(
        self,
        url: str,
        token: str,
        attestation: AttestationLike,
        *,
        device_id: str = "",
        client: httpx.AsyncClient | None = None,
    ):
        if not url:
            raise ValueError("url is required")
        self.url = url.rstrip("/")
        self.token = token
        self.attestation = attestation
        self.device_id = device_id or self.__class__.__name__.lower()
        self._client = client

    async def fetch_and_attest(self, **query_params) -> int:
        """Fetch events, normalize them, attest each. Returns count attested."""
        raw = await self._http_get(self.endpoint(), query_params)
        events = list(self.parse_events(raw))
        for event in events:
            await self.attestation.submit(
                AttestationRecord(
                    timestamp_ns=event.timestamp_ns,
                    device_id=self.device_id,
                    measurement=event.measurement,
                    value=event.value,
                    raw_bytes=event.raw_bytes,
                    protocol=self.protocol,
                    source_ip=event.source_ip,
                    worker_id="",
                )
            )
        return len(events)

    # Subclass hooks ----------------------------------------------------
    def endpoint(self) -> str:
        raise NotImplementedError

    def parse_events(self, body: dict) -> Iterable[NormalizedEvent]:
        raise NotImplementedError

    # HTTP --------------------------------------------------------------
    async def _http_get(self, path: str, params: dict) -> dict:
        headers = self._headers()
        full_url = self.url + path
        if self._client is not None:
            resp = await self._client.get(full_url, headers=headers, params=params)
        else:
            async with httpx.AsyncClient(timeout=15.0) as c:
                resp = await c.get(full_url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}


def _now_ns() -> int:
    return time.time_ns()
