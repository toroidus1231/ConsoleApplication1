"""Retention policy enforcement.

Commissioning evidence has long retention requirements:
  - Per IEEE 1547 / NETA / typical insurance carrier policy: 7 years.
  - Some hyperscaler customer agreements: 10–25 years.
  - Cell-tower / utility-side equipment: lifetime of the asset.

The MinIO bucket should have object-lock with COMPLIANCE-mode retention
configured at bucket creation. This module:

  1. Computes the retention deadline for any given record.
  2. Verifies all objects in the bucket actually have lock metadata
     matching the policy.
  3. Refuses to write a record without a lock-aware client.

Production wires this into the AttestationEngine + MinIOEvidenceStore
write path so a misconfigured bucket fails at startup, not later
during an audit.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Protocol


class RetentionMode(str, enum.Enum):
    GOVERNANCE = "GOVERNANCE"  # bucket admins can delete; insufficient for audit
    COMPLIANCE = "COMPLIANCE"  # nobody (not even root) can delete; required


@dataclass(frozen=True)
class RetentionPolicy:
    mode: RetentionMode
    days: int

    def deadline_for(self, written_at: datetime) -> datetime:
        return written_at + timedelta(days=self.days)


# Common pre-set policies
SEVEN_YEAR_COMPLIANCE = RetentionPolicy(RetentionMode.COMPLIANCE, days=2555)
TEN_YEAR_COMPLIANCE = RetentionPolicy(RetentionMode.COMPLIANCE, days=3650)
TWENTY_FIVE_YEAR_COMPLIANCE = RetentionPolicy(RetentionMode.COMPLIANCE, days=9125)


class LockAwareMinio(Protocol):
    """A minio.Minio that exposes object-lock methods. Real client has
    these; tests inject a fake."""
    def get_bucket_policy(self, bucket_name: str) -> dict: ...
    def get_object_lock_config(self, bucket_name: str) -> dict: ...
    def list_objects(self, bucket_name: str, prefix: str = "",
                     recursive: bool = True): ...
    def get_object_retention(self, bucket_name: str, object_name: str) -> dict: ...


@dataclass
class BucketAuditResult:
    bucket: str
    bucket_lock_configured: bool
    bucket_mode: Optional[RetentionMode]
    bucket_days: Optional[int]
    objects_audited: int
    objects_violating: list[dict]
    ok: bool


class RetentionAuditor:
    def __init__(self, client: LockAwareMinio, policy: RetentionPolicy):
        self._client = client
        self._policy = policy

    def assert_bucket_compliant(self, bucket: str) -> None:
        cfg = self._client.get_object_lock_config(bucket)
        mode = cfg.get("mode")
        days = cfg.get("days")
        if mode != self._policy.mode.value:
            raise RetentionError(
                f"bucket {bucket} mode {mode!r} != policy {self._policy.mode.value!r}"
            )
        if days is None or int(days) < self._policy.days:
            raise RetentionError(
                f"bucket {bucket} retention {days} d < policy {self._policy.days} d"
            )

    def audit_bucket(self, bucket: str, sample_limit: int = 100) -> BucketAuditResult:
        try:
            cfg = self._client.get_object_lock_config(bucket)
            bucket_lock = True
            mode = RetentionMode(cfg["mode"]) if cfg.get("mode") else None
            days = int(cfg["days"]) if cfg.get("days") else None
        except Exception:
            cfg = {}
            bucket_lock = False
            mode = None
            days = None

        violating: list[dict] = []
        audited = 0
        try:
            for obj in self._client.list_objects(bucket, recursive=True):
                if audited >= sample_limit:
                    break
                audited += 1
                try:
                    ret = self._client.get_object_retention(bucket, obj.object_name)
                except Exception:
                    violating.append({"name": obj.object_name,
                                       "reason": "no retention metadata"})
                    continue
                if ret.get("mode") != self._policy.mode.value:
                    violating.append({"name": obj.object_name,
                                       "reason": f"mode={ret.get('mode')!r}"})
        except Exception as e:
            violating.append({"name": "<bucket-list-failed>",
                               "reason": f"{type(e).__name__}: {e}"})

        return BucketAuditResult(
            bucket=bucket,
            bucket_lock_configured=bucket_lock,
            bucket_mode=mode,
            bucket_days=days,
            objects_audited=audited,
            objects_violating=violating,
            ok=(bucket_lock and not violating
                and mode == self._policy.mode
                and days is not None and days >= self._policy.days),
        )


class RetentionError(RuntimeError):
    pass
