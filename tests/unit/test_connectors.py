"""Tests for Module 12 — REST API Connectors.

Mocks the vendor APIs via httpx.MockTransport. Verifies each connector
parses its vendor-specific JSON shape and submits one AttestationRecord
per event with the right protocol/measurement/value.
"""

from datetime import datetime, timezone

import httpx
import pytest

from src.connectors.base import RESTConnector
from src.connectors.genetec import GenetecConnector
from src.connectors.maximo import MaximoConnector
from src.connectors.milestone import MilestoneConnector
from src.connectors.servicenow import ServiceNowConnector
from src.types import AttestationRecord


class FakeAttestation:
    def __init__(self):
        self.submitted: list[AttestationRecord] = []

    async def submit(self, record):
        self.submitted.append(record)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- Base behavior -----------------------------------------------------------


def test_base_url_required():
    with pytest.raises(ValueError, match="url is required"):
        GenetecConnector(url="", token="t", attestation=FakeAttestation())


def test_base_url_trailing_slash_normalized():
    c = GenetecConnector(url="http://host/", token="t", attestation=FakeAttestation())
    assert c.url == "http://host"


# --- Genetec -----------------------------------------------------------------


async def test_genetec_parses_door_events():
    body = {
        "events": [
            {"type": "DoorOpened", "door_id": "D1", "timestamp": "2026-04-12T14:30:00Z", "source_ip": "10.0.0.5"},
            {"type": "DoorClosed", "door_id": "D1", "timestamp": "2026-04-12T14:31:00Z"},
            {"type": "DoorForcedOpen", "door_id": "D2", "timestamp": "2026-04-12T14:32:00Z"},
            {"type": "UnknownEvent", "door_id": "D9"},  # filtered
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/events"
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json=body)

    attest = FakeAttestation()
    async with _client(handler) as c:
        connector = GenetecConnector("http://genetec", "tok", attest, client=c)
        n = await connector.fetch_and_attest()

    assert n == 3
    assert [r.measurement for r in attest.submitted] == ["door.D1", "door.D1", "door.D2"]
    assert [r.value for r in attest.submitted] == [1.0, 0.0, 2.0]
    assert all(r.protocol == "rest_api" for r in attest.submitted)
    assert attest.submitted[0].source_ip == "10.0.0.5"


async def test_genetec_iso_timestamp_converted_to_ns():
    body = {"events": [{"type": "DoorOpened", "door_id": "D1", "timestamp": "2026-04-12T14:30:00Z"}]}

    def handler(_):
        return httpx.Response(200, json=body)

    attest = FakeAttestation()
    async with _client(handler) as c:
        await GenetecConnector("http://x", "t", attest, client=c).fetch_and_attest()

    expected_ns = int(datetime(2026, 4, 12, 14, 30, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    assert attest.submitted[0].timestamp_ns == expected_ns


# --- ServiceNow --------------------------------------------------------------


async def test_servicenow_parses_change_requests():
    body = {
        "result": [
            {"number": "CHG0001", "state": "1", "sys_updated_on": "2026-04-12 14:30:00"},   # open
            {"number": "CHG0002", "state": "3", "sys_updated_on": "2026-04-12 14:31:00"},   # closed
        ]
    }

    def handler(request):
        assert request.url.path == "/api/now/table/change_request"
        return httpx.Response(200, json=body)

    attest = FakeAttestation()
    async with _client(handler) as c:
        n = await ServiceNowConnector("http://sn", "t", attest, client=c).fetch_and_attest()

    assert n == 2
    assert [r.measurement for r in attest.submitted] == [
        "change_request.CHG0001", "change_request.CHG0002",
    ]
    assert [r.value for r in attest.submitted] == [1.0, 0.0]


# --- Maximo ------------------------------------------------------------------


async def test_maximo_parses_workorders():
    body = {
        "member": [
            {"wonum": "WO1001", "status": "INPRG"},   # open
            {"wonum": "WO1002", "status": "CLOSE"},   # closed
            {"wonum": "WO1003", "status": "WAPPR"},   # awaiting approval -> open
        ]
    }

    def handler(request):
        assert request.url.path == "/maxrest/rest/mbo/workorder"
        return httpx.Response(200, json=body)

    attest = FakeAttestation()
    async with _client(handler) as c:
        n = await MaximoConnector("http://mx", "t", attest, client=c).fetch_and_attest()

    assert n == 3
    assert [r.value for r in attest.submitted] == [1.0, 0.0, 1.0]


# --- Milestone ---------------------------------------------------------------


async def test_milestone_parses_camera_events():
    body = {
        "array": [
            {"cameraId": "CAM1", "eventType": "Motion", "timestamp": "2026-04-12T14:30:00Z"},
            {"cameraId": "CAM2", "eventType": "VideoLoss", "timestamp": "2026-04-12T14:31:00Z"},
        ]
    }

    def handler(request):
        assert request.url.path == "/api/rest/v1/events"
        return httpx.Response(200, json=body)

    attest = FakeAttestation()
    async with _client(handler) as c:
        n = await MilestoneConnector("http://ms", "t", attest, client=c).fetch_and_attest()

    assert n == 2
    assert attest.submitted[0].measurement == "camera.CAM1.Motion"
    assert attest.submitted[1].measurement == "camera.CAM2.VideoLoss"


# --- Error propagation -------------------------------------------------------


async def test_5xx_propagates_as_http_status_error():
    def handler(_):
        return httpx.Response(503)

    attest = FakeAttestation()
    async with _client(handler) as c:
        with pytest.raises(httpx.HTTPStatusError):
            await GenetecConnector("http://x", "t", attest, client=c).fetch_and_attest()
    assert attest.submitted == []


async def test_empty_response_attests_nothing():
    def handler(_):
        return httpx.Response(200, json={"events": []})

    attest = FakeAttestation()
    async with _client(handler) as c:
        n = await GenetecConnector("http://x", "t", attest, client=c).fetch_and_attest()

    assert n == 0
    assert attest.submitted == []
