"""Calibration certificate registry.

Every test record's instrument metadata carries a `cert_id` and
`cert_hash`. Before accepting a test result as evidence, the platform
verifies that:

  1. The cert_id exists in the registry.
  2. The cert_hash matches what the registry has for that cert.
  3. The cert is currently valid (issued ≤ today ≤ expires).
  4. The cert covers the requested test type (e.g., a hipot cert
     can't be used to claim a megger test result).

Certs are issued by accredited cal labs (NIST/UKAS/A2LA-traceable).
The registry can be backed by:

  - A local SQLite cache populated from a CMM (Calibration Management)
    system like Indysoft, ProCalV5, Beamex CMX.
  - A REST endpoint at the CMM (production).
  - An in-memory dict (dev / tests).

Schema per cert:

    {
      "cert_id":         "CAL-2026-Q1-A847",
      "instrument_serial": "SN-MIT-78421",
      "vendor":          "Megger",
      "model":           "MIT525",
      "covers_test_types": ["insulation_resistance"],
      "issued_at":       "2026-01-15T00:00:00Z",
      "expires_at":      "2026-12-31T00:00:00Z",
      "issuer":          "Megger UK Ltd. (UKAS-accredited cal lab)",
      "standards":       ["NIST", "UKAS"],
      "hash":            "sha256:megger-mit525-CAL-2026-Q1-A847"
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol


@dataclass(frozen=True)
class CalCert:
    cert_id: str
    instrument_serial: str
    vendor: str
    model: str
    covers_test_types: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime
    issuer: str
    standards: tuple[str, ...]
    hash: str


class CalCertSource(Protocol):
    def get(self, cert_id: str) -> Optional[CalCert]: ...
    def list_for_instrument(self, vendor: str, model: str,
                            serial: str) -> list[CalCert]: ...


# ---------------------------------------------------------------------------
# In-memory source (dev / tests)
# ---------------------------------------------------------------------------


class InMemoryCalCertSource:
    def __init__(self):
        self._by_id: dict[str, CalCert] = {}

    def add(self, cert: CalCert) -> None:
        self._by_id[cert.cert_id] = cert

    def get(self, cert_id: str) -> Optional[CalCert]:
        return self._by_id.get(cert_id)

    def list_for_instrument(self, vendor: str, model: str,
                            serial: str) -> list[CalCert]:
        return [c for c in self._by_id.values()
                if c.vendor.lower() == vendor.lower()
                and c.model.lower() == model.lower()
                and c.instrument_serial == serial]


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


@dataclass
class VerificationResult:
    valid: bool
    reason: str = ""
    cert: Optional[CalCert] = None


class CertVerifier:
    """Verifies a test result's instrument cert against the registry."""

    def __init__(self, source: CalCertSource):
        self._source = source

    def verify(self, *, cert_id: str, expected_hash: str,
               vendor: str, model: str,
               instrument_serial: str,
               test_type: str,
               run_at: Optional[datetime] = None) -> VerificationResult:
        cert = self._source.get(cert_id)
        if cert is None:
            return VerificationResult(False, f"unknown cert_id: {cert_id}")
        if cert.hash != expected_hash:
            return VerificationResult(
                False, f"cert hash mismatch (registry={cert.hash}, "
                       f"submitted={expected_hash})", cert)
        if cert.vendor.lower() != vendor.lower():
            return VerificationResult(
                False, f"cert vendor mismatch: cert={cert.vendor}, "
                       f"submitted={vendor}", cert)
        if cert.model.lower() != model.lower():
            return VerificationResult(
                False, f"cert model mismatch: cert={cert.model}, "
                       f"submitted={model}", cert)
        if cert.instrument_serial != instrument_serial:
            return VerificationResult(
                False, f"cert serial mismatch: cert={cert.instrument_serial}, "
                       f"submitted={instrument_serial}", cert)
        if test_type not in cert.covers_test_types:
            return VerificationResult(
                False, f"cert does not cover test type {test_type!r}; "
                       f"covers {list(cert.covers_test_types)}", cert)
        ts = run_at or datetime.now(timezone.utc)
        if ts < cert.issued_at:
            return VerificationResult(
                False, f"run timestamp {ts.isoformat()} is before cert "
                       f"issuance {cert.issued_at.isoformat()}", cert)
        if ts > cert.expires_at:
            return VerificationResult(
                False, f"cert expired at {cert.expires_at.isoformat()}", cert)
        return VerificationResult(True, "valid", cert)
