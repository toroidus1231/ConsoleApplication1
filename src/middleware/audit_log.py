"""Audit log middleware.

Every write request (POST / PATCH / PUT / DELETE) is recorded with:
  timestamp, subject, method, path, status_code, request_size,
  response_size, duration_ms, source_ip, user_agent, request_id.

The attestation chain captures the SUBSTANCE of writes (test runs,
checklist sign-offs, punchlist resolutions). The audit log captures
the PROCESS (who hit which endpoint, when, with what outcome). Useful
for incident review and compliance audits.

Backend is pluggable via AuditSink protocol. Default sink writes JSON
lines to stdout (captured by Loki / Cloud Logging in production).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


@dataclass
class AuditEntry:
    request_id: str
    timestamp_ns: int
    subject: str
    method: str
    path: str
    status_code: int
    request_size: int
    response_size: int
    duration_ms: float
    source_ip: str
    user_agent: str = ""
    extra: dict = field(default_factory=dict)


class AuditSink(Protocol):
    async def write(self, entry: AuditEntry) -> None: ...


class StdoutJSONSink:
    """Default sink — writes JSON lines to stdout."""

    async def write(self, entry: AuditEntry) -> None:
        import sys
        sys.stdout.write(json.dumps({
            "type": "audit",
            "request_id": entry.request_id,
            "timestamp_ns": entry.timestamp_ns,
            "subject": entry.subject,
            "method": entry.method,
            "path": entry.path,
            "status_code": entry.status_code,
            "request_size": entry.request_size,
            "response_size": entry.response_size,
            "duration_ms": entry.duration_ms,
            "source_ip": entry.source_ip,
            "user_agent": entry.user_agent,
            **entry.extra,
        }) + "\n")
        sys.stdout.flush()


class InMemorySink:
    """Useful for tests. Keeps the last N entries, exposes them as a list."""

    def __init__(self, max_entries: int = 1000):
        self._entries: list[AuditEntry] = []
        self._max = max_entries

    async def write(self, entry: AuditEntry) -> None:
        self._entries.append(entry)
        if len(self._entries) > self._max:
            self._entries = self._entries[-self._max:]

    def entries(self) -> list[AuditEntry]:
        return list(self._entries)


WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}


class AuditLogMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, sink: AuditSink,
                 subject_resolver: Callable[[Request], str] | None = None,
                 record_reads: bool = False):
        super().__init__(app)
        self._sink = sink
        self._subject = subject_resolver or self._default_subject
        self._record_reads = record_reads

    @staticmethod
    def _default_subject(request: Request) -> str:
        auth = request.headers.get("authorization")
        if auth and auth.startswith("Bearer "):
            return f"bearer:{auth[7:][:16]}…"
        api_key = request.headers.get("x-api-key")
        if api_key:
            return f"apikey:{api_key[:16]}"
        return "anonymous"

    async def dispatch(self, request: Request,
                       call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if not self._record_reads and request.method not in WRITE_METHODS:
            return await call_next(request)

        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        t0 = time.perf_counter()
        body = await request.body()
        # Re-attach the body for downstream — Starlette consumes it once.
        from starlette.types import Receive

        async def replay() -> dict:
            return {"type": "http.request", "body": body, "more_body": False}
        request._receive = replay  # type: ignore[attr-defined]

        try:
            response = await call_next(request)
        except Exception:
            await self._sink.write(AuditEntry(
                request_id=rid, timestamp_ns=time.time_ns(),
                subject=self._subject(request), method=request.method,
                path=request.url.path, status_code=500,
                request_size=len(body), response_size=0,
                duration_ms=(time.perf_counter() - t0) * 1000,
                source_ip=request.client.host if request.client else "",
                user_agent=request.headers.get("user-agent", ""),
                extra={"exception": True},
            ))
            raise

        # Buffer the response so we can size it without breaking streaming
        # consumers (PDF/SSE responses). We don't buffer streaming.
        response_size = -1
        if hasattr(response, "body") and isinstance(response.body, (bytes, bytearray)):
            response_size = len(response.body)

        await self._sink.write(AuditEntry(
            request_id=rid, timestamp_ns=time.time_ns(),
            subject=self._subject(request), method=request.method,
            path=request.url.path, status_code=response.status_code,
            request_size=len(body), response_size=response_size,
            duration_ms=(time.perf_counter() - t0) * 1000,
            source_ip=request.client.host if request.client else "",
            user_agent=request.headers.get("user-agent", ""),
        ))
        response.headers["X-Request-Id"] = rid
        return response
