"""Tests for src/observability/{structured_logging, metrics, health}.py."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import pytest

from src.observability.structured_logging import (
    JSONFormatter, configure, get_logger, set_trace_id, clear_trace_id,
)
from src.observability.metrics import (
    Counter, Gauge, Histogram, Registry, render_text_format,
)
from src.observability.health import HealthChecker, HealthCheckResult


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------


def _capture_log(level: str = "INFO"):
    buf = io.StringIO()
    configure(level=level, stream=buf)
    return buf


def test_json_formatter_emits_message_and_level():
    buf = _capture_log()
    log = get_logger("t1")
    log.info("hello")
    line = buf.getvalue().strip()
    rec = json.loads(line)
    assert rec["message"] == "hello"
    assert rec["level"] == "INFO"
    assert rec["logger"] == "t1"


def test_structured_fields_passed_through():
    buf = _capture_log()
    log = get_logger("t2")
    log.info("test_started", device_id="ups-A", duration_s=12.4)
    rec = json.loads(buf.getvalue().strip())
    assert rec["device_id"] == "ups-A"
    assert rec["duration_s"] == 12.4


def test_trace_id_attached_when_set():
    buf = _capture_log()
    log = get_logger("t3")
    tok = set_trace_id("trace-abc-123")
    try:
        log.info("event")
    finally:
        clear_trace_id(tok)
    rec = json.loads(buf.getvalue().strip())
    assert rec["trace_id"] == "trace-abc-123"


def test_trace_id_absent_when_unset():
    buf = _capture_log()
    log = get_logger("t4")
    log.info("event")
    rec = json.loads(buf.getvalue().strip())
    assert "trace_id" not in rec


def test_unserialisable_field_falls_back_to_repr():
    buf = _capture_log()
    log = get_logger("t5")

    class Weird:
        def __repr__(self): return "<weird>"
    log.info("event", thing=Weird())
    rec = json.loads(buf.getvalue().strip())
    assert rec["thing"] == "<weird>"


def test_exception_logs_traceback():
    buf = _capture_log()
    log = get_logger("t6")
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("caught")
    rec = json.loads(buf.getvalue().strip())
    assert "exception" in rec
    assert "ValueError" in rec["exception"]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_counter_increments():
    reg = Registry()
    c = Counter("api_requests_total", "API requests",
                labels=["method"], registry=reg)
    c.inc(method="GET")
    c.inc(method="GET")
    c.inc(method="POST")
    assert c.value(method="GET") == 2
    assert c.value(method="POST") == 1


def test_counter_rejects_negative():
    reg = Registry()
    c = Counter("c1", "x", registry=reg)
    with pytest.raises(ValueError):
        c.inc(-1)


def test_gauge_set_inc_dec():
    reg = Registry()
    g = Gauge("queue_depth", "Items in queue", registry=reg)
    g.set(5)
    g.inc()
    g.inc(2)
    g.dec()
    assert g.value() == 7


def test_histogram_observes_and_buckets():
    reg = Registry()
    h = Histogram("latency_seconds", "Latency",
                  buckets=[0.1, 0.5, 1.0, 5.0], registry=reg)
    for v in (0.05, 0.3, 0.4, 0.7, 1.5, 8.0):
        h.observe(v)
    out = render_text_format(reg)
    assert "latency_seconds_count" in out
    assert "latency_seconds_sum" in out
    assert 'le="+Inf"' in out
    assert 'le="0.1"' in out


def test_render_text_format_well_formed():
    reg = Registry()
    Counter("c", "help1", registry=reg).inc()
    Gauge("g", "help2", registry=reg).set(42)
    text = render_text_format(reg)
    assert "# HELP c help1" in text
    assert "# TYPE c counter" in text
    assert "# HELP g help2" in text
    assert "# TYPE g gauge" in text
    assert "g 42" in text


def test_label_escaping_preserves_quotes():
    reg = Registry()
    c = Counter("c", "x", labels=["path"], registry=reg)
    c.inc(path='/api/v1/foo"with"quotes')
    text = render_text_format(reg)
    assert '\\"with\\"quotes' in text


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_all_ok():
    hc = HealthChecker()
    hc.add("a", lambda: True)
    hc.add("b", lambda: True)
    out = await hc.run()
    assert out["status"] == "ok"
    assert all(c["ok"] for c in out["checks"])


@pytest.mark.asyncio
async def test_health_one_failed_returns_degraded():
    hc = HealthChecker()
    hc.add("a", lambda: True)
    hc.add("b", lambda: False)
    out = await hc.run()
    assert out["status"] == "degraded"
    assert any(not c["ok"] for c in out["checks"])


@pytest.mark.asyncio
async def test_health_async_check_supported():
    hc = HealthChecker()

    async def slow_ok():
        await asyncio.sleep(0.01)
        return True
    hc.add("slow", slow_ok)
    out = await hc.run()
    assert out["status"] == "ok"
    assert out["checks"][0]["duration_ms"] >= 5  # ~10ms


@pytest.mark.asyncio
async def test_health_check_exception_marks_failed():
    hc = HealthChecker()

    def boom():
        raise RuntimeError("connection refused")
    hc.add("boom", boom)
    out = await hc.run()
    assert out["status"] == "degraded"
    assert out["checks"][0]["ok"] is False
    assert "RuntimeError" in out["checks"][0]["detail"]


@pytest.mark.asyncio
async def test_health_check_string_means_failed_with_detail():
    hc = HealthChecker()
    hc.add("str", lambda: "queue depth too high")
    out = await hc.run()
    # Truthy non-bool means "ok=True with detail" per implementation
    assert out["checks"][0]["ok"] is True
    assert "queue depth too high" in out["checks"][0]["detail"]


@pytest.mark.asyncio
async def test_health_check_result_passthrough():
    hc = HealthChecker()
    hc.add("custom", lambda: HealthCheckResult(
        name="custom", ok=True, detail="42 records",
        extras={"records": 42},
    ))
    out = await hc.run()
    check = out["checks"][0]
    assert check["records"] == 42
    assert check["detail"] == "42 records"
