"""Module 12: REST API Connectors — Genetec (access control).

Genetec Security Center exposes access-control events at ``/api/events``.
Each event records a cardholder badge action at a door. We normalize each to
an ``AttestationRecord`` with measurement ``"access_granted"``.

Event time is taken from the event's ``Timestamp`` (ISO-8601, UTC) when
present, else ``ns_now()``. ``device_id`` is the door/reader identifier.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..types import AttestationRecord
from .base import RestConnector

# Numeric encoding for ``value`` (measurement "access_granted").
#   AccessGranted -> 1.0   (door opened for a valid credential)
#   AccessDenied  -> 0.0   (credential rejected)
#   DoorForced    -> -1.0  (door opened without a grant — security event)
#   DoorHeld      -> -2.0  (door held open past the relock timer)
# Unknown event types map to ``_UNKNOWN``.
_EVENT_CODES: dict[str, float] = {
    "AccessGranted": 1.0,
    "AccessDenied": 0.0,
    "DoorForced": -1.0,
    "DoorHeld": -2.0,
}
_UNKNOWN = -99.0


class GenetecConnector(RestConnector):
    """Genetec access-control connector."""

    endpoint_path = "/api/events"

    def __init__(self, base_url: str, token: str, **kwargs):
        super().__init__("genetec", base_url, token, **kwargs)

    def _normalize(self, raw: dict) -> AttestationRecord:
        event_type = raw["EventType"]  # KeyError -> skipped by base poll()
        device_id = str(raw.get("DoorGuid") or raw.get("UnitGuid") or "")
        value = _EVENT_CODES.get(event_type, _UNKNOWN)
        return self._record(
            device_id=device_id,
            measurement="access_granted",
            value=value,
            raw=raw,
            timestamp_ns=_parse_ts(raw.get("Timestamp")),
        )


def _parse_ts(value) -> int | None:
    """Parse a Genetec ISO-8601 timestamp to UTC nanoseconds, or None."""
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1e9)
