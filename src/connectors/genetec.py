"""Module 12.a: Genetec Security Center connector.

Pulls access-control events (door open/close/forced/held) from the Genetec
REST API and normalizes them into AttestationRecord. Used by Module 10's
DC-bus battery test (precondition: electrical_room_doors_locked).
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .base import NormalizedEvent, RESTConnector, _now_ns


# Map Genetec event types to numeric values that fit AttestationRecord.value.
# 0 = closed/secure, 1 = open, 2 = forced, 3 = held-open alarm.
_DOOR_STATE_MAP = {
    "DoorOpened": 1.0,
    "DoorClosed": 0.0,
    "DoorForcedOpen": 2.0,
    "DoorHeldOpen": 3.0,
}


class GenetecConnector(RESTConnector):
    protocol = "rest_api"

    def endpoint(self) -> str:
        return "/api/v2/events"

    def parse_events(self, body: dict) -> Iterable[NormalizedEvent]:
        for ev in body.get("events", []):
            event_type = ev.get("type", "")
            value = _DOOR_STATE_MAP.get(event_type)
            if value is None:
                continue
            ts_iso = ev.get("timestamp")
            ts_ns = _iso_to_ns(ts_iso) if ts_iso else _now_ns()
            yield NormalizedEvent(
                timestamp_ns=ts_ns,
                measurement=f"door.{ev.get('door_id', 'unknown')}",
                value=value,
                raw_bytes=str(ev),
                source_ip=ev.get("source_ip", ""),
            )


def _iso_to_ns(iso: str) -> int:
    """Convert an ISO-8601 timestamp to nanoseconds since epoch.

    Accepts the common Z-suffix shape Genetec emits.
    """
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    dt = datetime.fromisoformat(iso)
    return int(dt.timestamp() * 1_000_000_000)
