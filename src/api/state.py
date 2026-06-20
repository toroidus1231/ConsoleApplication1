"""Shared application state for the API server (Module 21).

``Platform`` holds references to every backend service plus the in-memory stores
the API needs (discovery scans, generated reports, async jobs, the punch list,
test results, the latest poll per device). Everything is injectable and defaults
to ``None``/empty, so the API is unit-tested with fakes and wired to the real
modules in ``main.py``.

The async-job functions (discovery, reconciliation, report, BIM, PDF) are stored
as callables so the server can kick them off as background tasks without
importing every module — and so tests can inject trivial fakes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..config import PlatformConfig
from ..types import DeviceInfo, Event, PollResult, PunchListItem, TestResult
from .events import EventBroadcaster


@dataclass
class Platform:
    config: PlatformConfig

    # --- Backend services (duck-typed; see the modules for the real shapes) ---
    get_devices: Callable[[], Awaitable[list[DeviceInfo]]] | None = None
    influx: Any = None              # InfluxWriter (query_register_history)
    orchestrator: Any = None        # Orchestrator (submit, results)
    test_engine: Any = None         # TestEngine (confirm_manual)
    attestation: Any = None         # AttestationEngine (verify_chain)
    attestation_reader: Any = None  # get_by_hash / chain / verify
    workers: dict[str, Any] = field(default_factory=dict)
    health_probe: Callable[[], Awaitable[dict]] | None = None

    # --- Async job functions (injectable) ---
    discovery_fn: Callable[[list[str]], Awaitable[Any]] | None = None
    reconcile_fn: Callable[[], Awaitable[list[PunchListItem]]] | None = None
    report_fn: Callable[[list[str]], Awaitable[bytes]] | None = None
    bim_preview_fn: Callable[[bytes], Awaitable[list[dict]]] | None = None
    bim_commit_fn: Callable[[list[dict]], Awaitable[dict]] | None = None
    pdf_fn: Callable[[bytes, str], Awaitable[dict]] | None = None

    # --- Eventing ---
    broadcaster: EventBroadcaster = field(default_factory=EventBroadcaster)
    event_queue: asyncio.Queue = field(default_factory=asyncio.Queue)

    # --- In-memory stores ---
    scans: dict[str, dict] = field(default_factory=dict)
    reports: dict[str, dict] = field(default_factory=dict)
    bim_jobs: dict[str, dict] = field(default_factory=dict)
    config_jobs: dict[str, dict] = field(default_factory=dict)
    recon_jobs: dict[str, dict] = field(default_factory=dict)
    punchlist: list[PunchListItem] = field(default_factory=list)
    test_results: dict[str, TestResult] = field(default_factory=dict)
    last_poll: dict[str, PollResult] = field(default_factory=dict)
    checklist_submissions: dict[str, dict] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def devices(self) -> list[DeviceInfo]:
        return await self.get_devices() if self.get_devices else []

    async def find_device(self, device_id: str) -> DeviceInfo | None:
        for d in await self.devices():
            if d.device_id == device_id:
                return d
        return None

    @staticmethod
    def available_tests(device: DeviceInfo) -> list[dict]:
        tests = []
        for t in device.config_context.get("active_tests", []) or []:
            tests.append(
                {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "preconditions": t.get("preconditions", []),
                }
            )
        return tests

    def redacted_config(self) -> dict:
        """Platform config with secrets stripped (contracts spec §4.10)."""
        c = self.config
        return {
            "facility_name": c.facility_name,
            "scan_subnets": c.scan_subnets,
            "netbox_url": c.netbox_url,
            "influxdb_url": c.influxdb_url,
            "influxdb_org": c.influxdb_org,
            "influxdb_bucket": c.influxdb_bucket,
            "minio_endpoint": c.minio_endpoint,
            "minio_bucket": c.minio_bucket,
            "minio_retention_days": c.minio_retention_days,
            "max_concurrent_polls": c.max_concurrent_polls,
            "max_concurrent_tests": c.max_concurrent_tests,
            "api_port": c.api_port,
            "cors_origins": c.cors_origins,
            "ntp_server": c.ntp_server,
            "log_level": c.log_level,
        }

    def publish(self, event: Event) -> None:
        self.broadcaster.publish(event)
