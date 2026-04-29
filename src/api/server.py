"""Module 21: FastAPI Server.

Implements the contracts spec §4 endpoints. All routes are prefixed with
/api/v1, JSON in/out, X-API-Key header auth (single key from platform.yml,
spec §6.3).

The server is constructed via ``create_app(deps)`` rather than module-level
state. Tests inject a Deps with fakes for everything (orchestrator, test
engine, attestation engine, influx writer, etc.). FastAPI's TestClient is
used in tests; uvicorn is only invoked from main.py at runtime.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sse_starlette.sse import EventSourceResponse

from ..types import Event, TestRequest


@dataclass
class Deps:
    """All runtime dependencies the API talks to. Wired up by main.py.

    Tests construct a Deps with fakes and pass it to create_app().
    """

    api_key: str
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:8080"])
    orchestrator: Any = None
    test_engine: Any = None
    attestation: Any = None
    influx: Any = None
    netbox: Any = None
    reconciliation: Any = None
    reports: Any = None
    discovery: Any = None
    pdf_pipeline: Any = None
    bim: Any = None
    event_queue: Optional[asyncio.Queue[Event]] = None
    # Optional facility model for the demo UI: list[dict] of devices and
    # the power_graph dict. Real deployments hit NetBox instead — these
    # exist so dev_server.py can drive the dashboard without NetBox.
    facility_devices: list[dict] = field(default_factory=list)
    facility_power_graph: dict = field(default_factory=dict)
    facility_test_runs: list[dict] = field(default_factory=list)
    facility_checklists: dict = field(default_factory=dict)
    discovery_state: dict = field(default_factory=dict)
    # Live telemetry callable (returns a dict ready for JSON). dev_server
    # wires this to simulator.telemetry.live_telemetry(); production wires
    # it to InfluxDB recent reads.
    telemetry_provider: Any = None
    # SSE fan-out — registered listeners get every event.
    _sse_subscribers: set[asyncio.Queue[Event]] = field(default_factory=set)


def create_app(deps: Deps) -> FastAPI:
    app = FastAPI(title="Commissioning Platform API", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=deps.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def auth(x_api_key: str | None = Header(default=None)):
        if x_api_key != deps.api_key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")

    @app.get("/api/v1/system/health")
    async def health():
        return {
            "netbox": "ok" if deps.netbox else "absent",
            "influxdb": "ok" if deps.influx else "absent",
            "minio": "ok" if deps.attestation else "absent",
            "orchestrator": "ok" if deps.orchestrator else "absent",
        }

    @app.get("/api/v1/system/events", dependencies=[Depends(auth)])
    async def system_events():
        # Each connection gets its own queue; the fan-out task copies every
        # incoming event from deps.event_queue onto every subscriber's queue.
        local: asyncio.Queue[Event] = asyncio.Queue()
        deps._sse_subscribers.add(local)

        async def gen():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(local.get(), timeout=15.0)
                        yield {"event": event.event_type, "data": json.dumps(event.data)}
                    except asyncio.TimeoutError:
                        yield {"event": "keepalive", "data": "{}"}
            finally:
                deps._sse_subscribers.discard(local)

        return EventSourceResponse(gen())

    @app.post("/api/v1/tests/run", dependencies=[Depends(auth)])
    async def run_test(body: dict):
        if not body.get("device_id") or not body.get("test_name"):
            raise HTTPException(400, "device_id and test_name are required")
        req = TestRequest(
            device_id=body["device_id"],
            test_name=body["test_name"],
            requested_by=body.get("requested_by", "api"),
            priority=int(body.get("priority", 3)),
        )
        await deps.orchestrator.submit(req)
        return {"test_id": req.test_id, "status": "queued"}

    @app.post("/api/v1/tests/confirm/{test_id}", dependencies=[Depends(auth)])
    async def confirm_test(test_id: str, body: dict):
        ok = deps.test_engine.confirm_manual(test_id, body.get("confirmed_by", "unknown"))
        if not ok:
            raise HTTPException(404, f"no test pending confirmation with id {test_id}")
        return {"status": "confirmed"}

    @app.get("/api/v1/tests/status/{test_id}", dependencies=[Depends(auth)])
    async def test_status(test_id: str):
        if test_id not in deps.orchestrator._results:
            raise HTTPException(404, f"unknown test_id: {test_id}")
        return asdict(deps.orchestrator._results[test_id])

    @app.get("/api/v1/punchlist", dependencies=[Depends(auth)])
    async def punchlist():
        items = getattr(deps.reconciliation, "items", [])
        return {"items": [asdict(i) for i in items]}

    @app.get("/api/v1/punchlist/summary", dependencies=[Depends(auth)])
    async def punchlist_summary():
        items = getattr(deps.reconciliation, "items", [])
        by_severity: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for item in items:
            by_severity[item.severity] = by_severity.get(item.severity, 0) + 1
            by_category[item.category] = by_category.get(item.category, 0) + 1
        return {
            "total": len(items),
            "by_severity": by_severity,
            "by_category": by_category,
        }

    @app.get("/api/v1/attestation/verify", dependencies=[Depends(auth)])
    async def attestation_verify():
        if not deps.attestation:
            raise HTTPException(503, "attestation engine not configured")
        return deps.attestation.verify_chain()

    @app.get("/api/v1/attestation/certificate", dependencies=[Depends(auth)])
    async def attestation_certificate():
        if not deps.attestation:
            raise HTTPException(503, "attestation engine not configured")
        return {
            "facility": deps.attestation._chain_id,
            "chain_length": deps.attestation.sequence,
            "last_hash": deps.attestation.previous_hash,
        }

    @app.get("/api/v1/attestation/{record_hash}", dependencies=[Depends(auth)])
    async def attestation_by_hash(record_hash: str):
        """Return one AttestedRecord by its SHA-256 hash. Used by the punch
        list evidence modal to render the chained record + raw bytes."""
        if not deps.attestation:
            raise HTTPException(503, "attestation engine not configured")
        for obj in deps.attestation._minio.list_objects(
            deps.attestation._bucket, prefix=f"{deps.attestation._chain_id}/", recursive=True
        ):
            stream = deps.attestation._minio.get_object(
                deps.attestation._bucket, obj.object_name
            )
            try:
                body = stream.read()
            finally:
                if hasattr(stream, "close"):
                    stream.close()
            record = json.loads(body)
            if record.get("hash") == record_hash:
                return {"record": record}
        raise HTTPException(404, f"no attested record with hash {record_hash}")

    # ------------------------------------------------------------------
    # Facility model (devices, power graph, test runs)
    # ------------------------------------------------------------------

    @app.get("/api/v1/devices", dependencies=[Depends(auth)])
    async def devices(protocol: str | None = None, site: str | None = None,
                      rack: str | None = None):
        rows = deps.facility_devices
        if protocol:
            rows = [d for d in rows if d.get("protocol") == protocol]
        if site:
            rows = [d for d in rows if d.get("site") == site]
        if rack:
            rows = [d for d in rows if d.get("rack") == rack]
        return {"devices": rows}

    @app.get("/api/v1/devices/{device_id}", dependencies=[Depends(auth)])
    async def device_detail(device_id: str):
        for d in deps.facility_devices:
            if d.get("device_id") == device_id:
                runs = [r for r in deps.facility_test_runs if r["device_id"] == device_id]
                return {"device": d, "tests_run": runs}
        raise HTTPException(404, f"unknown device {device_id}")

    @app.get("/api/v1/power-graph", dependencies=[Depends(auth)])
    async def power_graph_endpoint():
        return deps.facility_power_graph

    @app.get("/api/v1/racks", dependencies=[Depends(auth)])
    async def racks():
        """Return rack-elevation data: { rack: [device, ...] sorted by U, desc }."""
        by_rack: dict[str, list[dict]] = {}
        for d in deps.facility_devices:
            by_rack.setdefault(d.get("rack", ""), []).append(d)
        for rack in by_rack:
            by_rack[rack].sort(key=lambda d: d.get("position", 0), reverse=True)
        return {"racks": by_rack}

    @app.get("/api/v1/tests/history", dependencies=[Depends(auth)])
    async def test_history(device_id: str | None = None, status_filter: str | None = None):
        rows = deps.facility_test_runs
        if device_id:
            rows = [r for r in rows if r["device_id"] == device_id]
        if status_filter:
            rows = [r for r in rows if r["status"] == status_filter]
        return {"tests": rows}

    @app.get("/api/v1/tests/live/{test_id}", dependencies=[Depends(auth)])
    async def tests_live(test_id: str):
        """SSE stream: per-monitor-register values during an active test.

        Subscribes to the global event queue and forwards `poll_result`
        events whose data carries this test_id. Each yield is one register
        sample so a Recharts strip-chart can append in place.
        """
        local: asyncio.Queue[Event] = asyncio.Queue()
        deps._sse_subscribers.add(local)

        async def gen():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(local.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield {"event": "keepalive", "data": "{}"}
                        continue
                    if event.event_type != "poll_result":
                        continue
                    data = event.data or {}
                    if data.get("test_id") and data.get("test_id") != test_id:
                        continue
                    measurements = data.get("measurements") or {}
                    for register, value in measurements.items():
                        yield {
                            "event": "sample",
                            "data": json.dumps({
                                "test_id": test_id,
                                "register": register,
                                "value": value,
                                "timestamp": event.timestamp,
                            }),
                        }
            finally:
                deps._sse_subscribers.discard(local)

        return EventSourceResponse(gen())

    # ------------------------------------------------------------------
    # Discovery (Module 11) — exposes scan progress to the UI
    # ------------------------------------------------------------------

    @app.post("/api/v1/discovery/scan", dependencies=[Depends(auth)])
    async def discovery_scan(body: dict):
        scan_id = body.get("scan_id") or "scan-" + str(len(deps.discovery_state) + 1)
        subnets = body.get("subnets", [])
        deps.discovery_state[scan_id] = {
            "scan_id": scan_id, "subnets": subnets,
            "status": "running", "devices_found": 0,
            "devices_classified": 0, "devices_unmatched": 0,
            "errors": [],
        }
        return {"scan_id": scan_id, "status": "running"}

    @app.get("/api/v1/discovery/status/{scan_id}", dependencies=[Depends(auth)])
    async def discovery_status(scan_id: str):
        if scan_id not in deps.discovery_state:
            raise HTTPException(404, f"unknown scan_id {scan_id}")
        return deps.discovery_state[scan_id]

    @app.get("/api/v1/discovery/results/{scan_id}", dependencies=[Depends(auth)])
    async def discovery_results(scan_id: str):
        if scan_id not in deps.discovery_state:
            raise HTTPException(404, f"unknown scan_id {scan_id}")
        return {"scan_id": scan_id, "devices": deps.discovery_state[scan_id].get("devices", [])}

    # ------------------------------------------------------------------
    # Checklists (Module 19)
    # ------------------------------------------------------------------

    @app.get("/api/v1/checklist/{device_id}", dependencies=[Depends(auth)])
    async def checklist_get(device_id: str):
        items = deps.facility_checklists.get(device_id, [])
        return {"items": items}

    # ------------------------------------------------------------------
    # Live telemetry — drives the SLD / relay console / SOE banner
    # ------------------------------------------------------------------

    @app.get("/api/v1/telemetry", dependencies=[Depends(auth)])
    async def telemetry():
        if deps.telemetry_provider is None:
            return {
                "timestamp": "", "breakers": {}, "buses": {}, "relays": {},
                "xfmrs": {}, "gens": {}, "upses": {}, "soe": [],
            }
        return deps.telemetry_provider()

    return app


async def fanout_loop(deps: Deps) -> None:
    """Background task: copy every event from deps.event_queue onto every
    SSE subscriber's queue. Cancel to stop."""
    if deps.event_queue is None:
        return
    while True:
        event = await deps.event_queue.get()
        for sub in list(deps._sse_subscribers):
            try:
                sub.put_nowait(event)
            except asyncio.QueueFull:
                continue
