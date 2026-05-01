"""Tests for rate-limit + audit-log middleware."""

from __future__ import annotations

import asyncio
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.middleware.audit_log import (
    AuditLogMiddleware, InMemorySink, AuditEntry,
)
from src.middleware.rate_limit import (
    Bucket, RateLimiter, RateLimitMiddleware,
)


# ---------------------------------------------------------------------------
# Bucket math
# ---------------------------------------------------------------------------


def test_bucket_take_succeeds_when_full():
    b = Bucket(capacity=10, refill_per_second=1.0, tokens=10)
    ok, retry = b.take(1)
    assert ok and retry == 0.0


def test_bucket_take_fails_when_empty():
    b = Bucket(capacity=10, refill_per_second=1.0, tokens=0)
    ok, retry = b.take(1)
    assert not ok
    assert retry == pytest.approx(1.0, rel=0.1)


def test_bucket_refills_over_time():
    import time
    b = Bucket(capacity=10, refill_per_second=10.0, tokens=0,
               last_refill=time.monotonic() - 1.0)
    ok, _ = b.take(1)
    assert ok  # 1s of refill at 10/s should give us tokens


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_limiter_allows_within_capacity():
    rl = RateLimiter(capacity=5, refill_per_second=1)
    for _ in range(5):
        ok, _ = await rl.check("alice")
        assert ok


@pytest.mark.asyncio
async def test_limiter_blocks_when_exhausted():
    rl = RateLimiter(capacity=2, refill_per_second=0.1)
    await rl.check("bob")
    await rl.check("bob")
    ok, retry = await rl.check("bob")
    assert not ok
    assert retry > 0


@pytest.mark.asyncio
async def test_limiter_isolates_subjects():
    rl = RateLimiter(capacity=1, refill_per_second=0.01)
    ok_a, _ = await rl.check("alice")
    ok_b, _ = await rl.check("bob")
    assert ok_a and ok_b
    # Both exhausted now
    ok_a2, _ = await rl.check("alice")
    ok_b2, _ = await rl.check("bob")
    assert not ok_a2 and not ok_b2


# ---------------------------------------------------------------------------
# RateLimitMiddleware integration
# ---------------------------------------------------------------------------


def _build_app(limiter: RateLimiter, **mw_kwargs) -> Starlette:
    async def hello(request):
        return PlainTextResponse("ok")
    app = Starlette(routes=[Route("/hello", hello)])
    app.add_middleware(RateLimitMiddleware, limiter=limiter, **mw_kwargs)
    return app


def test_middleware_returns_429_when_exhausted():
    rl = RateLimiter(capacity=2, refill_per_second=0.01)
    client = TestClient(_build_app(rl))
    r1 = client.get("/hello", headers={"X-API-Key": "demo"})
    r2 = client.get("/hello", headers={"X-API-Key": "demo"})
    r3 = client.get("/hello", headers={"X-API-Key": "demo"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429
    assert "Retry-After" in r3.headers


def test_middleware_bypasses_exempt_paths():
    rl = RateLimiter(capacity=1, refill_per_second=0.01)
    client = TestClient(_build_app(rl, exempt_paths={"/hello"}))
    for _ in range(5):
        r = client.get("/hello")
        assert r.status_code == 200


def test_middleware_isolates_by_api_key():
    rl = RateLimiter(capacity=1, refill_per_second=0.01)
    client = TestClient(_build_app(rl))
    assert client.get("/hello", headers={"X-API-Key": "alice"}).status_code == 200
    assert client.get("/hello", headers={"X-API-Key": "alice"}).status_code == 429
    # bob still has tokens
    assert client.get("/hello", headers={"X-API-Key": "bob"}).status_code == 200


# ---------------------------------------------------------------------------
# AuditLogMiddleware
# ---------------------------------------------------------------------------


def _audit_app(sink, **kwargs):
    async def hello(request):
        return PlainTextResponse("ok")
    async def boom(request):
        raise RuntimeError("planned failure")
    app = Starlette(routes=[Route("/hello", hello, methods=["GET", "POST"]),
                            Route("/boom", boom, methods=["POST"])])
    app.add_middleware(AuditLogMiddleware, sink=sink, **kwargs)
    return app


def test_audit_log_records_writes_only_by_default():
    sink = InMemorySink()
    client = TestClient(_audit_app(sink))
    client.get("/hello")
    assert sink.entries() == []
    client.post("/hello", content=b"x")
    assert len(sink.entries()) == 1
    e = sink.entries()[0]
    assert e.method == "POST"
    assert e.path == "/hello"
    assert e.status_code == 200
    assert e.request_size == 1


def test_audit_log_can_record_reads_too():
    sink = InMemorySink()
    client = TestClient(_audit_app(sink, record_reads=True))
    client.get("/hello")
    assert len(sink.entries()) == 1


def test_audit_log_records_exception_path():
    sink = InMemorySink()
    client = TestClient(_audit_app(sink))
    with pytest.raises(RuntimeError):
        client.post("/boom", content=b"")
    entries = sink.entries()
    assert len(entries) == 1
    assert entries[0].extra.get("exception") is True


def test_audit_log_attaches_request_id_header():
    sink = InMemorySink()
    client = TestClient(_audit_app(sink))
    r = client.post("/hello", content=b"")
    assert "X-Request-Id" in r.headers
    rid = r.headers["X-Request-Id"]
    assert sink.entries()[0].request_id == rid


def test_audit_log_uses_supplied_request_id():
    sink = InMemorySink()
    client = TestClient(_audit_app(sink))
    r = client.post("/hello", content=b"", headers={"X-Request-Id": "trace-abc-123"})
    assert r.headers["X-Request-Id"] == "trace-abc-123"
    assert sink.entries()[0].request_id == "trace-abc-123"
