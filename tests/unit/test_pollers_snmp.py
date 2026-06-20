"""Tests for Module 8 — SNMP Poller.

Uses an in-memory fake async getter that matches the injectable SNMP seam
(host, port, community, oid, timeout_s) -> SnmpResult. This avoids needing
pysnmp installed or a live agent in unit tests; the fake returns canned
SnmpResult values per OID.
"""

import pytest

from src.pollers.snmp import SnmpResult, poll_device
from src.types import DeviceInfo


class FakeGetter:
    """In-memory SNMP getter.

    `oids` maps an OID string to a SnmpResult returned for that OID. A missing
    OID yields a SnmpResult with an error. When `raise_exc` is set, the getter
    raises it on every call (simulating a transport blowup). Every call is
    recorded in `calls` as (host, port, community, oid, timeout_s).
    """

    def __init__(self, oids=None, raise_exc=None):
        self.oids = oids or {}
        self.raise_exc = raise_exc
        self.calls: list[tuple] = []

    async def __call__(self, host, port, community, oid, timeout_s):
        self.calls.append((host, port, community, oid, timeout_s))
        if self.raise_exc:
            raise self.raise_exc
        if oid not in self.oids:
            return SnmpResult(None, "", f"No such OID: {oid}")
        return self.oids[oid]


def _device(oids, connection=None):
    return DeviceInfo(
        device_id="dev-1",
        name="fake",
        primary_ip="10.0.0.5",
        device_type_slug="switch1",
        config_context={
            "protocol": "snmp",
            "connection": connection
            or {"community": "public", "port": 161, "timeout_seconds": 5},
            "oids": oids,
        },
        protocol="snmp",
        site="DC1",
        rack="A3",
        position=10,
    )


async def test_happy_path_multiple_oids():
    device = _device([
        {"name": "ifInOctets", "oid": "1.3.6.1.2.1.2.2.1.10.1"},
        {"name": "ifOutOctets", "oid": "1.3.6.1.2.1.2.2.1.16.1"},
    ])
    fake = FakeGetter(oids={
        "1.3.6.1.2.1.2.2.1.10.1": SnmpResult(12345, "12345", None),
        "1.3.6.1.2.1.2.2.1.16.1": SnmpResult(678, "678", None),
    })

    result = await poll_device(device, getter=fake)

    assert result.success is True
    assert result.measurements == {"ifInOctets": 12345.0, "ifOutOctets": 678.0}
    assert result.raw_bytes == {"ifInOctets": "12345", "ifOutOctets": "678"}
    assert result.protocol == "snmp"
    assert result.source_ip == "10.0.0.5"
    assert result.errors == {}


async def test_scale_is_applied():
    device = _device([
        {"name": "tempC", "oid": "1.3.6.1.4.1.9.1", "scale": 0.1},
    ])
    fake = FakeGetter(oids={"1.3.6.1.4.1.9.1": SnmpResult(235, "235", None)})

    result = await poll_device(device, getter=fake)

    assert result.success is True
    assert result.measurements["tempC"] == pytest.approx(23.5)


async def test_one_oid_errors_others_succeed():
    device = _device([
        {"name": "good", "oid": "1.1"},
        {"name": "bad", "oid": "2.2"},
    ])
    fake = FakeGetter(oids={
        "1.1": SnmpResult(42, "42", None),
        "2.2": SnmpResult(None, "", "SNMP error: noSuchName"),
    })

    result = await poll_device(device, getter=fake)

    assert result.success is False
    assert result.measurements == {"good": 42.0}
    assert "SNMP error: noSuchName" in result.errors["bad"]
    assert "bad" not in result.raw_bytes


async def test_getter_exception_captured_per_oid():
    device = _device([
        {"name": "x", "oid": "1.2.3"},
    ])
    fake = FakeGetter(raise_exc=TimeoutError("snmp timeout"))

    result = await poll_device(device, getter=fake)

    assert result.success is False
    assert "TimeoutError" in result.errors["x"]
    assert result.measurements == {}


async def test_empty_oids_list_succeeds():
    device = _device([])
    fake = FakeGetter()

    result = await poll_device(device, getter=fake)

    assert result.success is True
    assert result.measurements == {}
    assert result.errors == {}
    assert fake.calls == []


async def test_nan_value_reports_error():
    device = _device([
        {"name": "v", "oid": "1.2.3"},
    ])
    fake = FakeGetter(oids={"1.2.3": SnmpResult(float("nan"), "nan", None)})

    result = await poll_device(device, getter=fake)

    assert result.success is False
    assert "NaN/Inf" in result.errors["v"]
    assert "v" not in result.measurements


async def test_none_value_reports_error_without_crashing():
    device = _device([
        {"name": "v", "oid": "1.2.3"},
    ])
    fake = FakeGetter(oids={"1.2.3": SnmpResult(None, "", None)})

    result = await poll_device(device, getter=fake)

    assert result.success is False
    assert "v" in result.errors
    assert "v" not in result.measurements


async def test_getter_receives_correct_args():
    device = _device(
        [{"name": "ifInOctets", "oid": "1.3.6.1.2.1.2.2.1.10.1"}],
        connection={"community": "private", "port": 1610, "timeout_seconds": 2},
    )
    fake = FakeGetter(oids={
        "1.3.6.1.2.1.2.2.1.10.1": SnmpResult(1, "1", None),
    })

    await poll_device(device, getter=fake)

    assert fake.calls == [("10.0.0.5", 1610, "private", "1.3.6.1.2.1.2.2.1.10.1", 2.0)]
