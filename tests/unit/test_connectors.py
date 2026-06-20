"""Tests for Module 12 — REST API Connectors.

Mirrors test_netbox_reader.py: every connector is driven through an injected
httpx.AsyncClient backed by httpx.MockTransport. We assert the auth header, the
endpoint path, the normalized AttestationRecord fields, the record count, and
that attestation.submit is called exactly once per event.
"""

import json

import httpx

from src.connectors import (
    GenetecConnector,
    MaximoConnector,
    MilestoneConnector,
    ServiceNowConnector,
)
from src.types import AttestationRecord


class FakeAttestation:
    """Captures records submitted via the async submit() seam."""

    def __init__(self):
        self.submitted: list[AttestationRecord] = []

    async def submit(self, record: AttestationRecord) -> None:
        self.submitted.append(record)


def _client(handler, base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)


# ----------------------------------------------------------------------
# Genetec — access control
# ----------------------------------------------------------------------
async def test_genetec_normalizes_and_submits():
    payload = {
        "events": [
            {
                "EventType": "AccessGranted",
                "DoorGuid": "door-1",
                "Timestamp": "2026-06-20T12:00:00Z",
            },
            {"EventType": "AccessDenied", "DoorGuid": "door-2", "Timestamp": "2026-06-20T12:01:00Z"},
            {"EventType": "DoorForced", "DoorGuid": "door-3"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer gtok"
        assert request.url.path == "/api/events"
        return httpx.Response(200, json=payload)

    att = FakeAttestation()
    async with _client(handler, "https://genetec.dc1.local") as c:
        conn = GenetecConnector("https://genetec.dc1.local", "gtok", attestation=att, client=c)
        records = await conn.poll()

    assert len(records) == 3
    assert len(att.submitted) == 3  # one submit per event
    granted, denied, forced = records

    assert granted.device_id == "door-1"
    assert granted.measurement == "access_granted"
    assert granted.value == 1.0
    assert granted.protocol == "rest_api"
    assert granted.source_ip == "genetec.dc1.local"
    assert granted.worker_id == ""
    assert granted.test_id is None
    # raw_bytes is a compact JSON round-trip of the event.
    assert json.loads(granted.raw_bytes)["EventType"] == "AccessGranted"
    # Timestamp parsed to UTC ns (2026-06-20T12:00:00Z).
    assert granted.timestamp_ns == 1781956800_000000000

    assert denied.value == 0.0
    assert forced.value == -1.0
    # No timestamp on the forced event -> ns_now() fallback (non-zero).
    assert forced.timestamp_ns > 0


# ----------------------------------------------------------------------
# ServiceNow — change management
# ----------------------------------------------------------------------
async def test_servicenow_normalizes_and_submits():
    payload = {
        "result": [
            {"number": "CHG0001", "state": "implement", "sys_updated_on": "2026-06-20 09:30:00"},
            {"number": "CHG0002", "state": "Closed", "sys_updated_on": "2026-06-20 10:00:00"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Basic snowtok"
        assert request.url.path == "/api/now/table/change_request"
        return httpx.Response(200, json=payload)

    att = FakeAttestation()
    async with _client(handler, "https://snow.dc1.local") as c:
        conn = ServiceNowConnector("https://snow.dc1.local", "snowtok", attestation=att, client=c)
        records = await conn.poll()

    assert len(records) == 2
    assert len(att.submitted) == 2
    impl, closed = records

    assert impl.device_id == "CHG0001"
    assert impl.measurement == "change_state"
    assert impl.value == 2.0
    assert impl.source_ip == "snow.dc1.local"
    assert impl.timestamp_ns == 1781947800_000000000  # 2026-06-20 09:30:00 UTC
    # state lookup is case-insensitive.
    assert closed.value == 4.0


# ----------------------------------------------------------------------
# Maximo — CMMS work orders
# ----------------------------------------------------------------------
async def test_maximo_normalizes_and_submits():
    payload = {
        "member": [
            {"wonum": "WO1001", "status": "INPRG", "statusdate": "2026-06-20T08:00:00Z"},
            {"wonum": "WO1002", "status": "comp", "statusdate": "2026-06-20T08:15:00Z"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        # Maximo uses apikey, not Authorization.
        assert request.headers["apikey"] == "mxtok"
        assert "Authorization" not in request.headers
        assert request.url.path == "/maximo/api/os/mxwo"
        return httpx.Response(200, json=payload)

    att = FakeAttestation()
    async with _client(handler, "https://maximo.dc1.local") as c:
        conn = MaximoConnector("https://maximo.dc1.local", "mxtok", attestation=att, client=c)
        records = await conn.poll()

    assert len(records) == 2
    assert len(att.submitted) == 2
    inprg, comp = records

    assert inprg.device_id == "WO1001"
    assert inprg.measurement == "work_order_status"
    assert inprg.value == 2.0
    assert inprg.source_ip == "maximo.dc1.local"
    assert inprg.timestamp_ns == 1781942400_000000000  # 2026-06-20T08:00:00Z
    # status lookup is case-insensitive.
    assert comp.value == 3.0


# ----------------------------------------------------------------------
# Milestone — CCTV
# ----------------------------------------------------------------------
async def test_milestone_normalizes_and_submits():
    payload = {
        "array": [
            {"id": "cam-1", "state": "Recording"},
            {"id": "cam-2", "state": "Offline"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer mstok"
        assert request.url.path == "/api/rest/v1/cameras"
        return httpx.Response(200, json=payload)

    att = FakeAttestation()
    async with _client(handler, "https://milestone.dc1.local") as c:
        conn = MilestoneConnector("https://milestone.dc1.local", "mstok", attestation=att, client=c)
        records = await conn.poll()

    assert len(records) == 2
    assert len(att.submitted) == 2
    recording, offline = records

    assert recording.device_id == "cam-1"
    assert recording.measurement == "camera_status"
    assert recording.value == 1.0
    assert recording.source_ip == "milestone.dc1.local"
    assert recording.timestamp_ns > 0  # no event time -> ns_now()
    assert offline.value == 0.0


# ----------------------------------------------------------------------
# Per-event isolation
# ----------------------------------------------------------------------
async def test_malformed_event_is_skipped_not_crashing():
    payload = {
        "events": [
            {"EventType": "AccessGranted", "DoorGuid": "door-1", "Timestamp": "2026-06-20T12:00:00Z"},
            {"DoorGuid": "door-x"},  # missing EventType -> KeyError -> skipped
            {"EventType": "AccessDenied", "DoorGuid": "door-2"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    att = FakeAttestation()
    async with _client(handler, "https://genetec.dc1.local") as c:
        conn = GenetecConnector("https://genetec.dc1.local", "gtok", attestation=att, client=c)
        records = await conn.poll()

    # Two good events survive; the malformed one is skipped, not fatal.
    assert len(records) == 2
    assert len(att.submitted) == 2
    assert [r.device_id for r in records] == ["door-1", "door-2"]
    # The skipped event is captured for observability.
    assert len(conn.errors) == 1
    assert conn.errors[0]["event"] == {"DoorGuid": "door-x"}


async def test_unknown_event_type_maps_to_sentinel_value():
    payload = {"events": [{"EventType": "Mystery", "DoorGuid": "door-9"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    att = FakeAttestation()
    async with _client(handler, "https://genetec.dc1.local") as c:
        conn = GenetecConnector("https://genetec.dc1.local", "gtok", attestation=att, client=c)
        records = await conn.poll()

    assert len(records) == 1
    assert records[0].value == -99.0  # sentinel for unknown event type


async def test_empty_payload_returns_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"array": []})

    att = FakeAttestation()
    async with _client(handler, "https://milestone.dc1.local") as c:
        conn = MilestoneConnector("https://milestone.dc1.local", "mstok", attestation=att, client=c)
        records = await conn.poll()

    assert records == []
    assert att.submitted == []
