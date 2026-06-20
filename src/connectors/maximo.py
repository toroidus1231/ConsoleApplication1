"""Module 12: REST API Connectors — IBM Maximo (CMMS work orders).

Maximo exposes work orders via its REST object structure ``mxwo`` at
``/maximo/api/os/mxwo``. We normalize each work order to an
``AttestationRecord`` with measurement ``"work_order_status"``.

Maximo wraps results in a ``{"member": [...]}`` envelope and authenticates via
the ``maxauth`` (or ``apikey``) header rather than ``Authorization``; here the
injected ``token`` is sent as ``apikey``.

Event time is taken from the work order's ``statusdate`` (ISO-8601, UTC) when
present, else ``ns_now()``. ``device_id`` is the work order number (``wonum``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..types import AttestationRecord
from .base import RestConnector

# Numeric encoding for ``value`` (measurement "work_order_status").
# Mirrors Maximo's WORKORDER.status synonyms:
#   WAPPR -> 0.0   (waiting on approval)
#   APPR  -> 1.0   (approved)
#   INPRG -> 2.0   (in progress)
#   COMP  -> 3.0   (completed)
#   CLOSE -> 4.0   (closed)
#   CAN   -> -1.0  (cancelled)
# Unknown statuses map to ``_UNKNOWN``.
_STATUS_CODES: dict[str, float] = {
    "WAPPR": 0.0,
    "APPR": 1.0,
    "INPRG": 2.0,
    "COMP": 3.0,
    "CLOSE": 4.0,
    "CAN": -1.0,
}
_UNKNOWN = -99.0


class MaximoConnector(RestConnector):
    """IBM Maximo CMMS work-order connector."""

    endpoint_path = "/maximo/api/os/mxwo"

    def __init__(self, base_url: str, token: str, **kwargs):
        super().__init__("maximo", base_url, token, **kwargs)

    def _headers(self) -> dict:
        # Maximo authenticates via apikey, not a Bearer Authorization header.
        return {"apikey": self.token, "Accept": "application/json"}

    def _extract_events(self, payload: Any) -> list[dict]:
        if isinstance(payload, dict) and isinstance(payload.get("member"), list):
            return payload["member"]
        return super()._extract_events(payload)

    def _normalize(self, raw: dict) -> AttestationRecord:
        status = str(raw["status"])  # KeyError -> skipped by base poll()
        device_id = str(raw.get("wonum") or raw.get("assetnum") or "")
        value = _STATUS_CODES.get(status.upper(), _UNKNOWN)
        return self._record(
            device_id=device_id,
            measurement="work_order_status",
            value=value,
            raw=raw,
            timestamp_ns=_parse_ts(raw.get("statusdate")),
        )


def _parse_ts(value) -> int | None:
    """Parse a Maximo ISO-8601 timestamp to UTC nanoseconds, or None."""
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1e9)
