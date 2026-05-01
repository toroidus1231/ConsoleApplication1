"""Prometheus-compatible metrics exporter.

No external prometheus_client dependency — this is a small zero-deps
implementation of counters, gauges, and histograms with the standard
text exposition format. Plenty for a commissioning platform's metric
needs (request count, latency, test runs by status, attestation chain
length, queue depth, evidence-store sizes).

Usage:

    from src.observability.metrics import (
        Counter, Gauge, Histogram, render_text_format, REGISTRY,
    )
    requests = Counter("api_requests_total", "API requests",
                       labels=["method", "path", "status"])
    requests.inc(method="GET", path="/devices", status="200")

    # In FastAPI:
    @app.get("/metrics")
    async def metrics():
        return Response(render_text_format(REGISTRY),
                        media_type="text/plain; version=0.0.4")
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Iterable


class Registry:
    def __init__(self):
        self._metrics: list[Metric] = []
        self._lock = threading.Lock()

    def register(self, metric: "Metric") -> None:
        with self._lock:
            self._metrics.append(metric)

    def metrics(self) -> list["Metric"]:
        with self._lock:
            return list(self._metrics)


REGISTRY = Registry()


def _ls(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    parts = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(labels.items()))
    return "{" + parts + "}"


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Metric:
    NAME: str = ""
    HELP: str = ""
    TYPE: str = ""

    def __init__(self, name: str, help: str, labels: list[str] | None = None,
                 registry: Registry = REGISTRY):
        self.name = name
        self.help = help
        self.label_names = list(labels or [])
        self._lock = threading.Lock()
        registry.register(self)

    def expose(self) -> Iterable[str]:
        raise NotImplementedError


class Counter(Metric):
    TYPE = "counter"

    def __init__(self, name, help, labels=None, registry=REGISTRY):
        super().__init__(name, help, labels, registry)
        self._values: dict[tuple, float] = {}

    def inc(self, amount: float = 1.0, **labels) -> None:
        if amount < 0:
            raise ValueError("counter must increase by ≥ 0")
        key = tuple(labels.get(n, "") for n in self.label_names)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, **labels) -> float:
        key = tuple(labels.get(n, "") for n in self.label_names)
        return self._values.get(key, 0.0)

    def expose(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help}"
        yield f"# TYPE {self.name} counter"
        with self._lock:
            for key, v in self._values.items():
                lbl = dict(zip(self.label_names, key))
                yield f"{self.name}{_ls(lbl)} {v}"


class Gauge(Metric):
    TYPE = "gauge"

    def __init__(self, name, help, labels=None, registry=REGISTRY):
        super().__init__(name, help, labels, registry)
        self._values: dict[tuple, float] = {}

    def set(self, value: float, **labels) -> None:
        key = tuple(labels.get(n, "") for n in self.label_names)
        with self._lock:
            self._values[key] = float(value)

    def inc(self, amount: float = 1.0, **labels) -> None:
        key = tuple(labels.get(n, "") for n in self.label_names)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, **labels) -> None:
        self.inc(-amount, **labels)

    def value(self, **labels) -> float:
        key = tuple(labels.get(n, "") for n in self.label_names)
        return self._values.get(key, 0.0)

    def expose(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help}"
        yield f"# TYPE {self.name} gauge"
        with self._lock:
            for key, v in self._values.items():
                lbl = dict(zip(self.label_names, key))
                yield f"{self.name}{_ls(lbl)} {v}"


@dataclass
class _HistogramState:
    bucket_counts: list[int] = field(default_factory=list)
    sum: float = 0.0
    count: int = 0


class Histogram(Metric):
    TYPE = "histogram"

    def __init__(self, name, help, *, buckets: list[float], labels=None,
                 registry=REGISTRY):
        super().__init__(name, help, labels, registry)
        self.buckets = sorted(set(buckets))
        if not self.buckets or self.buckets[-1] != float("inf"):
            self.buckets = list(self.buckets) + [float("inf")]
        self._states: dict[tuple, _HistogramState] = {}

    def observe(self, value: float, **labels) -> None:
        key = tuple(labels.get(n, "") for n in self.label_names)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = _HistogramState(bucket_counts=[0] * len(self.buckets))
                self._states[key] = state
            state.sum += value
            state.count += 1
            for i, b in enumerate(self.buckets):
                if value <= b:
                    state.bucket_counts[i] += 1

    def expose(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help}"
        yield f"# TYPE {self.name} histogram"
        with self._lock:
            for key, state in self._states.items():
                lbl = dict(zip(self.label_names, key))
                cumulative = 0
                for b, count in zip(self.buckets, state.bucket_counts):
                    cumulative += count - cumulative if False else count
                    # Already cumulative-counted via observe; use directly
                    bound = "+Inf" if b == float("inf") else f"{b}"
                    bucket_lbl = dict(lbl, le=bound)
                    yield f"{self.name}_bucket{_ls(bucket_lbl)} {state.bucket_counts[self.buckets.index(b)]}"
                yield f"{self.name}_sum{_ls(lbl)} {state.sum}"
                yield f"{self.name}_count{_ls(lbl)} {state.count}"


def render_text_format(registry: Registry = REGISTRY) -> str:
    lines = []
    for m in registry.metrics():
        lines.extend(m.expose())
    return "\n".join(lines) + "\n"
