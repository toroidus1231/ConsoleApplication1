"""Audit-review tooling.

Compliance auditors need to traverse the platform's records by
arbitrary filters (date range, device, test type, operator, fault
severity) and export them in evidence-packet form. This module exposes
a query interface over the AttestationEngine + EvidenceStore that
compiles to deterministic, paginatable results.

Intended consumers:
  - Internal compliance team building energization reports.
  - External auditors / insurance reviewers downloading evidence
    packets keyed to a punch-list cycle.
  - Regulator inspections (NEC / NFPA Article 110.16 sign-off).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol


class EvidenceStoreLike(Protocol):
    def list_for_device(self, device_id: str) -> list[dict]: ...
    def list_runs(self, device_id: str, test_name: str) -> list[dict]: ...


class AttestationLike(Protocol):
    def verify_chain(self) -> dict: ...


@dataclass
class AuditQuery:
    device_ids: Optional[list[str]] = None
    test_names: Optional[list[str]] = None
    started_after: Optional[datetime] = None
    started_before: Optional[datetime] = None
    statuses: Optional[list[str]] = None  # e.g., ["failed", "aborted"]
    operator: Optional[str] = None
    instrument_vendor: Optional[str] = None


@dataclass
class AuditPage:
    rows: list[dict]
    total: int
    page: int
    page_size: int


class AuditReview:
    def __init__(self, evidence: EvidenceStoreLike,
                 attestation: AttestationLike):
        self._ev = evidence
        self._att = attestation

    def query(self, q: AuditQuery, *, page: int = 1, page_size: int = 50,
              all_devices: Optional[list[str]] = None) -> AuditPage:
        device_ids = q.device_ids or all_devices or []
        rows: list[dict] = []
        for did in device_ids:
            for run in self._ev.list_for_device(did):
                if not _match(run, q):
                    continue
                rows.append(run)
        rows.sort(key=lambda r: r.get("completed_at", ""), reverse=True)
        start = (page - 1) * page_size
        end = start + page_size
        return AuditPage(
            rows=rows[start:end], total=len(rows),
            page=page, page_size=page_size,
        )

    def chain_status(self) -> dict:
        return self._att.verify_chain()

    def export_packet(self, q: AuditQuery,
                      all_devices: Optional[list[str]] = None) -> dict:
        page = self.query(q, page=1, page_size=10_000,
                          all_devices=all_devices)
        return {
            "query": _query_to_dict(q),
            "row_count": page.total,
            "rows": page.rows,
            "attestation": self._att.verify_chain(),
            "exported_at": datetime.utcnow().isoformat(),
        }


def _query_to_dict(q: AuditQuery) -> dict:
    return {
        "device_ids": q.device_ids,
        "test_names": q.test_names,
        "started_after": q.started_after.isoformat() if q.started_after else None,
        "started_before": q.started_before.isoformat() if q.started_before else None,
        "statuses": q.statuses,
        "operator": q.operator,
        "instrument_vendor": q.instrument_vendor,
    }


def _match(run: dict, q: AuditQuery) -> bool:
    if q.test_names and run.get("test_name") not in q.test_names:
        # Fall back to test_type field for older records
        if run.get("test_type") not in q.test_names:
            return False
    if q.statuses:
        s = run.get("status") or ("passed" if run.get("passed") else "failed")
        if s not in q.statuses:
            return False
    if q.operator and run.get("operator") != q.operator:
        return False
    if q.instrument_vendor:
        instr = run.get("instrument") or {}
        if instr.get("vendor", "").lower() != q.instrument_vendor.lower():
            return False
    completed = run.get("completed_at", "")
    if q.started_after and completed < q.started_after.isoformat():
        return False
    if q.started_before and completed > q.started_before.isoformat():
        return False
    return True
