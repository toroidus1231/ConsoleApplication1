"""Shared dataclasses used by every module.

Contracts spec §3.1. These are the wire types between modules. Import from here;
do not duplicate definitions elsewhere.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DeviceInfo:
    device_id: str
    name: str
    primary_ip: str
    device_type_slug: str
    config_context: dict
    protocol: str
    site: str
    rack: str
    position: int


@dataclass
class PollResult:
    device_id: str
    timestamp_ns: int
    measurements: dict
    raw_bytes: dict
    protocol: str
    source_ip: str
    success: bool
    errors: dict = field(default_factory=dict)


@dataclass
class AttestationRecord:
    timestamp_ns: int
    device_id: str
    measurement: str
    value: float
    raw_bytes: str
    protocol: str
    source_ip: str
    worker_id: str
    test_id: Optional[str] = None
    facility: str = ""
    nonce: str = ""


@dataclass
class AttestedRecord(AttestationRecord):
    hash: str = ""
    previous_hash: str = ""
    chain_id: str = ""
    sequence: int = 0


@dataclass
class TestRequest:
    test_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    device_id: str = ""
    test_name: str = ""
    requested_by: str = ""
    requested_at: str = ""
    priority: int = 3


@dataclass
class TestResult:
    test_id: str = ""
    device_id: str = ""
    test_name: str = ""
    status: str = ""
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0
    precondition_results: list = field(default_factory=list)
    manual_confirmations: list = field(default_factory=list)
    acceptance_results: list = field(default_factory=list)
    abort_triggered: bool = False
    abort_reason: Optional[str] = None
    restore_success: bool = True
    evidence_hashes: list = field(default_factory=list)
    contact_wear_before: Optional[float] = None
    contact_wear_after: Optional[float] = None


@dataclass
class PunchListItem:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    severity: str = ""
    category: str = ""
    device_id: str = ""
    device_name: str = ""
    site: str = ""
    rack: str = ""
    expected: str = ""
    actual: str = ""
    source: str = ""
    evidence_hash: Optional[str] = None
    test_id: Optional[str] = None
    remediation: str = ""
    status: str = "open"
    resolved_by: Optional[str] = None
    resolved_at: Optional[str] = None


@dataclass
class Event:
    event_type: str
    timestamp: str
    data: dict = field(default_factory=dict)
