"""Module 12.c: IBM Maximo CMMS connector.

Pulls open work orders so reconciliation can flag devices that are under
maintenance. Each work order becomes an attested record tagged with the
device asset number.
"""

from __future__ import annotations

from typing import Iterable

from .base import NormalizedEvent, RESTConnector, _now_ns


class MaximoConnector(RESTConnector):
    protocol = "rest_api"

    def endpoint(self) -> str:
        return "/maxrest/rest/mbo/workorder"

    def parse_events(self, body: dict) -> Iterable[NormalizedEvent]:
        for wo in body.get("member", []):
            yield NormalizedEvent(
                timestamp_ns=_now_ns(),
                measurement=f"workorder.{wo.get('wonum', 'unknown')}",
                # 1 = open, 0 = closed/cancelled.
                value=1.0 if wo.get("status") in {"WAPPR", "APPR", "INPRG"} else 0.0,
                raw_bytes=str(wo),
                source_ip="",
            )
