"""End-to-end tests for the 5 operator-write endpoints I added:

  PATCH /api/v1/punchlist/{id}
  POST  /api/v1/checklist/{device}/{item}
  POST  /api/v1/reports/generate
  POST  /api/v1/bim/import
  POST  /api/v1/config/generate

These were the silent dead endpoints. Tests run the FastAPI TestClient
against a real Deps() shape with in-memory fakes for everything the
handlers touch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import asyncio
import pytest

from fastapi.testclient import TestClient

from src.api.server import Deps, create_app
from src.attestation import AttestationEngine
from src.types import PunchListItem


class FakeMinio:
    def __init__(self):
        self.store: dict[str, bytes] = {}
    def put_object(self, b, n, data, length, content_type=""):
        body = data.read() if hasattr(data, "read") else bytes(data)
        self.store[n] = body
    def get_object(self, b, n):
        class S:
            def __init__(self, body): self.body = body
            def read(self): return self.body
            def close(self): pass
        return S(self.store[n])
    def list_objects(self, b, prefix="", recursive=True):
        class O:
            def __init__(self, name): self.object_name = name
        return [O(k) for k in self.store if k.startswith(prefix)]


@dataclass
class FakeReconciliation:
    items: list[PunchListItem] = field(default_factory=list)


@pytest.fixture
def deps_with_punch():
    items = [
        PunchListItem(id="punch-001", severity="critical", category="power",
                      device_id="xfmr-A1", remediation="Active arcing"),
        PunchListItem(id="punch-002", severity="major", category="power",
                      device_id="ups-B", remediation="Weak cells exceed threshold"),
    ]
    attest = AttestationEngine(
        facility_name="DC1-Ashburn", minio_bucket="att",
        minio_client=FakeMinio(), wal_dir="/tmp/test-wal",
        worker_id="test",
    )
    return Deps(
        api_key="demo",
        attestation=attest,
        reconciliation=FakeReconciliation(items=items),
        facility_checklists={
            "xfmr-A1": [
                {"id": "nameplate", "description": "Verify nameplate",
                 "completed": False, "signed": False},
                {"id": "oil_level", "description": "Verify oil level",
                 "completed": False, "signed": False},
            ],
        },
    )


# ---------------------------------------------------------------------------
# PATCH /punchlist/{id}
# ---------------------------------------------------------------------------


def test_patch_punchlist_resolves_item(deps_with_punch):
    client = TestClient(create_app(deps_with_punch))
    r = client.patch("/api/v1/punchlist/punch-001",
                     headers={"X-API-Key": "demo"},
                     json={"status": "resolved", "resolved_by": "j.reyes"})
    assert r.status_code == 200
    assert r.json()["status"] == "resolved"
    item = next(i for i in deps_with_punch.reconciliation.items if i.id == "punch-001")
    assert item.status == "resolved"
    assert item.resolved_by == "j.reyes"


def test_patch_punchlist_unknown_id_404(deps_with_punch):
    client = TestClient(create_app(deps_with_punch))
    r = client.patch("/api/v1/punchlist/does-not-exist",
                     headers={"X-API-Key": "demo"},
                     json={"status": "resolved"})
    assert r.status_code == 404


def test_patch_punchlist_appends_to_attestation_chain(deps_with_punch):
    client = TestClient(create_app(deps_with_punch))
    qsize_before = deps_with_punch.attestation._queue.qsize()
    client.patch("/api/v1/punchlist/punch-001",
                 headers={"X-API-Key": "demo"},
                 json={"status": "resolved", "resolved_by": "j.reyes"})
    qsize_after = deps_with_punch.attestation._queue.qsize()
    assert qsize_after > qsize_before


def test_patch_punchlist_requires_auth(deps_with_punch):
    client = TestClient(create_app(deps_with_punch))
    r = client.patch("/api/v1/punchlist/punch-001",
                     json={"status": "resolved"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /checklist/{device}/{item}
# ---------------------------------------------------------------------------


def test_post_checklist_signs_item(deps_with_punch):
    client = TestClient(create_app(deps_with_punch))
    r = client.post(
        "/api/v1/checklist/xfmr-A1/nameplate",
        headers={"X-API-Key": "demo"},
        data={"signed_by": "j.reyes", "note": "verified"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["device_id"] == "xfmr-A1"
    assert body["item_id"] == "nameplate"
    assert "signed_at" in body
    item = next(i for i in deps_with_punch.facility_checklists["xfmr-A1"]
                if i["id"] == "nameplate")
    assert item["signed"] is True
    assert item["signed_by"] == "j.reyes"


def test_post_checklist_unknown_device_404(deps_with_punch):
    client = TestClient(create_app(deps_with_punch))
    r = client.post(
        "/api/v1/checklist/no-such-device/nameplate",
        headers={"X-API-Key": "demo"},
        data={"signed_by": "x"},
    )
    assert r.status_code == 404


def test_post_checklist_unknown_item_404(deps_with_punch):
    client = TestClient(create_app(deps_with_punch))
    r = client.post(
        "/api/v1/checklist/xfmr-A1/no-such-item",
        headers={"X-API-Key": "demo"},
        data={"signed_by": "x"},
    )
    assert r.status_code == 404


def test_post_checklist_appends_to_attestation_chain(deps_with_punch):
    client = TestClient(create_app(deps_with_punch))
    qsize_before = deps_with_punch.attestation._queue.qsize()
    client.post(
        "/api/v1/checklist/xfmr-A1/nameplate",
        headers={"X-API-Key": "demo"},
        data={"signed_by": "j.reyes"},
    )
    qsize_after = deps_with_punch.attestation._queue.qsize()
    assert qsize_after > qsize_before


# ---------------------------------------------------------------------------
# POST /reports/generate
# ---------------------------------------------------------------------------


def test_reports_generate_returns_pdf(deps_with_punch):
    client = TestClient(create_app(deps_with_punch))
    r = client.post("/api/v1/reports/generate",
                    headers={"X-API-Key": "demo"},
                    json={"title": "DC1-Ashburn Commissioning Report"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-")
    assert len(r.content) > 1000  # actual PDF, not stub


def test_reports_generate_no_body_uses_defaults(deps_with_punch):
    client = TestClient(create_app(deps_with_punch))
    r = client.post("/api/v1/reports/generate",
                    headers={"X-API-Key": "demo"},
                    json={})
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")


# ---------------------------------------------------------------------------
# POST /bim/import — graceful degradation when ifcopenshell not configured
# ---------------------------------------------------------------------------


def test_bim_import_without_parser_returns_graceful_response(deps_with_punch):
    client = TestClient(create_app(deps_with_punch))
    r = client.post("/api/v1/bim/import",
                    headers={"X-API-Key": "demo"},
                    files={"file": ("design.ifc", b"FAKE-IFC-CONTENT", "application/octet-stream")})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["filename"] == "design.ifc"
    assert "not configured" in body["note"]


def test_bim_import_requires_file(deps_with_punch):
    client = TestClient(create_app(deps_with_punch))
    r = client.post("/api/v1/bim/import",
                    headers={"X-API-Key": "demo"})
    assert r.status_code == 422  # missing required form field


# ---------------------------------------------------------------------------
# POST /config/generate
# ---------------------------------------------------------------------------


def test_config_generate_without_pipeline_returns_graceful(deps_with_punch):
    client = TestClient(create_app(deps_with_punch))
    r = client.post(
        "/api/v1/config/generate",
        headers={"X-API-Key": "demo"},
        files={"file": ("vertiv.pdf", b"%PDF-fake", "application/pdf")},
        data={"device_type_slug": "vertiv-mtg-5000a"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["device_type_slug"] == "vertiv-mtg-5000a"


def test_config_generate_requires_device_type_slug(deps_with_punch):
    client = TestClient(create_app(deps_with_punch))
    r = client.post(
        "/api/v1/config/generate",
        headers={"X-API-Key": "demo"},
        files={"file": ("x.pdf", b"%PDF-fake", "application/pdf")},
    )
    assert r.status_code == 422
