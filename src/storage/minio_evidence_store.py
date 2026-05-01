"""MinIO-backed implementation of the EvidenceStore protocol.

Object layout:
    evidence/<device_id>/<test_name>/<completed_at_iso>-<seq>.json

Each object body is the JSON-serialised TestResult panel record. Listing
runs for (device_id, test_name) is a prefix scan; getting the latest
sorts by object name (ISO timestamps sort lexicographically).

Production deployments wire this against a MinIO bucket with WORM/
compliance-mode retention so evidence cannot be deleted or modified
within the retention window. The same hash chain (AttestationEngine)
covers integrity.

This module deliberately mirrors the InMemoryEvidenceStore interface in
src/evidence_store.py — drop-in replacement when MinIO is configured.
"""

from __future__ import annotations

import io
import json
import re
import threading
from typing import Optional, Protocol


class MinioLike(Protocol):
    """Subset of the minio.Minio API we use. Tests inject a fake."""
    def put_object(self, bucket_name: str, object_name: str,
                   data, length: int, content_type: str = "") -> None: ...
    def get_object(self, bucket_name: str, object_name: str): ...
    def list_objects(self, bucket_name: str, prefix: str = "",
                     recursive: bool = True): ...


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitise(name: str) -> str:
    return _SAFE.sub("_", name)


class MinioEvidenceStore:
    """MinIO-backed EvidenceStore. Compatible with src/evidence_store.py."""

    def __init__(self, minio_client: MinioLike, bucket: str = "evidence"):
        self._minio = minio_client
        self._bucket = bucket
        self._seq_lock = threading.Lock()
        # In-process tie-breaker for objects within the same ISO-second
        self._seq_per_key: dict[tuple[str, str], int] = {}

    # ---------- public protocol ----------

    def put(self, device_id: str, test_name: str, record: dict) -> None:
        completed_at = record.get("completed_at", "")
        sd = _sanitise(device_id)
        st = _sanitise(test_name)
        sa = _sanitise(completed_at)
        with self._seq_lock:
            seq_key = (sd, st)
            self._seq_per_key[seq_key] = self._seq_per_key.get(seq_key, 0) + 1
            seq = self._seq_per_key[seq_key]
        key = f"evidence/{sd}/{st}/{sa}-{seq:06d}.json"
        body = json.dumps(record, separators=(",", ":")).encode()
        self._minio.put_object(self._bucket, key,
                               io.BytesIO(body), len(body),
                               content_type="application/json")

    def get_latest(self, device_id: str, test_name: str) -> Optional[dict]:
        runs = self.list_runs(device_id, test_name)
        return runs[-1] if runs else None

    def list_runs(self, device_id: str, test_name: str) -> list[dict]:
        prefix = f"evidence/{_sanitise(device_id)}/{_sanitise(test_name)}/"
        try:
            objs = list(self._minio.list_objects(self._bucket, prefix=prefix,
                                                 recursive=True))
        except Exception:
            return []
        sorted_objs = sorted(objs, key=lambda o: o.object_name)
        out: list[dict] = []
        for o in sorted_objs:
            body = self._read(o.object_name)
            if body is not None:
                out.append(body)
        return out

    def list_for_device(self, device_id: str) -> list[dict]:
        prefix = f"evidence/{_sanitise(device_id)}/"
        try:
            objs = list(self._minio.list_objects(self._bucket, prefix=prefix,
                                                 recursive=True))
        except Exception:
            return []
        sorted_objs = sorted(objs, key=lambda o: o.object_name)
        out: list[dict] = []
        for o in sorted_objs:
            body = self._read(o.object_name)
            if body is not None:
                out.append(body)
        return out

    # ---------- helpers ----------

    def _read(self, object_name: str) -> Optional[dict]:
        try:
            stream = self._minio.get_object(self._bucket, object_name)
            data = stream.read()
            try:
                stream.close()
            except Exception:
                pass
            return json.loads(data.decode())
        except Exception:
            return None
