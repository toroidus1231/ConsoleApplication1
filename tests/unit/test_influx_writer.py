"""Tests for Module 4 — InfluxDB Writer.

Uses a fake InfluxDBClientAsync to capture the points/queries generated without
needing a running InfluxDB.
"""

import pytest

from src.influx_writer import InfluxWriter, _build_points
from src.types import PollResult


class FakeWriteApi:
    def __init__(self):
        self.calls: list[dict] = []

    async def write(self, bucket, org, record):
        self.calls.append({"bucket": bucket, "org": org, "records": record})


class FakeRecord:
    def __init__(self, ts, value):
        self._ts = ts
        self._value = value

    def get_time(self):
        return self._ts

    def get_value(self):
        return self._value


class FakeTable:
    def __init__(self, records):
        self.records = records


class FakeQueryApi:
    def __init__(self, tables=None):
        self.tables = tables or []
        self.last_query = None
        self.last_org = None

    async def query(self, query, org):
        self.last_query = query
        self.last_org = org
        return self.tables


class FakeClient:
    def __init__(self, write_api=None, query_api=None):
        self._write_api = write_api or FakeWriteApi()
        self._query_api = query_api or FakeQueryApi()
        self.closed = False

    def write_api(self):
        return self._write_api

    def query_api(self):
        return self._query_api

    async def close(self):
        self.closed = True


def _poll(measurements=None, ts_ns=1_712_847_600_000_000_000) -> PollResult:
    if measurements is None:
        measurements = {"frequency_hz": 60.01, "voltage_ab": 480.2}
    return PollResult(
        device_id="dev-123",
        timestamp_ns=ts_ns,
        measurements=measurements,
        raw_bytes={"frequency_hz": "003c", "voltage_ab": "01e0"},
        protocol="modbus_tcp",
        source_ip="10.0.0.5",
        success=True,
    )


async def test_write_poll_uses_poll_measurement_without_test_id():
    fake = FakeClient()
    writer = InfluxWriter(org="commissioning", bucket="commissioning", client=fake)

    await writer.write_poll(_poll())

    assert len(fake._write_api.calls) == 1
    call = fake._write_api.calls[0]
    assert call["bucket"] == "commissioning"
    assert call["org"] == "commissioning"
    # two registers => two points
    assert len(call["records"]) == 2
    line = call["records"][0].to_line_protocol()
    assert line.startswith("poll,")
    assert "device_id=dev-123" in line
    assert "protocol=modbus_tcp" in line
    assert "source_ip=10.0.0.5" in line


async def test_write_poll_uses_test_measurement_with_test_id():
    fake = FakeClient()
    writer = InfluxWriter(org="o", bucket="b", client=fake)

    await writer.write_poll(_poll(), test_id="test-abc")

    line = fake._write_api.calls[0]["records"][0].to_line_protocol()
    assert line.startswith("test,")
    assert "test_id=test-abc" in line


async def test_empty_measurements_issues_no_write():
    fake = FakeClient()
    writer = InfluxWriter(org="o", bucket="b", client=fake)

    await writer.write_poll(_poll(measurements={}))

    assert fake._write_api.calls == []


async def test_build_points_one_point_per_register():
    pts = _build_points(_poll(), "poll", None)
    assert len(pts) == 2
    names = {p.to_line_protocol().split(" ")[1].split("=")[0] for p in pts}
    assert names == {"frequency_hz", "voltage_ab"}


async def test_query_register_history_returns_rows():
    from datetime import datetime

    records = [
        FakeRecord(datetime(2026, 4, 12, 14, 30, 0), 60.01),
        FakeRecord(datetime(2026, 4, 12, 14, 30, 5), 60.02),
    ]
    fake = FakeClient(query_api=FakeQueryApi(tables=[FakeTable(records)]))
    writer = InfluxWriter(org="o", bucket="b", client=fake)

    rows = await writer.query_register_history("dev-123", "frequency_hz", "-1h", "now()")

    assert len(rows) == 2
    assert rows[0]["value"] == 60.01
    assert rows[0]["timestamp"] == "2026-04-12T14:30:00"
    assert 'r["device_id"] == "dev-123"' in fake._query_api.last_query
    assert 'r["_field"] == "frequency_hz"' in fake._query_api.last_query


async def test_constructor_requires_url_token_org_when_no_client():
    with pytest.raises(ValueError, match="url, token, and org"):
        InfluxWriter()


async def test_close_delegates_to_client():
    fake = FakeClient()
    writer = InfluxWriter(org="o", bucket="b", client=fake)
    await writer.close()
    assert fake.closed is True
