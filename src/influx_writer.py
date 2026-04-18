"""Module 4: InfluxDB Writer.

Writes PollResult batches to InfluxDB as line-protocol points. Measurement name
is "poll" for regular polling and "test" when a test_id is provided (contracts
spec §3.2).

Each register becomes a field on one Point per poll. Tags: device_id, protocol,
source_ip, and (for tests) test_id.
"""

from __future__ import annotations

from typing import Protocol

from influxdb_client import Point, WritePrecision
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

from .types import PollResult


class WriteApiLike(Protocol):
    async def write(self, bucket: str, org: str, record) -> None: ...


class QueryApiLike(Protocol):
    async def query(self, query: str, org: str): ...


class InfluxWriter:
    """Thin async wrapper around InfluxDBClientAsync.

    Accepts either a URL+token (constructs its own client) or an already-built
    client (for tests).
    """

    def __init__(
        self,
        url: str = "",
        token: str = "",
        org: str = "",
        bucket: str = "commissioning",
        *,
        client: InfluxDBClientAsync | None = None,
    ):
        if client is None:
            if not (url and token and org):
                raise ValueError("url, token, and org are required when client is not provided")
            client = InfluxDBClientAsync(url=url, token=token, org=org)
        self._client = client
        self.org = org
        self.bucket = bucket

    async def write_poll(self, result: PollResult, test_id: str | None = None) -> None:
        """Write one PollResult as a batch of Points. Single InfluxDB write call."""
        measurement = "test" if test_id else "poll"
        points = _build_points(result, measurement, test_id)
        if not points:
            return

        write_api = self._client.write_api()
        await write_api.write(bucket=self.bucket, org=self.org, record=points)

    async def query_register_history(
        self,
        device_id: str,
        register: str,
        start: str,
        stop: str,
    ) -> list[dict]:
        """Flux query for a single register's values between start and stop.

        Args:
            start, stop: RFC-3339 timestamps or Flux duration literals like -1h.
        """
        query = f'''
        from(bucket: "{self.bucket}")
          |> range(start: {start}, stop: {stop})
          |> filter(fn: (r) => r["device_id"] == "{device_id}")
          |> filter(fn: (r) => r["_field"] == "{register}")
          |> sort(columns: ["_time"])
        '''
        query_api = self._client.query_api()
        tables = await query_api.query(query, org=self.org)
        rows: list[dict] = []
        for table in tables:
            for r in table.records:
                rows.append({"timestamp": r.get_time().isoformat(), "value": r.get_value()})
        return rows

    async def close(self) -> None:
        await self._client.close()


def _build_points(result: PollResult, measurement: str, test_id: str | None) -> list[Point]:
    """Build one Point per register, tagged with device_id/protocol/source_ip."""
    points: list[Point] = []
    for name, value in result.measurements.items():
        pt = (
            Point(measurement)
            .tag("device_id", result.device_id)
            .tag("protocol", result.protocol)
            .tag("source_ip", result.source_ip)
            .field(name, float(value))
            .time(result.timestamp_ns, WritePrecision.NS)
        )
        if test_id:
            pt = pt.tag("test_id", test_id)
        points.append(pt)
    return points
