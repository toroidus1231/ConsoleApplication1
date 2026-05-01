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
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, status
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
    # Equipment record provider: callable(kind, device_id) -> dict. For
    # /equipment/{kind}/{id} endpoint.
    equipment_provider: Any = None
    # Generic device-id-based equipment provider used by /api/v1/equipment/{device_id}
    equipment_by_id_provider: Any = None
    # Map device_id → effective Config Context (post-override). Used by
    # GET /api/v1/devices/{id}/active_tests to populate Launch Test UI.
    _effective_configs_for_devices: dict[str, dict] | None = None
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

    @app.get("/api/v1/devices/{device_id}/active_tests",
             dependencies=[Depends(auth)])
    async def device_active_tests(device_id: str):
        """Return the list of `active_tests` defined in this device's
        Config Context. Used by the Launch Test UI to populate the
        test_name dropdown so operators can only pick tests the
        platform actually knows how to run."""
        if deps.equipment_by_id_provider is None:
            raise HTTPException(503, "no equipment provider configured")
        try:
            panel = deps.equipment_by_id_provider(device_id)
        except KeyError:
            raise HTTPException(404, f"unknown device: {device_id}")
        # The panel record includes the device's Config Context's
        # active_tests via the loader's effective_configs cache, but we
        # need the raw test definitions, not the panel record. Pull
        # from the same provider closure if it exposes the configs.
        eff = getattr(deps, "_effective_configs_for_devices", None)
        if eff and device_id in eff:
            tests = eff[device_id].get("active_tests", []) or []
            return {"device_id": device_id,
                    "device_type_slug": eff[device_id].get("device_type_slug", ""),
                    "active_tests": [{
                        "name": t["name"], "type": t["type"],
                        "spec_reference": t.get("spec_reference", ""),
                        "instrument": t.get("instrument", {}),
                    } for t in tests]}
        return {"device_id": device_id, "active_tests": []}

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

    @app.get("/api/v1/sld/layout", dependencies=[Depends(auth)])
    async def sld_layout():
        """NetBox/BIM-driven SLD layout. Computes node positions and
        wire endpoints from the device list + power-DAG connections.
        Frontend renders generically — no hardcoded positions."""
        from src.topology.dynamic_sld import compute_layout, layout_to_dict
        connections = []
        for d in deps.facility_devices:
            ps = d.get("power_source_id")
            if ps:
                connections.append((d["device_id"], ps))
        layout = compute_layout(deps.facility_devices, connections)
        return layout_to_dict(layout)

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
        # Merge pre-recorded historical runs with any executed via this
        # orchestrator instance so the UI sees newly-launched tests.
        rows = list(deps.facility_test_runs)
        if deps.orchestrator is not None:
            for r in deps.orchestrator._results.values():
                rows.append({
                    "test_id": r.test_id, "device_id": r.device_id,
                    "test_name": r.test_name, "status": r.status,
                    "started_at": r.started_at, "completed_at": r.completed_at,
                    "duration_seconds": r.duration_seconds,
                    "evidence_hashes": r.evidence_hashes,
                })
        if device_id:
            rows = [r for r in rows if r["device_id"] == device_id]
        if status_filter:
            rows = [r for r in rows if r["status"] == status_filter]
        # Newest first
        rows.sort(key=lambda r: r.get("started_at", ""), reverse=True)
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
        subnets = body.get("subnets", []) or []
        protocols = body.get("protocols", ["modbus_tcp", "bacnet_ip", "snmp"])

        # Walk the facility devices and report any whose primary_ip falls
        # inside the requested subnets. For a real deployment this is
        # replaced with src/discovery.py which actually probes the
        # network. Here we surface the simulator topology so the UI is
        # populated with realistic devices.
        from ipaddress import ip_address, ip_network
        nets = []
        for s in subnets:
            try:
                nets.append(ip_network(s, strict=False))
            except ValueError:
                pass
        devices = []
        for d in deps.facility_devices:
            ip = d.get("primary_ip")
            if not ip:
                continue
            if d.get("protocol") not in protocols:
                continue
            try:
                addr = ip_address(ip)
            except ValueError:
                continue
            if nets and not any(addr in n for n in nets):
                continue
            devices.append({
                "ip": ip, "protocol": d.get("protocol"),
                "identity": d.get("name") or d.get("device_id"),
                "device_type_slug": d.get("device_type_slug"),
                "matched_exact": d.get("device_type_slug") not in ("unknown", None, ""),
            })

        classified = sum(1 for x in devices if x["matched_exact"])
        deps.discovery_state[scan_id] = {
            "scan_id": scan_id, "subnets": subnets,
            "status": "completed",
            "devices_found": len(devices),
            "devices_classified": classified,
            "devices_unmatched": len(devices) - classified,
            "errors": [],
            "devices": devices,
        }
        return {"scan_id": scan_id, "status": "completed",
                "devices_found": len(devices)}

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

    @app.get("/api/v1/equipment/{kind}/{device_id}", dependencies=[Depends(auth)])
    async def equipment_record(kind: str, device_id: str):
        """Legacy per-kind endpoint. Kept for backward compatibility;
        the frontend now uses /api/v1/equipment/{device_id} which
        dispatches by Config Context category."""
        if deps.equipment_provider is None:
            raise HTTPException(503, "equipment provider not configured")
        try:
            return deps.equipment_provider(kind, device_id)
        except KeyError:
            raise HTTPException(404, f"no record for {kind}/{device_id}")

    @app.get("/api/v1/equipment/{device_id}", dependencies=[Depends(auth)])
    async def equipment_by_id(device_id: str):
        """Generic equipment endpoint. Looks up the device's Config
        Context, dispatches to the right category aggregator, and
        attaches panel.layout for the frontend's generic renderer.
        Adding a new equipment family is now a config drop."""
        if deps.equipment_by_id_provider is None:
            raise HTTPException(503, "equipment provider not configured")
        try:
            return deps.equipment_by_id_provider(device_id)
        except KeyError:
            raise HTTPException(404, f"no device: {device_id}")

    # ------------------------------------------------------------------
    # Operator write paths: punchlist resolve, checklist sign-off,
    # report generation, BIM import, PDF→Config Context.
    # ------------------------------------------------------------------

    @app.patch("/api/v1/punchlist/{item_id}", dependencies=[Depends(auth)])
    async def punchlist_update(item_id: str, body: dict):
        """Resolve / re-open / change severity of a punch-list item.
        Mutates the in-memory reconciliation list and appends an
        attestation record so the change is auditable."""
        items = getattr(deps.reconciliation, "items", None) or []
        target = next((i for i in items if i.id == item_id), None)
        if target is None:
            raise HTTPException(404, f"no punch-list item: {item_id}")
        old_status = target.status
        for k in ("status", "severity", "category", "remediation"):
            if k in body:
                setattr(target, k, body[k])
        if "resolved_by" in body:
            target.resolved_by = body["resolved_by"]
        if deps.attestation is not None:
            from src.types import AttestationRecord
            await deps.attestation.submit(AttestationRecord(
                timestamp_ns=int(time.time_ns()),
                device_id=target.device_id, measurement="punchlist_status",
                value=1.0 if target.status == "resolved" else 0.0,
                raw_bytes=f"{item_id}:{old_status}->{target.status}",
                protocol="ui", source_ip="ui",
                worker_id=body.get("resolved_by", "operator"),
            ))
        return {"id": item_id, "status": target.status, "ok": True}

    @app.post("/api/v1/checklist/{device_id}/{item_id}", dependencies=[Depends(auth)])
    async def checklist_signoff(device_id: str, item_id: str, request: Request):
        """Record an operator sign-off for a physical-verification
        checklist item. Persists in deps.facility_checklists and
        attestation chain."""
        form = await request.form()
        signed_by = form.get("signed_by", "operator")
        note = form.get("note", "")
        items = deps.facility_checklists.get(device_id, [])
        item = next((i for i in items if i.get("id") == item_id), None)
        if item is None:
            raise HTTPException(404, f"no checklist item: {device_id}/{item_id}")
        item["signed"] = True
        item["signed_by"] = signed_by
        item["signed_at"] = datetime.now(timezone.utc).isoformat()
        if note:
            item["note"] = note
        if deps.attestation is not None:
            from src.types import AttestationRecord
            await deps.attestation.submit(AttestationRecord(
                timestamp_ns=int(time.time_ns()),
                device_id=device_id, measurement="checklist_signoff",
                value=1.0,
                raw_bytes=f"{item_id}:{signed_by}",
                protocol="ui", source_ip="ui",
                worker_id=signed_by,
            ))
        return {"device_id": device_id, "item_id": item_id, "ok": True,
                "signed_at": item["signed_at"]}

    @app.post("/api/v1/reports/generate", dependencies=[Depends(auth)])
    async def reports_generate(body: dict | None = None):
        """Generate a commissioning-report PDF from the current punch
        list + attestation summary. Returns the PDF bytes."""
        items = getattr(deps.reconciliation, "items", None) or []
        chain = deps.attestation.verify_chain() if deps.attestation else {
            "valid": True, "length": 0, "last_hash": "",
        }
        from src.reports import AttestationSummary, generate_pdf
        summary = AttestationSummary(
            facility=(body or {}).get("facility", "DC1-Ashburn"),
            chain_length=chain.get("length", 0),
            first_hash=chain.get("first_hash", ""),
            last_hash=chain.get("last_hash", ""),
            chain_valid=chain.get("valid", True),
        )
        pdf_bytes = generate_pdf(
            items, summary,
            title=(body or {}).get("title", "Commissioning Report"),
            subtitle=(body or {}).get("subtitle"),
        )
        return Response(
            content=pdf_bytes, media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=commissioning-report.pdf"},
        )

    @app.post("/api/v1/bim/import", dependencies=[Depends(auth)])
    async def bim_import(file: UploadFile = File(...)):
        """Parse an uploaded IFC file via src/bim_import.py and return
        the device list it produced. Persists to NetBox if configured;
        otherwise returns the parsed entities for inspection."""
        from src.bim_import import parse_design
        # Save to a tempfile so the IFC parser (which expects a path) can read it.
        import tempfile, os
        suffix = os.path.splitext(file.filename or "design.ifc")[1] or ".ifc"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name
        try:
            if deps.bim is None or not hasattr(deps.bim, "parser"):
                # No real ifcopenshell wired — accept the upload but
                # report what we'd parse.
                return {"ok": True, "filename": file.filename,
                        "size_bytes": os.path.getsize(tmp_path),
                        "note": "BIM parser not configured in this deployment; file accepted but not parsed."}
            design = parse_design(tmp_path, parser=deps.bim.parser)
            return {"ok": True, "filename": file.filename,
                    "entities": [_entity_to_dict(e) for e in design.entities],
                    "connections": design.connections}
        finally:
            os.unlink(tmp_path)

    @app.post("/api/v1/config/generate", dependencies=[Depends(auth)])
    async def config_generate(file: UploadFile = File(...),
                              device_type_slug: str = Form(...)):
        """Run an uploaded manufacturer PDF through pdf_pipeline →
        Claude → validated Config Context. Returns the generated JSON."""
        from src.pdf_pipeline import extract_config_context
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name
        try:
            if deps.pdf_pipeline is None:
                return {"ok": False,
                        "error": "PDF pipeline not configured (no PDF reader / LLM in this deployment).",
                        "device_type_slug": device_type_slug,
                        "filename": file.filename}
            result = await extract_config_context(
                tmp_path,
                pdf_reader=deps.pdf_pipeline.reader,
                llm=deps.pdf_pipeline.llm,
            )
            return {
                "ok": result.config_context is not None,
                "device_type_slug": device_type_slug,
                "config_context": result.config_context,
                "validation_errors": result.validation_errors,
                "warnings": result.warnings,
            }
        finally:
            os.unlink(tmp_path)

    return app


def _entity_to_dict(e):
    return {"id": e.id, "type": e.type, "name": e.name,
            "device_type_slug": e.device_type_slug,
            "location": e.location, "properties": e.properties}


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
