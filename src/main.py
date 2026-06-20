"""Application entry point (implementation guide — main.py).

The composition root. Loads platform.yml, constructs every backend service,
wires them into a :class:`~src.api.state.Platform`, and runs the FastAPI server
alongside the attestation consumer, the WAL recovery loop, the SSE pump, the
per-protocol polling workers, and the test orchestrator — all as tasks in one
asyncio event loop (contracts spec §1.1).

This module is the only place the concrete heavy clients (MinIO, InfluxDB) and
the real protocol pollers are stitched together; every individual module is
independently importable and tested. Run with ``python -m src.main``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile

import uvicorn

from .api.server import create_app
from .api.state import Platform
from .attestation import AttestationEngine
from .config import PlatformConfig, load_config
from .influx_writer import InfluxWriter
from .netbox_reader import get_devices_by_protocol
from .orchestrator import Orchestrator
from .test_engine import TestEngine
from .timeutil import iso_now
from .types import Event
from .workers.base import ProtocolWorker
from .workers.modbus_worker import make_modbus_worker

log = logging.getLogger("main")

PROTOCOLS = ("modbus_tcp", "bacnet_ip", "snmp", "nvml")


def _build_workers(config, influx, attestation, event_queue) -> dict[str, ProtocolWorker]:
    """One polling worker per protocol. The non-Modbus pollers are lazily
    constructed so importing this module never pulls in BAC0/pysnmp/pynvml."""
    from .pollers.bacnet import poll_device as bacnet_poll
    from .pollers.nvml_poller import poll_device as nvml_poll
    from .pollers.snmp import poll_device as snmp_poll

    poll_fns = {
        "bacnet_ip": bacnet_poll,
        "snmp": snmp_poll,
        "nvml": nvml_poll,
    }
    workers: dict[str, ProtocolWorker] = {
        "modbus_tcp": make_modbus_worker(config, influx, attestation, event_queue)
    }
    for protocol, poll_fn in poll_fns.items():
        workers[protocol] = ProtocolWorker(
            protocol=protocol, poll_fn=poll_fn, influx=influx,
            attestation=attestation, event_queue=event_queue,
            netbox_url=config.netbox_url, netbox_token=config.netbox_token,
            max_concurrent_polls=config.max_concurrent_polls,
        )
    return workers


async def _all_devices(config):
    devices = []
    for protocol in PROTOCOLS:
        try:
            devices += await get_devices_by_protocol(config.netbox_url, config.netbox_token, protocol)
        except Exception as e:  # noqa: BLE001 — NetBox may be briefly unavailable
            log.warning("device fetch failed for %s: %s", protocol, e)
    return devices


def _build_platform(config: PlatformConfig) -> tuple[Platform, list]:
    from minio import Minio

    event_queue: asyncio.Queue = asyncio.Queue()

    influx = InfluxWriter(config.influxdb_url, config.influxdb_token,
                          config.influxdb_org, config.influxdb_bucket)
    minio_client = Minio(config.minio_endpoint, access_key=config.minio_access_key,
                         secret_key=config.minio_secret_key, secure=False)
    attestation = AttestationEngine(config.facility_name, config.minio_bucket, minio_client)

    test_engine = TestEngine(attestation=attestation, influx=influx, event_queue=event_queue)
    orchestrator = Orchestrator(test_engine=test_engine, devices={},
                                max_concurrent_tests=config.max_concurrent_tests,
                                event_queue=event_queue)
    workers = _build_workers(config, influx, attestation, event_queue)

    platform = Platform(config=config, influx=influx, orchestrator=orchestrator,
                        test_engine=test_engine, attestation=attestation,
                        attestation_reader=MinioAttestationReader(minio_client, config.minio_bucket,
                                                                  config.facility_name, attestation),
                        workers=workers, event_queue=event_queue)
    platform.get_devices = lambda: _all_devices(config)
    _wire_jobs(platform, config, attestation, minio_client)

    # Orchestrator dispatches results into the platform's test-result store.
    orchestrator_results = orchestrator.results
    platform.test_results = orchestrator_results

    tasks = [
        attestation.run(),
        attestation.wal_recovery_loop(),
        platform.broadcaster.pump(event_queue),
        orchestrator.run(),
        *[w.run() for w in workers.values()],
    ]
    return platform, tasks


def _wire_jobs(platform: Platform, config: PlatformConfig, attestation, minio_client) -> None:
    """Bind the API's async-job seams to the real module functions."""

    async def discovery_fn(subnets):
        from .discovery import default_probes, scan
        return await scan(subnets, probes=default_probes(),
                          netbox_url=config.netbox_url, netbox_token=config.netbox_token)

    async def reconcile_fn():
        from .reconciliation import reconcile_from_netbox
        return await reconcile_from_netbox(config.netbox_url, config.netbox_token,
                                           test_results={r.device_id: [r] for r in platform.test_results.values()})

    async def report_fn(sections):
        from .reports import build_report_data, generate_pdf
        summary = platform.attestation_reader.verify_chain()
        data = build_report_data(config.facility_name, platform.punchlist, summary)
        return generate_pdf(data)

    async def bim_preview_fn(data: bytes):
        from .bim_import import load_ifc, parse_model
        with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as f:
            f.write(data)
            path = f.name
        return await asyncio.to_thread(lambda: parse_model(load_ifc(path)))

    async def bim_commit_fn(devices):
        from .bim_import import import_to_netbox
        return await import_to_netbox(devices, netbox_url=config.netbox_url,
                                      netbox_token=config.netbox_token)

    async def pdf_fn(data: bytes, slug: str):
        from .pdf_pipeline import pdf_to_config_context
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(data)
            path = f.name
        key = (config.external_apis.get("anthropic", {}) or {}).get("token", "")
        return await pdf_to_config_context(path, slug, api_key=key)

    platform.discovery_fn = discovery_fn
    platform.reconcile_fn = reconcile_fn
    platform.report_fn = report_fn
    platform.bim_preview_fn = bim_preview_fn
    platform.bim_commit_fn = bim_commit_fn
    platform.pdf_fn = pdf_fn
    platform.health_probe = lambda: _health_probe(config)


async def _health_probe(config) -> dict:
    import httpx

    async def ok(url: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(url)
                return "ok" if r.status_code < 500 else "error"
        except Exception:  # noqa: BLE001
            return "error"

    return {
        "netbox": await ok(f"{config.netbox_url}/api/status/"),
        "influxdb": await ok(f"{config.influxdb_url}/health"),
        "minio": await ok(f"http://{config.minio_endpoint}/minio/health/live"),
    }


class MinioAttestationReader:
    """Reads attested records back out of MinIO for the API (Module 21 §4.5)."""

    def __init__(self, minio_client, bucket, chain_id, engine):
        self._minio = minio_client
        self._bucket = bucket
        self._chain_id = chain_id
        self._engine = engine

    def _load_all(self) -> list[dict]:
        recs = []
        for obj in sorted(self._minio.list_objects(self._bucket, prefix=f"{self._chain_id}/", recursive=True),
                          key=lambda o: o.object_name):
            stream = self._minio.get_object(self._bucket, obj.object_name)
            try:
                recs.append(json.loads(stream.read()))
            finally:
                stream.close()
        return recs

    def get_by_hash(self, h):
        return next((r for r in self._load_all() if r.get("hash") == h), None)

    def chain(self, from_sequence: int, count: int):
        recs = [r for r in self._load_all() if r.get("sequence", 0) >= from_sequence]
        return recs[:count]

    def verify(self, h):
        recs = self._load_all()
        record = next((r for r in recs if r.get("hash") == h), None)
        result = self._engine.verify_chain()
        return {"valid": record is not None and result.get("chain_valid", False),
                "chain_position": record.get("sequence") if record else None,
                "chain_length": result.get("length", len(recs)),
                "breaks": result.get("breaks", [])}

    def verify_chain(self):
        result = dict(self._engine.verify_chain())
        recs = self._load_all()
        result["first"] = recs[0] if recs else None
        result["last"] = recs[-1] if recs else None
        return result


async def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    config = load_config(os.environ.get("PLATFORM_CONFIG", "/app/config/platform.yml"))
    platform, tasks = _build_platform(config)

    platform.publish(Event("worker_health", iso_now(), {"status": "starting"}))
    app = create_app(platform)
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=config.api_port,
                                           log_level=config.log_level.lower()))
    await asyncio.gather(server.serve(), *tasks)


if __name__ == "__main__":
    asyncio.run(main())
