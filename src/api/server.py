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
