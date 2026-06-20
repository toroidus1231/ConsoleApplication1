"""Module 12: REST API Connectors — ServiceNow (change management).

ServiceNow exposes change requests via the Table API at
``/api/now/table/change_request``. We normalize each change request to an
``AttestationRecord`` with measurement ``"change_state"``.

ServiceNow's Table API wraps rows in a ``{"result": [...]}`` envelope and uses
HTTP Basic auth (``Authorization: Basic ...``); here the injected ``token`` is
the pre-encoded Basic credential, mirroring how callers supply it.

Event time is taken from the row's ``sys_updated_on`` (``YYYY-MM-DD HH:MM:SS``,
UTC) when present, else ``ns_now()``. ``device_id`` is the change number.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..types import AttestationRecord
from .base import RestConnector

# Numeric encoding for ``value`` (measurement "change_state").
# Mirrors ServiceNow's change_request.state lifecycle:
#   new        -> 0.0
#   assess     -> 1.0
#   authorize  -> 1.5
#   scheduled  -> 1.8
#   implement  -> 2.0
#   review     -> 3.0
#   closed     -> 4.0
#   canceled   -> -1.0
# Unknown states map to ``_UNKNOWN``.
_STATE_CODES: dict[str, float] = {
    "new": 0.0,
    "assess": 1.0,
    "authorize": 1.5,
    "scheduled": 1.8,
    "implement": 2.0,
    "review": 3.0,
    "closed": 4.0,
    "canceled": -1.0,
}
_UNKNOWN = -99.0


class ServiceNowConnector(RestConnector):
    """ServiceNow change-management connector."""

    endpoint_path = "/api/now/table/change_request"

    def __init__(self, base_url: str, token: str, **kwargs):
        super().__init__("servicenow", base_url, token, **kwargs)

    def _headers(self) -> dict:
        # ServiceNow Table API uses HTTP Basic auth.
        return {"Authorization": f"Basic {self.token}", "Accept": "application/json"}

    def _extract_events(self, payload: Any) -> list[dict]:
        if isinstance(payload, dict) and isinstance(payload.get("result"), list):
            return payload["result"]
        return super()._extract_events(payload)

    def _normalize(self, raw: dict) -> AttestationRecord:
        state = str(raw["state"])  # KeyError -> skipped by base poll()
        device_id = str(raw.get("number") or raw.get("sys_id") or "")
        value = _STATE_CODES.get(state.lower(), _UNKNOWN)
        return self._record(
            device_id=device_id,
            measurement="change_state",
            value=value,
            raw=raw,
            timestamp_ns=_parse_ts(raw.get("sys_updated_on")),
        )


def _parse_ts(value) -> int | None:
    """Parse a ServiceNow ``YYYY-MM-DD HH:MM:SS`` UTC timestamp to ns, or None."""
    if not value:
        return None
    dt = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1e9)
