"""Module 21: FastAPI Server.

Implements every endpoint from contracts spec §4 and serves the React frontend.
Routes delegate to the backend modules through a :class:`~src.api.state.Platform`
object (injected at construction), so the server is unit-tested against fakes via
an ASGI transport with no real NetBox/InfluxDB/MinIO.

Auth (spec §6.3): a single API key in the ``X-API-Key`` header. Browsers can't
set headers on an ``EventSource``, so the SSE endpoints also accept the key as an
``?api_key=`` query parameter.

Async operations (discovery, reconciliation, report/PDF/BIM generation) are
started as background tasks that populate in-memory job stores; the matching
``/status`` endpoints report progress (spec §4.1/§4.7/§4.8/§4.9).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import uuid
from dataclasses import asdict
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from ..timeutil import iso_now, ns_now
from ..types import AttestationRecord, Event, TestRequest
from .state import Platform

FRONTEND_DIR = "/app/frontend/build"


def create_app(platform: Platform) -> FastAPI:
    app = FastAPI(title="Commissioning Platform", version="1.0.0")
    app.state.platform = platform

    app.add_middleware(
        CORSMiddleware,
        allow_origins=platform.config.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- Auth ----------------------------------------------------------
    async def verify_key(
        request: Request,
        x_api_key: str | None = Header(default=None),
    ) -> None:
        key = x_api_key or request.query_params.get("api_key")
        if key != platform.config.api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    auth = [Depends(verify_key)]

    # ==================================================================
    # 4.1 Discovery
    # ==================================================================
    @app.post("/api/v1/discovery/scan", dependencies=auth)
    async def discovery_scan(body: dict | None = None):
        subnets = (body or {}).get("subnets") or platform.config.scan_subnets
        scan_id = str(uuid.uuid4())
        platform.scans[scan_id] = {"scan_id": scan_id, "status": "running",
                                   "devices_found": 0, "devices_classified": 0,
                                   "devices_unmatched": 0, "errors": [], "devices": []}

        async def _run():
            try:
                result = await platform.discovery_fn(subnets)
                platform.scans[scan_id].update(
                    status="complete",
                    devices_found=result.devices_found,
                    devices_classified=result.devices_classified,
                    devices_unmatched=result.devices_unmatched,
                    errors=result.errors,
                    devices=result.devices,
                )
            except Exception as e:  # noqa: BLE001
                platform.scans[scan_id].update(status="error", errors=[str(e)])

        if platform.discovery_fn:
            asyncio.create_task(_run())
        return {"scan_id": scan_id, "status": "running"}

    @app.get("/api/v1/discovery/status/{scan_id}", dependencies=auth)
    async def discovery_status(scan_id: str):
        scan = _require(platform.scans.get(scan_id), "scan")
        return {k: scan[k] for k in
                ("scan_id", "status", "devices_found", "devices_classified",
                 "devices_unmatched", "errors")}

    @app.get("/api/v1/discovery/results/{scan_id}", dependencies=auth)
    async def discovery_results(scan_id: str):
        scan = _require(platform.scans.get(scan_id), "scan")
        return {"devices": scan["devices"]}

    # ==================================================================
    # 4.2 Devices
    # ==================================================================
    @app.get("/api/v1/devices", dependencies=auth)
    async def list_devices(protocol: str | None = None, site: str | None = None):
        devices = await platform.devices()
        if protocol:
            devices = [d for d in devices if d.protocol == protocol]
        if site:
            devices = [d for d in devices if d.site == site]
        return {"devices": [asdict(d) for d in devices]}

    @app.get("/api/v1/devices/{device_id}", dependencies=auth)
    async def get_device(device_id: str):
        device = _require(await platform.find_device(device_id), "device")
        last = platform.last_poll.get(device_id)
        tests_run = [asdict(r) for r in platform.test_results.values()
                     if r.device_id == device_id]
        return {
            "device": asdict(device),
            "last_poll": asdict(last) if last else None,
            "tests_available": [t["name"] for t in platform.available_tests(device)],
            "tests_run": tests_run,
        }

    @app.get("/api/v1/devices/{device_id}/history", dependencies=auth)
    async def device_history(device_id: str, register: str,
                             from_: str = Query("-30d", alias="from"),
                             to: str = Query("now()", alias="to")):
        if not platform.influx:
            return {"points": []}
        rows = await platform.influx.query_register_history(device_id, register, from_, to)
        return {"points": [{"timestamp": r["timestamp"], "value": r["value"]} for r in rows]}

    # ==================================================================
    # 4.3 Tests
    # ==================================================================
    @app.get("/api/v1/tests/available/{device_id}", dependencies=auth)
    async def tests_available(device_id: str):
        device = _require(await platform.find_device(device_id), "device")
        return {"tests": platform.available_tests(device)}

    @app.post("/api/v1/tests/run", dependencies=auth)
    async def run_test(body: dict):
        req = TestRequest(device_id=body["device_id"], test_name=body["test_name"],
                          requested_by=body.get("requested_by", "api"),
                          requested_at=iso_now())
        if platform.orchestrator:
            await platform.orchestrator.submit(req)
        return {"test_id": req.test_id, "status": "queued"}

    @app.post("/api/v1/tests/confirm/{test_id}", dependencies=auth)
    async def confirm_test(test_id: str, body: dict):
        if platform.test_engine:
            platform.test_engine.confirm_manual(test_id, body.get("confirmed_by", "unknown"))
        return {"status": "confirmed"}

    @app.get("/api/v1/tests/status/{test_id}", dependencies=auth)
    async def test_status(test_id: str):
        result = platform.test_results.get(test_id)
        if result is None and platform.orchestrator is not None:
            result = getattr(platform.orchestrator, "results", {}).get(test_id)
        return asdict(_require(result, "test"))

    @app.get("/api/v1/tests/history", dependencies=auth)
    async def tests_history(device_id: str | None = None, status: str | None = None):
        results = list(platform.test_results.values())
        if platform.orchestrator is not None:
            results = list({**{r.test_id: r for r in results},
                            **getattr(platform.orchestrator, "results", {})}.values())
        if device_id:
            results = [r for r in results if r.device_id == device_id]
        if status:
            results = [r for r in results if r.status == status]
        return {"tests": [asdict(r) for r in results]}

    @app.post("/api/v1/tests/abort/{test_id}", dependencies=auth)
    async def abort_test(test_id: str):
        platform.publish(Event("test_aborted", iso_now(), {"test_id": test_id}))
        return {"status": "aborting"}

    @app.get("/api/v1/tests/live/{test_id}")
    async def test_live(test_id: str, request: Request, api_key: str | None = None):
        await verify_key(request, None)
        return EventSourceResponse(_sse_stream(platform, request,
                                               test_filter=test_id))

    # ==================================================================
    # 4.4 Punch List
    # ==================================================================
    @app.get("/api/v1/punchlist", dependencies=auth)
    async def punchlist(severity: str | None = None, category: str | None = None,
                        status: str | None = None):
        items = platform.punchlist
        if severity:
            items = [i for i in items if i.severity == severity]
        if category:
            items = [i for i in items if i.category == category]
        if status:
            items = [i for i in items if i.status == status]
        return {"items": [asdict(i) for i in items]}

    @app.patch("/api/v1/punchlist/{item_id}", dependencies=auth)
    async def update_punch(item_id: str, body: dict):
        for item in platform.punchlist:
            if item.id == item_id:
                item.status = body.get("status", item.status)
                item.resolved_by = body.get("resolved_by", item.resolved_by)
                item.resolved_at = iso_now() if item.status == "resolved" else item.resolved_at
                return {"item": asdict(item)}
        raise HTTPException(status_code=404, detail="punch item not found")

    @app.get("/api/v1/punchlist/summary", dependencies=auth)
    async def punchlist_summary():
        return _summary(platform.punchlist)

    # ==================================================================
    # 4.5 Attestation
    # ==================================================================
    @app.get("/api/v1/attestation/certificate", dependencies=auth)
    async def attestation_certificate():
        verify = _verify_chain(platform)
        return {
            "facility": platform.config.facility_name,
            "chain_length": verify.get("length", 0),
            "chain_valid": verify.get("chain_valid", False),
            "first_record": verify.get("first"),
            "last_record": verify.get("last"),
            "hash_algorithm": "SHA-256",
        }

    @app.get("/api/v1/attestation/chain", dependencies=auth)
    async def attestation_chain(from_sequence: int = 0, count: int = 50):
        reader = platform.attestation_reader
        records = reader.chain(from_sequence, count) if reader else []
        verify = _verify_chain(platform)
        return {"records": records, "chain_valid": verify.get("chain_valid", False)}

    @app.get("/api/v1/attestation/verify/{record_hash}", dependencies=auth)
    async def attestation_verify(record_hash: str):
        reader = platform.attestation_reader
        if reader is None:
            raise HTTPException(status_code=404, detail="no attestation reader")
        return reader.verify(record_hash)

    @app.get("/api/v1/attestation/{record_hash}", dependencies=auth)
    async def attestation_get(record_hash: str):
        reader = platform.attestation_reader
        record = reader.get_by_hash(record_hash) if reader else None
        return {"record": _require(record, "attestation record")}

    # ==================================================================
    # 4.6 Physical Checklist
    # ==================================================================
    @app.get("/api/v1/checklist/summary", dependencies=auth)
    async def checklist_summary():
        subs = list(platform.checklist_submissions.values())
        completed = sum(1 for s in subs if s.get("completed"))
        return {"total_items": len(subs), "completed": completed,
                "remaining": len(subs) - completed, "by_device": _by_device(subs)}

    @app.get("/api/v1/checklist/{device_id}", dependencies=auth)
    async def checklist(device_id: str):
        device = _require(await platform.find_device(device_id), "device")
        items = []
        for c in device.config_context.get("checklist", []) or []:
            sub = platform.checklist_submissions.get(f"{device_id}:{c['id']}")
            items.append({
                "id": c["id"], "description": c.get("description", ""),
                "category": c.get("category", ""),
                "required_photo": c.get("requires_photo", False),
                "completed": bool(sub and sub.get("completed")),
            })
        return {"items": items}

    @app.post("/api/v1/checklist/{device_id}/{item_id}", dependencies=auth)
    async def submit_checklist(device_id: str, item_id: str,
                               completed: bool = Form(True), notes: str = Form(""),
                               photo: UploadFile | None = File(default=None)):
        key = f"{device_id}:{item_id}"
        photo_name = photo.filename if photo else None
        submission = {"device_id": device_id, "item_id": item_id,
                      "completed": completed, "notes": notes,
                      "photo": photo_name, "timestamp": iso_now()}
        attestation_hash = hashlib.sha256(
            json.dumps(submission, sort_keys=True).encode()).hexdigest()
        submission["attestation_hash"] = attestation_hash
        platform.checklist_submissions[key] = submission
        if platform.attestation:
            await platform.attestation.submit(AttestationRecord(
                timestamp_ns=ns_now(), device_id=device_id,
                measurement=f"checklist:{item_id}", value=1.0 if completed else 0.0,
                raw_bytes=attestation_hash, protocol="physical_checklist",
                source_ip="", worker_id=""))
        return {"item": submission, "attestation_hash": attestation_hash}

    # ==================================================================
    # 4.7 Reconciliation and Reports
    # ==================================================================
    @app.post("/api/v1/reconciliation/run", dependencies=auth)
    async def reconciliation_run():
        job_id = str(uuid.uuid4())
        platform.recon_jobs[job_id] = {"status": "running", "punch_items_generated": 0}

        async def _run():
            try:
                items = await platform.reconcile_fn()
                platform.punchlist = items
                platform.recon_jobs[job_id].update(status="complete",
                                                   punch_items_generated=len(items))
            except Exception as e:  # noqa: BLE001
                platform.recon_jobs[job_id].update(status="error", error=str(e))

        if platform.reconcile_fn:
            asyncio.create_task(_run())
        return {"job_id": job_id, "status": "running"}

    @app.get("/api/v1/reconciliation/status/{job_id}", dependencies=auth)
    async def reconciliation_status(job_id: str):
        return _require(platform.recon_jobs.get(job_id), "job")

    @app.post("/api/v1/reports/generate", dependencies=auth)
    async def reports_generate(body: dict | None = None):
        sections = (body or {}).get("include_sections",
                                    ["discovery", "tests", "punchlist", "attestation"])
        report_id = str(uuid.uuid4())
        platform.reports[report_id] = {"status": "generating", "pdf": None}

        async def _run():
            try:
                pdf = await platform.report_fn(sections)
                platform.reports[report_id].update(status="complete", pdf=pdf)
            except Exception as e:  # noqa: BLE001
                platform.reports[report_id].update(status="error", error=str(e))

        if platform.report_fn:
            asyncio.create_task(_run())
        return {"report_id": report_id, "status": "generating"}

    @app.get("/api/v1/reports/{report_id}", dependencies=auth)
    async def reports_get(report_id: str):
        job = _require(platform.reports.get(report_id), "report")
        if job.get("status") != "complete" or not job.get("pdf"):
            raise HTTPException(status_code=409, detail=f"report {job.get('status')}")
        return Response(content=job["pdf"], media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{report_id}.pdf"'})

    # ==================================================================
    # 4.8 BIM Import
    # ==================================================================
    @app.post("/api/v1/bim/import", dependencies=auth)
    async def bim_import(file: UploadFile):
        data = await file.read()
        job_id = str(uuid.uuid4())
        platform.bim_jobs[job_id] = {"status": "parsing", "devices_parsed": 0,
                                     "devices_created": 0, "errors": [], "devices": []}

        async def _run():
            try:
                devices = await platform.bim_preview_fn(data)
                platform.bim_jobs[job_id].update(status="parsed",
                                                 devices_parsed=len(devices),
                                                 devices=devices)
            except Exception as e:  # noqa: BLE001
                platform.bim_jobs[job_id].update(status="error", errors=[str(e)])

        if platform.bim_preview_fn:
            asyncio.create_task(_run())
        return {"job_id": job_id, "status": "parsing"}

    @app.get("/api/v1/bim/status/{job_id}", dependencies=auth)
    async def bim_status(job_id: str):
        job = _require(platform.bim_jobs.get(job_id), "job")
        return {k: job[k] for k in ("status", "devices_parsed", "devices_created", "errors")}

    @app.get("/api/v1/bim/preview/{job_id}", dependencies=auth)
    async def bim_preview(job_id: str):
        job = _require(platform.bim_jobs.get(job_id), "job")
        return {"devices": job.get("devices", [])}

    @app.post("/api/v1/bim/commit/{job_id}", dependencies=auth)
    async def bim_commit(job_id: str):
        job = _require(platform.bim_jobs.get(job_id), "job")
        result = await platform.bim_commit_fn(job.get("devices", [])) if platform.bim_commit_fn else {"devices_committed": 0}
        job.update(status="committed", devices_created=result.get("devices_committed", 0))
        return {"devices_committed": result.get("devices_committed", 0)}

    # ==================================================================
    # 4.9 Config Context Generation
    # ==================================================================
    @app.post("/api/v1/config/generate", dependencies=auth)
    async def config_generate(pdf: UploadFile, device_type_slug: str = Form("")):
        data = await pdf.read()
        job_id = str(uuid.uuid4())
        platform.config_jobs[job_id] = {"status": "extracting", "result": None,
                                        "device_type_slug": device_type_slug}

        async def _run():
            try:
                result = await platform.pdf_fn(data, device_type_slug)
                platform.config_jobs[job_id].update(status="complete", result=result)
            except Exception as e:  # noqa: BLE001
                platform.config_jobs[job_id].update(status="error", error=str(e))

        if platform.pdf_fn:
            asyncio.create_task(_run())
        return {"job_id": job_id, "status": "extracting"}

    @app.get("/api/v1/config/result/{job_id}", dependencies=auth)
    async def config_result(job_id: str):
        job = _require(platform.config_jobs.get(job_id), "job")
        result = job.get("result") or {}
        return {"config_context": result.get("config_context"),
                "validation_errors": result.get("validation_errors", []),
                "warnings": result.get("warnings", [])}

    @app.post("/api/v1/config/commit/{job_id}", dependencies=auth)
    async def config_commit(job_id: str):
        job = _require(platform.config_jobs.get(job_id), "job")
        job["status"] = "committed"
        return {"status": "committed"}

    # ==================================================================
    # 4.10 System
    # ==================================================================
    @app.get("/api/v1/system/health", dependencies=auth)
    async def system_health():
        infra = await platform.health_probe() if platform.health_probe else {}
        workers = {
            name: {"devices": len(getattr(w, "devices", [])),
                   "errors": len(getattr(w, "unreachable", set()))}
            for name, w in platform.workers.items()
        }
        return {"netbox": infra.get("netbox", "unknown"),
                "influxdb": infra.get("influxdb", "unknown"),
                "minio": infra.get("minio", "unknown"),
                "workers": workers}

    @app.get("/api/v1/system/config", dependencies=auth)
    async def system_config():
        return platform.redacted_config()

    @app.get("/api/v1/system/events")
    async def system_events(request: Request, api_key: str | None = None):
        await verify_key(request, None)
        return EventSourceResponse(_sse_stream(platform, request))

    # ---- Static frontend (optional; absent during tests) ---------------
    if Path(FRONTEND_DIR).is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

    return app


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _require(value, what: str):
    if value is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return value


def _summary(items) -> dict:
    by_severity = {s: 0 for s in ("critical", "major", "minor", "info")}
    by_status = {s: 0 for s in ("open", "acknowledged", "resolved", "deferred")}
    by_category: dict[str, int] = {}
    for i in items:
        by_severity[i.severity] = by_severity.get(i.severity, 0) + 1
        by_status[i.status] = by_status.get(i.status, 0) + 1
        by_category[i.category] = by_category.get(i.category, 0) + 1
    return {"total": len(items), "by_severity": by_severity,
            "by_category": by_category, "by_status": by_status}


def _by_device(subs) -> list[dict]:
    devices: dict[str, dict] = {}
    for s in subs:
        d = devices.setdefault(s["device_id"], {"device_id": s["device_id"], "completed": 0})
        if s.get("completed"):
            d["completed"] += 1
    return list(devices.values())


def _verify_chain(platform: Platform) -> dict:
    if platform.attestation_reader and hasattr(platform.attestation_reader, "verify_chain"):
        return platform.attestation_reader.verify_chain()
    if platform.attestation and hasattr(platform.attestation, "verify_chain"):
        try:
            return platform.attestation.verify_chain()
        except Exception:  # noqa: BLE001 - reading the whole chain may be unavailable
            return {}
    return {}


async def _sse_stream(platform: Platform, request: Request, test_filter: str | None = None):
    """Yield SSE messages from a fresh broadcaster subscription until the client
    disconnects. Emits a keepalive every 30s of silence."""
    queue = platform.broadcaster.subscribe()
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                event: Event = await asyncio.wait_for(queue.get(), timeout=30)
            except asyncio.TimeoutError:
                yield {"event": "keepalive", "data": "{}"}
                continue
            if test_filter and event.data.get("test_id") != test_filter:
                continue
            yield {"event": event.event_type, "data": json.dumps(event.data)}
    finally:
        platform.broadcaster.unsubscribe(queue)
