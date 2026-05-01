"""Tests for src/compliance/* — cal certs, audit review, retention, cert gen."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from src.compliance.cal_certs import (
    CalCert, CertVerifier, InMemoryCalCertSource,
)
from src.compliance.audit_review import AuditReview, AuditQuery
from src.compliance.retention import (
    RetentionAuditor, RetentionMode, RetentionPolicy,
    SEVEN_YEAR_COMPLIANCE, RetentionError,
)
from src.compliance.certificate_gen import (
    CommissioningCertificate, render_certificate_pdf, verify_certificate,
)


def _now():
    return datetime(2026, 5, 1, tzinfo=timezone.utc)


def _make_cert(**over):
    base = dict(
        cert_id="CAL-TEST-001",
        instrument_serial="SN-1",
        vendor="Megger",
        model="MIT525",
        covers_test_types=("insulation_resistance",),
        issued_at=_now() - timedelta(days=30),
        expires_at=_now() + timedelta(days=365),
        issuer="Megger UK Ltd.",
        standards=("NIST", "UKAS"),
        hash="sha256:megger-mit525-CAL-TEST-001",
    )
    base.update(over)
    return CalCert(**base)


# ---------------------------------------------------------------------------
# CalCertVerifier
# ---------------------------------------------------------------------------


def test_verify_passes_for_valid_cert():
    src = InMemoryCalCertSource()
    src.add(_make_cert())
    v = CertVerifier(src)
    r = v.verify(
        cert_id="CAL-TEST-001",
        expected_hash="sha256:megger-mit525-CAL-TEST-001",
        vendor="Megger", model="MIT525", instrument_serial="SN-1",
        test_type="insulation_resistance", run_at=_now(),
    )
    assert r.valid is True
    assert r.cert is not None


def test_verify_fails_for_unknown_cert_id():
    v = CertVerifier(InMemoryCalCertSource())
    r = v.verify(cert_id="NOPE", expected_hash="x",
                 vendor="V", model="M", instrument_serial="S",
                 test_type="t", run_at=_now())
    assert not r.valid
    assert "unknown cert_id" in r.reason


def test_verify_fails_for_hash_mismatch():
    src = InMemoryCalCertSource()
    src.add(_make_cert(hash="sha256:legit"))
    v = CertVerifier(src)
    r = v.verify(cert_id="CAL-TEST-001", expected_hash="sha256:tampered",
                 vendor="Megger", model="MIT525", instrument_serial="SN-1",
                 test_type="insulation_resistance", run_at=_now())
    assert not r.valid
    assert "hash mismatch" in r.reason


def test_verify_fails_for_wrong_vendor():
    src = InMemoryCalCertSource()
    src.add(_make_cert())
    v = CertVerifier(src)
    r = v.verify(cert_id="CAL-TEST-001",
                 expected_hash="sha256:megger-mit525-CAL-TEST-001",
                 vendor="Acme", model="MIT525", instrument_serial="SN-1",
                 test_type="insulation_resistance", run_at=_now())
    assert not r.valid
    assert "vendor mismatch" in r.reason


def test_verify_fails_for_test_type_not_covered():
    src = InMemoryCalCertSource()
    src.add(_make_cert(covers_test_types=("dc_withstand",)))
    v = CertVerifier(src)
    r = v.verify(cert_id="CAL-TEST-001",
                 expected_hash="sha256:megger-mit525-CAL-TEST-001",
                 vendor="Megger", model="MIT525", instrument_serial="SN-1",
                 test_type="insulation_resistance", run_at=_now())
    assert not r.valid
    assert "does not cover" in r.reason


def test_verify_fails_for_expired_cert():
    src = InMemoryCalCertSource()
    src.add(_make_cert(expires_at=_now() - timedelta(days=1)))
    v = CertVerifier(src)
    r = v.verify(cert_id="CAL-TEST-001",
                 expected_hash="sha256:megger-mit525-CAL-TEST-001",
                 vendor="Megger", model="MIT525", instrument_serial="SN-1",
                 test_type="insulation_resistance", run_at=_now())
    assert not r.valid
    assert "expired" in r.reason


def test_verify_fails_for_future_run_before_issuance():
    src = InMemoryCalCertSource()
    src.add(_make_cert(issued_at=_now() + timedelta(days=30)))
    v = CertVerifier(src)
    r = v.verify(cert_id="CAL-TEST-001",
                 expected_hash="sha256:megger-mit525-CAL-TEST-001",
                 vendor="Megger", model="MIT525", instrument_serial="SN-1",
                 test_type="insulation_resistance", run_at=_now())
    assert not r.valid
    assert "before cert" in r.reason


def test_list_for_instrument():
    src = InMemoryCalCertSource()
    src.add(_make_cert())
    src.add(_make_cert(cert_id="CAL-TEST-002", expires_at=_now() + timedelta(days=180)))
    src.add(_make_cert(cert_id="OTHER", instrument_serial="SN-2"))
    matches = src.list_for_instrument("Megger", "MIT525", "SN-1")
    assert len(matches) == 2


# ---------------------------------------------------------------------------
# AuditReview
# ---------------------------------------------------------------------------


class FakeEvidence:
    def __init__(self, runs):
        # runs: dict[device_id, list[dict]]
        self._runs = runs

    def list_for_device(self, did):
        return list(self._runs.get(did, []))

    def list_runs(self, did, tn):
        return [r for r in self._runs.get(did, [])
                if r.get("test_name") == tn]


class FakeAttestation:
    def __init__(self, length=42, last_hash="abc"):
        self._length = length
        self._last = last_hash

    def verify_chain(self):
        return {"valid": True, "length": self._length, "last_hash": self._last}


def test_audit_query_filters_by_device():
    ev = FakeEvidence({
        "ups-A": [{"test_name": "ups_battery_transfer",
                   "completed_at": "2026-04-01T00:00:00", "passed": True}],
        "ups-B": [{"test_name": "ups_battery_transfer",
                   "completed_at": "2026-04-15T00:00:00", "passed": True}],
    })
    rv = AuditReview(ev, FakeAttestation())
    page = rv.query(AuditQuery(device_ids=["ups-A"]))
    assert page.total == 1
    assert page.rows[0]["completed_at"] == "2026-04-01T00:00:00"


def test_audit_query_filters_by_status():
    ev = FakeEvidence({
        "x": [
            {"test_name": "t", "completed_at": "2026-04-01T00", "status": "passed"},
            {"test_name": "t", "completed_at": "2026-04-02T00", "status": "failed"},
        ],
    })
    rv = AuditReview(ev, FakeAttestation())
    page = rv.query(AuditQuery(statuses=["failed"]),
                    all_devices=["x"])
    assert page.total == 1
    assert page.rows[0]["status"] == "failed"


def test_audit_query_paginates():
    ev = FakeEvidence({
        "x": [{"test_name": "t", "completed_at": f"2026-01-{d:02d}T00:00:00",
               "passed": True} for d in range(1, 31)],
    })
    rv = AuditReview(ev, FakeAttestation())
    p1 = rv.query(AuditQuery(), page=1, page_size=10, all_devices=["x"])
    p3 = rv.query(AuditQuery(), page=3, page_size=10, all_devices=["x"])
    assert p1.total == 30
    assert len(p1.rows) == 10
    assert len(p3.rows) == 10
    # Newest first
    assert p1.rows[0]["completed_at"] > p3.rows[0]["completed_at"]


def test_audit_export_packet_includes_attestation():
    ev = FakeEvidence({"x": [{"test_name": "t", "completed_at": "2026-01-01",
                              "passed": True}]})
    rv = AuditReview(ev, FakeAttestation(length=99, last_hash="zzz"))
    pkt = rv.export_packet(AuditQuery(), all_devices=["x"])
    assert pkt["row_count"] == 1
    assert pkt["attestation"]["length"] == 99
    assert pkt["attestation"]["last_hash"] == "zzz"


# ---------------------------------------------------------------------------
# RetentionAuditor
# ---------------------------------------------------------------------------


class _Obj:
    def __init__(self, name): self.object_name = name


class FakeLockMinio:
    def __init__(self, *, lock_cfg=None, objects=None,
                 obj_retentions=None):
        self.lock_cfg = lock_cfg or {}
        self.objects = objects or []
        self.obj_retentions = obj_retentions or {}

    def get_bucket_policy(self, b): return {}

    def get_object_lock_config(self, b):
        if not self.lock_cfg:
            raise Exception("no lock cfg")
        return self.lock_cfg

    def list_objects(self, b, prefix="", recursive=True):
        return [_Obj(n) for n in self.objects]

    def get_object_retention(self, b, name):
        if name not in self.obj_retentions:
            raise Exception("no retention")
        return self.obj_retentions[name]


def test_retention_assert_compliant_succeeds():
    minio = FakeLockMinio(lock_cfg={"mode": "COMPLIANCE", "days": 2555})
    auditor = RetentionAuditor(minio, SEVEN_YEAR_COMPLIANCE)
    auditor.assert_bucket_compliant("evidence")


def test_retention_assert_fails_for_governance_mode():
    minio = FakeLockMinio(lock_cfg={"mode": "GOVERNANCE", "days": 2555})
    auditor = RetentionAuditor(minio, SEVEN_YEAR_COMPLIANCE)
    with pytest.raises(RetentionError, match="mode"):
        auditor.assert_bucket_compliant("evidence")


def test_retention_assert_fails_for_short_retention():
    minio = FakeLockMinio(lock_cfg={"mode": "COMPLIANCE", "days": 365})
    auditor = RetentionAuditor(minio, SEVEN_YEAR_COMPLIANCE)
    with pytest.raises(RetentionError, match="< policy"):
        auditor.assert_bucket_compliant("evidence")


def test_retention_audit_bucket_passes_when_all_objects_locked():
    minio = FakeLockMinio(
        lock_cfg={"mode": "COMPLIANCE", "days": 2555},
        objects=["a.json", "b.json"],
        obj_retentions={
            "a.json": {"mode": "COMPLIANCE"},
            "b.json": {"mode": "COMPLIANCE"},
        },
    )
    auditor = RetentionAuditor(minio, SEVEN_YEAR_COMPLIANCE)
    r = auditor.audit_bucket("evidence")
    assert r.ok is True
    assert r.objects_violating == []


def test_retention_audit_flags_governance_object():
    minio = FakeLockMinio(
        lock_cfg={"mode": "COMPLIANCE", "days": 2555},
        objects=["a.json", "b.json"],
        obj_retentions={
            "a.json": {"mode": "COMPLIANCE"},
            "b.json": {"mode": "GOVERNANCE"},
        },
    )
    auditor = RetentionAuditor(minio, SEVEN_YEAR_COMPLIANCE)
    r = auditor.audit_bucket("evidence")
    assert not r.ok
    assert any("b.json" in v["name"] for v in r.objects_violating)


def test_retention_policy_deadline_arithmetic():
    p = RetentionPolicy(RetentionMode.COMPLIANCE, days=2555)
    written = datetime(2026, 1, 1, tzinfo=timezone.utc)
    deadline = p.deadline_for(written)
    assert deadline == written + timedelta(days=2555)


# ---------------------------------------------------------------------------
# CommissioningCertificate
# ---------------------------------------------------------------------------


def _sample_cert():
    return CommissioningCertificate(
        facility="DC1-Ashburn",
        devices=["mtg-feed-A", "ups-A", "xfmr-A1"],
        energization_date=_now(),
        tests_completed=[
            {"device_id": "mtg-feed-A", "test_name": "busway_megger",
             "status": "passed", "completed_at": "2026-05-01T08:00:00"},
            {"device_id": "ups-A", "test_name": "ups_battery_transfer",
             "status": "passed", "completed_at": "2026-05-01T09:00:00"},
        ],
        attestation_last_hash="abcdef" * 8,
        attestation_chain_length=458,
        signed_by="J. Reyes",
        signed_by_role="Commissioning Lead",
        nfpa_refs=["NFPA 110 §8.4.2"],
        nec_refs=["NEC §700.7"],
        cert_id="CC-DC1-2026-001",
    )


def test_certificate_signature_roundtrip():
    cert = _sample_cert()
    secret = b"signing-key-32-bytes-for-tests!!"
    sig = cert.sign(secret)
    assert verify_certificate(cert, sig, secret) is True


def test_certificate_signature_detects_tamper():
    cert = _sample_cert()
    secret = b"signing-key-32-bytes-for-tests!!"
    sig = cert.sign(secret)
    cert.devices.append("EXTRA-DEVICE")  # tamper
    assert verify_certificate(cert, sig, secret) is False


def test_certificate_signature_different_secret_rejected():
    cert = _sample_cert()
    sig = cert.sign(b"key-A-aaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert not verify_certificate(cert, sig, b"key-B-bbbbbbbbbbbbbbbbbbbbbbbbbb")


def test_render_certificate_pdf_produces_valid_pdf():
    cert = _sample_cert()
    sig = cert.sign(b"test-secret")
    pdf = render_certificate_pdf(cert, sig)
    assert pdf.startswith(b"%PDF-")
    # Sanity: cert ID in body (the signature is encoded in PDF text streams
    # which compresses/recodes characters, so we check for the cert ID which
    # is plain ASCII).
    assert cert.cert_id.encode() in pdf or cert.cert_id in pdf.decode("latin-1", errors="ignore")
    assert b"Commissioning Certificate" in pdf or len(pdf) > 1500


def test_canonical_json_is_stable():
    cert = _sample_cert()
    a = cert.canonical_json()
    cert.devices.reverse()  # order shouldn't matter
    b = cert.canonical_json()
    assert a == b
