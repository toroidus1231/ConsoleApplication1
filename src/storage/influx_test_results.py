"""InfluxDB writer for TestResult metadata + per-test metric streams.

The AttestationEngine writes per-poll values to MinIO as evidence; this
module writes a parallel time-series view to Influx so you can ad-hoc
query "show every cable_hipot peak leakage on cables installed before
2018, by month, faceted by feeder ID" in Grafana.

Two measurement schemas:

    test_run                         test result summary, one point per run
        tags:    device_id, device_type_slug, test_name, status,
                 site, rack, instrument_vendor, instrument_model
        fields:  duration_seconds, peak_value, instrument_serial,
                 cert_id, evidence_object_count

    test_metric                      per-step / per-sample metric stream
        tags:    device_id, test_name, register_or_metric_name
        fields:  value, unit

Adapter wraps the existing src/influx_writer.py async client without
duplicating connection logic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol


class InfluxWriterLike(Protocol):
    async def write_points(self, points: list[dict]) -> None: ...


@dataclass
class TestRunPoint:
    measurement: str = "test_run"
    tags: dict = None
    fields: dict = None
    timestamp_ns: Optional[int] = None


class InfluxTestResultsWriter:
    """Writes TestResult records and per-monitor metric streams."""

    def __init__(self, influx: InfluxWriterLike,
                 facility: str = "DC1-Ashburn"):
        self._influx = influx
        self._facility = facility

    async def write_test_result(self, *, device_id: str,
                                device_type_slug: str,
                                test_name: str, status: str,
                                duration_seconds: float,
                                peak_value: Optional[float] = None,
                                instrument: Optional[dict] = None,
                                evidence_object_count: int = 0,
                                site: str = "",
                                rack: str = "") -> None:
        instr = instrument or {}
        point = {
            "measurement": "test_run",
            "tags": {
                "device_id": device_id,
                "device_type_slug": device_type_slug,
                "test_name": test_name,
                "status": status,
                "facility": self._facility,
                "site": site,
                "rack": rack,
                "instrument_vendor": instr.get("vendor", ""),
                "instrument_model": instr.get("model", ""),
            },
            "fields": {
                "duration_seconds": float(duration_seconds),
                "instrument_serial": instr.get("serial", ""),
                "cert_id": instr.get("cert_id", ""),
                "evidence_object_count": int(evidence_object_count),
            },
            "timestamp_ns": time.time_ns(),
        }
        if peak_value is not None:
            point["fields"]["peak_value"] = float(peak_value)
        await self._influx.write_points([point])

    async def write_metric_stream(self, *, device_id: str, test_name: str,
                                  metric_name: str, samples: list[tuple[int, float]],
                                  unit: str = "") -> None:
        """Bulk-write a stream of (timestamp_ns, value) samples for one
        metric (e.g., output_voltage during a UPS battery transfer)."""
        points = [{
            "measurement": "test_metric",
            "tags": {
                "device_id": device_id,
                "test_name": test_name,
                "metric_name": metric_name,
                "facility": self._facility,
            },
            "fields": {"value": float(v), "unit": unit},
            "timestamp_ns": int(ts),
        } for ts, v in samples]
        if points:
            await self._influx.write_points(points)
