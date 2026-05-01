"""Per-key rate limiter middleware (token bucket).

Each subject (token sub or API-key id) gets a bucket: capacity burst,
refilled at sustained_rate per second. When empty, return HTTP 429 with
a Retry-After header.

Storage is in-memory by default — fine for single-process deployments.
For multi-instance, swap the backing dict for a Redis-backed token
bucket; the interface is the same.

Defaults pulled from spec §6.5: 30 req/s sustained, 60 burst per key.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse


@dataclass
class Bucket:
    capacity: int
    refill_per_second: float
    tokens: float = field(default=0.0)
    last_refill: float = field(default_factory=time.monotonic)

    def take(self, n: float = 1.0) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds_if_blocked)."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.last_refill = now
        if self.tokens >= n:
            self.tokens -= n
            return True, 0.0
        deficit = n - self.tokens
        return False, deficit / self.refill_per_second


class RateLimiter:
    """Holds per-subject buckets. Use as an async dependency or via the
    middleware below."""

    def __init__(self, *, capacity: int = 60, refill_per_second: float = 30.0):
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self._buckets: dict[str, Bucket] = {}
        self._lock = asyncio.Lock()

    async def check(self, subject: str) -> tuple[bool, float]:
        async with self._lock:
            bucket = self._buckets.get(subject)
            if bucket is None:
                bucket = Bucket(capacity=self.capacity,
                                refill_per_second=self.refill_per_second,
                                tokens=self.capacity)
                self._buckets[subject] = bucket
            return bucket.take()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """ASGI middleware. Subject derived from X-API-Key header (dev) or
    Authorization Bearer JWT (prod). Anonymous requests share an
    'anonymous' bucket so unauth health-checks aren't a back door."""

    def __init__(self, app, limiter: RateLimiter,
                 subject_resolver: Callable[[Request], str] | None = None,
                 exempt_paths: set[str] = frozenset()):
        super().__init__(app)
        self._limiter = limiter
        self._subject = subject_resolver or self._default_subject
        self._exempt = set(exempt_paths)

    @staticmethod
    def _default_subject(request: Request) -> str:
        auth = request.headers.get("authorization")
        if auth and auth.startswith("Bearer "):
            # Use the raw token tail as bucket key; sufficient since token
            # uniquely identifies the subject.
            return f"bearer:{auth[7:][:32]}"
        api_key = request.headers.get("x-api-key")
        if api_key:
            return f"apikey:{api_key[:32]}"
        return "anonymous"

    async def dispatch(self, request: Request,
                       call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.url.path in self._exempt:
            return await call_next(request)
        subject = self._subject(request)
        allowed, retry_after = await self._limiter.check(subject)
        if not allowed:
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": f"{retry_after:.2f}"},
            )
        return await call_next(request)
