"""Module 12: REST API Connectors — shared base.

Defines :class:`RestConnector`, the shared logic for every vendor connector.
A connector fetches recent events from an external REST API, normalizes each
raw event into an :class:`~src.types.AttestationRecord`, and submits each one
to the attestation engine.

The httpx client is dependency-injectable for tests, mirroring the
``netbox_reader`` pattern: an injected client is used as-is; otherwise a
transient ``httpx.AsyncClient`` is opened per call.

Per-event isolation: a single malformed event is skipped (its error is
captured) rather than aborting the whole poll. Successfully-normalized records
are collected, submitted, and returned.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

import httpx

from ..timeutil import ns_now
from ..types import AttestationRecord


@runtime_checkable
class AttestationLike(Protocol):
    """Anything with an async ``submit(AttestationRecord)`` — e.g. the
    Module 5 ``AttestationEngine``."""

    async def submit(self, record: AttestationRecord) -> None: ...


class RestConnector:
    """Base REST connector.

    Subclasses override :meth:`_endpoint` and :meth:`_normalize` (and may
    override :meth:`_headers` for a non-Bearer auth style). Everything else —
    the HTTP fetch, per-event isolation, submission, and the
    injected-client-or-transient seam — lives here.
    """

    #: Subclasses set the measurement name and the API path.
    name: str = "rest"
    endpoint_path: str = "/"

    def __init__(
        self,
        name: str,
        base_url: str,
        token: str,
        *,
        attestation: AttestationLike,
        client: httpx.AsyncClient | None = None,
    ):
        self.name = name
        self.base_url = base_url
        self.token = token
        self.attestation = attestation
        self._client = client
        # The API host, recorded as source_ip on every emitted record.
        self.source_ip = urlsplit(base_url).hostname or ""
        # Per-poll error log; cleared at the start of each poll().
        self.errors: list[dict] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def poll(self) -> list[AttestationRecord]:
        """Fetch recent events, normalize each, submit each, return the list.

        A malformed event is skipped (recorded in ``self.errors``) so one bad
        event never aborts the whole poll.
        """
        self.errors = []
        raw_events = await self._fetch_events()

        records: list[AttestationRecord] = []
        for raw in raw_events:
            try:
                record = self._normalize(raw)
            except Exception as exc:  # per-event isolation
                self.errors.append({"event": raw, "error": repr(exc)})
                continue
            records.append(record)

        for record in records:
            await self.attestation.submit(record)

        return records

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    async def _fetch_events(self) -> list[dict]:
        """GET the connector's endpoint and return the list of raw events.

        Uses the injected client if present, else a transient one (same
        pattern as ``netbox_reader``).
        """
        if self._client is None:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=30.0) as c:
                return await self._get(c)
        return await self._get(self._client)

    async def _get(self, client: httpx.AsyncClient) -> list[dict]:
        resp = await client.get(self._endpoint(), headers=self._headers())
        resp.raise_for_status()
        return self._extract_events(resp.json())

    def _headers(self) -> dict:
        """Default auth style: ``Authorization: Bearer <token>``.

        Subclasses may override (e.g. Basic auth, vendor token headers)."""
        return {"Authorization": f"Bearer {self.token}"}

    # ------------------------------------------------------------------
    # Overridable per subclass
    # ------------------------------------------------------------------
    def _endpoint(self) -> str:
        """API path to GET. Subclasses override (or set ``endpoint_path``)."""
        return self.endpoint_path

    def _extract_events(self, payload: Any) -> list[dict]:
        """Pull the list of raw events out of the decoded JSON body.

        Default handles a bare list or a common ``{"results"|"events": [...]}``
        envelope. Subclasses override for other shapes.
        """
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("results", "events", "data", "items"):
                val = payload.get(key)
                if isinstance(val, list):
                    return val
        return []

    def _normalize(self, raw: dict) -> AttestationRecord:  # pragma: no cover
        """Map a vendor event to an :class:`AttestationRecord`.

        Must be overridden by every concrete connector.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Helpers for subclasses
    # ------------------------------------------------------------------
    def _record(
        self,
        *,
        device_id: str,
        measurement: str,
        value: float,
        raw: dict,
        timestamp_ns: int | None = None,
    ) -> AttestationRecord:
        """Construct an AttestationRecord with the connector's shared fields.

        ``timestamp_ns`` falls back to :func:`ns_now` when the event carries no
        usable time. ``worker_id`` is left empty for the engine to fill;
        ``test_id`` is None. ``raw_bytes`` is a compact JSON dump of the event.
        """
        return AttestationRecord(
            timestamp_ns=timestamp_ns if timestamp_ns is not None else ns_now(),
            device_id=device_id,
            measurement=measurement,
            value=float(value),
            raw_bytes=json.dumps(raw, separators=(",", ":"), sort_keys=True, default=str),
            protocol="rest_api",
            source_ip=self.source_ip,
            worker_id="",  # filled by the attestation engine
            test_id=None,
        )
