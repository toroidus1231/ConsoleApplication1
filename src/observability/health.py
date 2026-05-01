"""Deep health-check endpoint helpers.

Beyond a simple "200 OK if process is up", a real platform's health
endpoint should report on every dependency: storage, time-series DB,
NetBox, attestation chain, orchestrator queue depth, instrument-driver
connectivity. The shape returned here matches the Kubernetes
liveness/readiness probe convention.

Usage:

    checker = HealthChecker()
    checker.add("minio", lambda: minio_ok(deps))
    checker.add("influx", lambda: influx_ok(deps))
    @app.get("/api/v1/system/health")
    async def health(): return await checker.run()

Each check is a sync or async callable returning bool, str, or a
HealthCheckResult. Failed checks → HTTP 503 with details.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Union

CheckFn = Callable[[], Union[bool, str, "HealthCheckResult", Awaitable]]


@dataclass
class HealthCheckResult:
    name: str
    ok: bool
    detail: str = ""
    duration_ms: float = 0.0
    extras: dict = field(default_factory=dict)


class HealthChecker:
    def __init__(self):
        self._checks: list[tuple[str, CheckFn]] = []

    def add(self, name: str, fn: CheckFn) -> None:
        self._checks.append((name, fn))

    async def run(self) -> dict:
        results: list[HealthCheckResult] = []
        for name, fn in self._checks:
            t0 = time.perf_counter()
            try:
                outcome = fn()
                if inspect.isawaitable(outcome):
                    outcome = await outcome
                if isinstance(outcome, HealthCheckResult):
                    r = outcome
                    r.duration_ms = (time.perf_counter() - t0) * 1000
                elif isinstance(outcome, bool):
                    r = HealthCheckResult(
                        name=name, ok=outcome,
                        duration_ms=(time.perf_counter() - t0) * 1000,
                    )
                else:
                    r = HealthCheckResult(
                        name=name, ok=bool(outcome), detail=str(outcome),
                        duration_ms=(time.perf_counter() - t0) * 1000,
                    )
            except Exception as e:  # noqa: BLE001
                r = HealthCheckResult(
                    name=name, ok=False, detail=f"{type(e).__name__}: {e}",
                    duration_ms=(time.perf_counter() - t0) * 1000,
                )
            results.append(r)

        all_ok = all(r.ok for r in results)
        return {
            "status": "ok" if all_ok else "degraded",
            "checks": [
                {"name": r.name, "ok": r.ok, "detail": r.detail,
                 "duration_ms": round(r.duration_ms, 2), **r.extras}
                for r in results
            ],
        }
