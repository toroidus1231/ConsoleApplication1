"""Tests for src/storage/{minio_evidence_store, influx_test_results}.py."""

from __future__ import annotations

import io
import json
import pytest

from src.storage.minio_evidence_store import MinioEvidenceStore
from src.storage.influx_test_results import InfluxTestResultsWriter


# ---------------------------------------------------------------------------
# Fake MinIO
# ---------------------------------------------------------------------------


class _Obj:
    def __init__(self, name): self.object_name = name


class FakeMinio:
    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.put_calls: list[tuple] = []

    def put_object(self, bucket, name, data, length, content_type=""):
        body = data.read() if hasattr(data, "read") else bytes(data)
        self.store[name] = body
        self.put_calls.append((bucket, name, length, content_type))

    def get_object(self, bucket, name):
        return _Stream(self.store[name])

    def list_objects(self, bucket, prefix="", recursive=True):
        return [_Obj(n) for n in self.store.keys() if n.startswith(prefix)]


class _Stream:
    def __init__(self, body): self._body = body
    def read(self): return self._body
    def close(self): pass


# ---------------------------------------------------------------------------
# MinioEvidenceStore
# ---------------------------------------------------------------------------


def test_put_and_get_latest():
    s = MinioEvidenceStore(FakeMinio())
    s.put("ups-A", "ups_battery_transfer", {
        "completed_at": "2026-04-01T08:00:00",
        "passed": True, "min_voltage_v": 462.0,
    })
    s.put("ups-A", "ups_battery_transfer", {
        "completed_at": "2026-05-01T08:00:00",
        "passed": True, "min_voltage_v": 461.5,
    })
    latest = s.get_latest("ups-A", "ups_battery_transfer")
    assert latest["completed_at"] == "2026-05-01T08:00:00"
    assert latest["min_voltage_v"] == 461.5


def test_list_runs_returns_all_in_order():
    s = MinioEvidenceStore(FakeMinio())
    for d in ("2026-02-01T00:00", "2026-04-01T00:00", "2026-03-01T00:00"):
        s.put("ups-A", "ups_battery_transfer", {"completed_at": d, "passed": True})
    runs = s.list_runs("ups-A", "ups_battery_transfer")
    assert [r["completed_at"] for r in runs] == [
        "2026-02-01T00:00", "2026-03-01T00:00", "2026-04-01T00:00",
    ]


def test_list_for_device_spans_test_names():
    s = MinioEvidenceStore(FakeMinio())
    s.put("xfmr-A1", "transformer_turns_ratio", {"completed_at": "2026-01-01T00:00"})
    s.put("xfmr-A1", "polarization_index", {"completed_at": "2026-01-15T00:00"})
    s.put("ups-B", "ups_battery_transfer", {"completed_at": "2026-01-30T00:00"})
    runs = s.list_for_device("xfmr-A1")
    assert len(runs) == 2


def test_object_layout_uses_evidence_prefix():
    minio = FakeMinio()
    s = MinioEvidenceStore(minio, bucket="commissioning-evidence")
    s.put("ups-A", "ups_battery_transfer",
          {"completed_at": "2026-05-01T00:00:00", "passed": True})
    name = list(minio.store.keys())[0]
    assert name.startswith("evidence/ups-A/ups_battery_transfer/")
    assert minio.put_calls[0][0] == "commissioning-evidence"
    assert minio.put_calls[0][3] == "application/json"


def test_object_name_seq_breaks_ties_within_same_iso_second():
    minio = FakeMinio()
    s = MinioEvidenceStore(minio)
    s.put("d1", "t1", {"completed_at": "2026-05-01T00:00:00", "passed": True})
    s.put("d1", "t1", {"completed_at": "2026-05-01T00:00:00", "passed": True})
    assert len(minio.store) == 2


def test_corrupt_object_skipped_in_list():
    minio = FakeMinio()
    s = MinioEvidenceStore(minio)
    s.put("d", "t", {"completed_at": "2026-01-01T00:00:00", "passed": True})
    # Sneak in a corrupt object
    minio.store["evidence/d/t/2026-99-99T99:99:99-000099.json"] = b"not-json"
    runs = s.list_runs("d", "t")
    assert len(runs) == 1
    assert runs[0]["passed"] is True


def test_get_latest_returns_none_if_empty():
    s = MinioEvidenceStore(FakeMinio())
    assert s.get_latest("nope", "nope") is None


def test_special_chars_sanitised():
    minio = FakeMinio()
    s = MinioEvidenceStore(minio)
    s.put("dev/ice", "t e s t", {"completed_at": "2026/01/01"})
    name = list(minio.store.keys())[0]
    # The slashes in device_id and test_name shouldn't break the path
    assert name.count("/") == 3  # evidence/<dev>/<test>/<file>
    assert "dev_ice" in name
    assert "t_e_s_t" in name


# ---------------------------------------------------------------------------
# InfluxTestResultsWriter
# ---------------------------------------------------------------------------


class FakeInflux:
    def __init__(self):
        self.points: list[dict] = []

    async def write_points(self, points):
        self.points.extend(points)


@pytest.mark.asyncio
async def test_write_test_result_emits_one_point():
    flux = FakeInflux()
    w = InfluxTestResultsWriter(flux, facility="DC1-Ashburn")
    await w.write_test_result(
        device_id="ups-A", device_type_slug="apc-symmetra-mw",
        test_name="ups_battery_transfer", status="passed",
        duration_seconds=12.4, peak_value=461.4,
        instrument={"vendor": "APC", "model": "Symmetra Service Bypass",
                    "serial": "SN-SVC-1", "cert_id": "APC-2026-Q1"},
        site="DC1", rack="UPS-A",
    )
    assert len(flux.points) == 1
    p = flux.points[0]
    assert p["measurement"] == "test_run"
    assert p["tags"]["device_id"] == "ups-A"
    assert p["tags"]["instrument_vendor"] == "APC"
    assert p["fields"]["duration_seconds"] == 12.4
    assert p["fields"]["peak_value"] == 461.4
    assert p["fields"]["cert_id"] == "APC-2026-Q1"


@pytest.mark.asyncio
async def test_write_test_result_omits_peak_value_when_none():
    flux = FakeInflux()
    w = InfluxTestResultsWriter(flux)
    await w.write_test_result(
        device_id="d", device_type_slug="x", test_name="t",
        status="passed", duration_seconds=1.0,
    )
    assert "peak_value" not in flux.points[0]["fields"]


@pytest.mark.asyncio
async def test_write_metric_stream_one_point_per_sample():
    flux = FakeInflux()
    w = InfluxTestResultsWriter(flux)
    await w.write_metric_stream(
        device_id="ups-A", test_name="ups_battery_transfer",
        metric_name="output_voltage",
        samples=[(1700000000_000_000_000, 480.0),
                 (1700000000_100_000_000, 462.5),
                 (1700000000_200_000_000, 478.0)],
        unit="V",
    )
    assert len(flux.points) == 3
    for p in flux.points:
        assert p["measurement"] == "test_metric"
        assert p["tags"]["metric_name"] == "output_voltage"
        assert p["fields"]["unit"] == "V"


@pytest.mark.asyncio
async def test_write_metric_stream_empty_does_nothing():
    flux = FakeInflux()
    w = InfluxTestResultsWriter(flux)
    await w.write_metric_stream(
        device_id="d", test_name="t", metric_name="m", samples=[],
    )
    assert flux.points == []
