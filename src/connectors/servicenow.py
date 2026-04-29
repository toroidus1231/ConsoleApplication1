"""Module 12.b: ServiceNow change-management connector.

Pulls change requests in OPEN states (planned, in-progress) so the orchestrator
knows which devices are under maintenance lockout. We attest the count of open
changes per CI (configuration item) — useful for the audit trail.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .base import NormalizedEvent, RESTConnector, _now_ns


class ServiceNowConnector(RESTConnector):
    protocol = "rest_api"

    def endpoint(self) -> str:
        return "/api/now/table/change_request"

    def parse_events(self, body: dict) -> Iterable[NormalizedEvent]:
        for cr in body.get("result", []):
            ts_str = cr.get("sys_updated_on") or cr.get("sys_created_on")
            ts_ns = _servicenow_ts_to_ns(ts_str) if ts_str else _now_ns()
            yield NormalizedEvent(
                timestamp_ns=ts_ns,
                measurement=f"change_request.{cr.get('number', 'unknown')}",
                value=1.0 if cr.get("state") in {"-1", "1", "2", "-3"} else 0.0,
                raw_bytes=str(cr),
                source_ip="",
            )


def _servicenow_ts_to_ns(s: str) -> int:
    # ServiceNow timestamps are in UTC, format: "2026-04-12 14:30:00"
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return _now_ns()
    return int(dt.timestamp() * 1_000_000_000)
