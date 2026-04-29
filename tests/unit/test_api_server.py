"""Tests for Module 21 — FastAPI Server.

Uses FastAPI's TestClient. Every backend dependency is faked.
"""

import asyncio
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from src.api.server import Deps, create_app
from src.types import PunchListItem, TestResult


class FakeOrchestrator:
    def __init__(self):
        self.submitted: list = []
        self._results: dict[str, TestResult] = {}

    async def submit(self, req):
        self.submitted.append(req)


class FakeTestEngine:
    def __init__(self):
        self.confirmations: list = []
        self.return_value = True

    def confirm_manual(self, test_id, confirmed_by):
        self.confirmations.append((test_id, confirmed_by))
        return self.return_value


class FakeAttestation:
    def __init__(self):
        self.sequence = 42
        self.previous_hash = "deadbeef"
        self._chain_id = "DC1"

    def verify_chain(self):
        return {"chain_valid": True, "breaks": [], "length": 42}


class FakeReconciliation:
    def __init__(self, items=None):
        self.items = items or []


def _client(deps: Deps) -> TestClient:
    return TestClient(create_app(deps))


def _deps(**overrides) -> Deps:
    base = dict(
        api_key="testkey",
        orchestrator=FakeOrchestrator(),
        test_engine=FakeTestEngine(),
        attestation=FakeAttestation(),
        netbox=object(),
        influx=object(),
        reconciliation=FakeReconciliation(),
    )
    base.update(overrides)
    return Deps(**base)


# --- auth -------------------------------------------------------------------


def test_missing_api_key_returns_401():
    c = _client(_deps())
    resp = c.post("/api/v1/tests/run", json={"device_id": "d1", "test_name": "x"})
    assert resp.status_code == 401


def test_wrong_api_key_returns_401():
    c = _client(_deps())
    resp = c.post(
        "/api/v1/tests/run",
        json={"device_id": "d1", "test_name": "x"},
        headers={"X-API-Key": "wrong"},
    )
    assert resp.status_code == 401


def test_health_endpoint_does_not_require_auth():
    c = _client(_deps())
    resp = c.get("/api/v1/system/health")
    assert resp.status_code == 200


# --- /tests/run -------------------------------------------------------------


def test_run_test_enqueues_request():
    deps = _deps()
    c = _client(deps)

    resp = c.post(
        "/api/v1/tests/run",
        json={"device_id": "d1", "test_name": "ups_battery_transfer", "priority": 1},
        headers={"X-API-Key": "testkey"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["test_id"]
    assert len(deps.orchestrator.submitted) == 1
    assert deps.orchestrator.submitted[0].device_id == "d1"
    assert deps.orchestrator.submitted[0].priority == 1


def test_run_test_missing_fields_returns_400():
    c = _client(_deps())
    resp = c.post(
        "/api/v1/tests/run",
        json={"device_id": "d1"},  # missing test_name
        headers={"X-API-Key": "testkey"},
    )
    assert resp.status_code == 400


# --- /tests/confirm ---------------------------------------------------------


def test_confirm_test_calls_engine():
    deps = _deps()
    c = _client(deps)

    resp = c.post(
        "/api/v1/tests/confirm/test-1",
        json={"confirmed_by": "engineer_1"},
        headers={"X-API-Key": "testkey"},
    )

    assert resp.status_code == 200
    assert deps.test_engine.confirmations == [("test-1", "engineer_1")]


def test_confirm_test_unknown_id_returns_404():
    deps = _deps()
    deps.test_engine.return_value = False
    c = _client(deps)

    resp = c.post(
        "/api/v1/tests/confirm/no-such",
        json={"confirmed_by": "x"},
        headers={"X-API-Key": "testkey"},
    )
    assert resp.status_code == 404


# --- /tests/status ----------------------------------------------------------


def test_test_status_returns_result_when_known():
    deps = _deps()
    deps.orchestrator._results["test-1"] = TestResult(
        test_id="test-1", device_id="d1", test_name="x", status="passed",
    )
    c = _client(deps)

    resp = c.get("/api/v1/tests/status/test-1", headers={"X-API-Key": "testkey"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "passed"
    assert body["test_id"] == "test-1"


def test_test_status_unknown_returns_404():
    c = _client(_deps())
    resp = c.get("/api/v1/tests/status/nope", headers={"X-API-Key": "testkey"})
    assert resp.status_code == 404


# --- /punchlist -------------------------------------------------------------


def test_punchlist_returns_items():
    items = [
        PunchListItem(severity="critical", category="identity", device_id="d1",
                      device_name="cm2000", expected="x", actual="y"),
        PunchListItem(severity="major", category="firmware", device_id="d2",
                      device_name="ex4300", expected="3.2", actual="2.0"),
    ]
    deps = _deps(reconciliation=FakeReconciliation(items=items))
    c = _client(deps)

    resp = c.get("/api/v1/punchlist", headers={"X-API-Key": "testkey"})
    assert resp.status_code == 200
    rows = resp.json()["items"]
    assert len(rows) == 2
    assert rows[0]["severity"] == "critical"


def test_punchlist_summary_aggregates_counts():
    items = [
        PunchListItem(severity="critical", category="identity", device_id="d1"),
        PunchListItem(severity="critical", category="power", device_id="d2"),
        PunchListItem(severity="major", category="firmware", device_id="d3"),
    ]
    deps = _deps(reconciliation=FakeReconciliation(items=items))
    c = _client(deps)

    resp = c.get("/api/v1/punchlist/summary", headers={"X-API-Key": "testkey"})
    body = resp.json()
    assert body["total"] == 3
    assert body["by_severity"] == {"critical": 2, "major": 1}
    assert body["by_category"] == {"identity": 1, "power": 1, "firmware": 1}


# --- /attestation -----------------------------------------------------------


def test_attestation_verify_returns_engine_result():
    c = _client(_deps())
    resp = c.get("/api/v1/attestation/verify", headers={"X-API-Key": "testkey"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["chain_valid"] is True
    assert body["length"] == 42


def test_attestation_certificate_includes_facility_and_length():
    c = _client(_deps())
    resp = c.get("/api/v1/attestation/certificate", headers={"X-API-Key": "testkey"})
    body = resp.json()
    assert body["facility"] == "DC1"
    assert body["chain_length"] == 42
    assert body["last_hash"] == "deadbeef"
