"""Evidence store: per-test panel records produced by the test engine.

The platform's flow is:
    test_engine.execute(...)
        -> emits poll stream (Influx) + attestation records (MinIO chain)
        -> on completion, writes one panel-shaped EvidenceRecord here
    api/equipment/{kind}/{device_id}
        -> evidence_store.get_latest(device_id, test_name)
        -> returns the panel dict the engine wrote, or {"no_prior_run": True}

This module deliberately does NOT generate any commissioning data — it
only stores and retrieves what the engine produced. Demo data ships as
historical runs seeded via .put() so the API path is identical between
demo and production.
"""

from __future__ import annotations

from typing import Optional, Protocol


class EvidenceStore(Protocol):
    def put(self, device_id: str, test_name: str, record: dict) -> None: ...
    def get_latest(self, device_id: str, test_name: str) -> Optional[dict]: ...
    def list_for_device(self, device_id: str) -> list[dict]: ...
    def list_runs(self, device_id: str, test_name: str) -> list[dict]: ...


class InMemoryEvidenceStore:
    """Dev/test implementation. Production swaps in a MinIO-backed store
    that puts each record at evidence/<device_id>/<test_name>/<run_id>.json."""

    def __init__(self):
        self._records: dict[tuple[str, str], list[dict]] = {}

    def put(self, device_id: str, test_name: str, record: dict) -> None:
        key = (device_id, test_name)
        # Records are kept in chronological order (oldest first).
        self._records.setdefault(key, []).append(dict(record))

    def get_latest(self, device_id: str, test_name: str) -> Optional[dict]:
        runs = self._records.get((device_id, test_name), [])
        return runs[-1] if runs else None

    def list_runs(self, device_id: str, test_name: str) -> list[dict]:
        return list(self._records.get((device_id, test_name), []))

    def list_for_device(self, device_id: str) -> list[dict]:
        out: list[dict] = []
        for (did, _), runs in self._records.items():
            if did == device_id:
                out.extend(runs)
        return sorted(out, key=lambda r: r.get("completed_at", ""))
