"""Module 12.d: Milestone XProtect CCTV connector.

Pulls camera-event metadata (motion, tamper, video-loss). Used as
audit-trail context — the orchestrator can correlate test events with
camera events for forensic playback.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .base import NormalizedEvent, RESTConnector, _now_ns


class MilestoneConnector(RESTConnector):
    protocol = "rest_api"

    def endpoint(self) -> str:
        return "/api/rest/v1/events"

    def parse_events(self, body: dict) -> Iterable[NormalizedEvent]:
        for ev in body.get("array", []):
            ts_iso = ev.get("timestamp")
            ts_ns = _iso_to_ns(ts_iso) if ts_iso else _now_ns()
            event_type = ev.get("eventType", "")
            yield NormalizedEvent(
                timestamp_ns=ts_ns,
                measurement=f"camera.{ev.get('cameraId', 'unknown')}.{event_type}",
                value=1.0,  # presence of an event
                raw_bytes=str(ev),
                source_ip=ev.get("sourceIp", ""),
            )


def _iso_to_ns(iso: str) -> int:
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return _now_ns()
    return int(dt.timestamp() * 1_000_000_000)
