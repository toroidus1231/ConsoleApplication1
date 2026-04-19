"""Tests for Module 8 — SNMP Poller.

Uses an injected async reader. pysnmp is never imported in the test env.
"""

import pytest

from src.pollers.snmp import poll_device
from src.types import DeviceInfo


def _device(oids, connection=None, ip="10.0.0.5"):
    return DeviceInfo(
        device_id="dev-1",
        name="fake-switch",
        primary_ip=ip,
        device_type_slug="ex4300",
        config_context={
            "protocol": "snmp",
            "connection": connection or {"port": 161, "community": "public"},
            "oids": oids,
        },
        protocol="snmp",
        site="DC1",
        rack="N1",
        position=2,
    )


async def test_happy_path_reads_all_oids():
    device = _device([
        {"name": "uptime", "oid": "1.3.6.1.2.1.1.3.0"},
        {"name": "temp_c", "oid": "1.3.6.1.4.1.100.1"},
    ])

    calls: list[tuple] = []

    async def reader(host, port, community, oid, timeout, retries):
        calls.append((host, port, community, oid))
        return {"1.3.6.1.2.1.1.3.0": 123456, "1.3.6.1.4.1.100.1": 25.5}[oid]

    result = await poll_device(device, reader=reader)

    assert result.success is True
    assert result.protocol == "snmp"
    assert result.measurements == {"uptime": 123456.0, "temp_c": 25.5}
    assert result.raw_bytes == {"uptime": "123456", "temp_c": "25.5"}
    assert calls[0] == ("10.0.0.5", 161, "public", "1.3.6.1.2.1.1.3.0")


async def test_connection_overrides_used_for_port_and_community():
    device = _device(
        [{"name": "x", "oid": "1.2.3"}],
        connection={"port": 10161, "community": "private", "timeout_seconds": 10, "retries": 1},
    )

    captured = {}

    async def reader(host, port, community, oid, timeout, retries):
        captured.update(port=port, community=community, timeout=timeout, retries=retries)
        return 1

    await poll_device(device, reader=reader)

    assert captured == {"port": 10161, "community": "private", "timeout": 10.0, "retries": 1}


async def test_scale_applied_to_numeric_values():
    device = _device([{"name": "cpu_pct", "oid": "1.2", "scale": 0.1}])

    async def reader(*_):
        return 427  # 42.7%

    result = await poll_device(device, reader=reader)

    assert result.measurements["cpu_pct"] == pytest.approx(42.7)


async def test_reader_exception_captured_per_oid():
    device = _device([
        {"name": "ok", "oid": "1.2"},
        {"name": "bad", "oid": "9.9"},
    ])

    async def reader(host, port, community, oid, timeout, retries):
        if oid == "9.9":
            raise TimeoutError("no response")
        return 1

    result = await poll_device(device, reader=reader)

    assert result.success is False
    assert result.measurements == {"ok": 1.0}
    assert "TimeoutError" in result.errors["bad"]


async def test_non_numeric_snmp_value_reported_as_error():
    device = _device([{"name": "sys_name", "oid": "1.3.6.1.2.1.1.5.0"}])

    async def reader(*_):
        return "router-01.example.com"  # strings are valid SNMP but not numeric

    result = await poll_device(device, reader=reader)

    assert result.success is False
    assert "Non-numeric SNMP value" in result.errors["sys_name"]
    assert result.raw_bytes["sys_name"] == "router-01.example.com"


async def test_empty_oid_list_returns_empty_success():
    device = _device([])

    async def reader(*_):  # pragma: no cover
        raise AssertionError("reader should not run with no OIDs")

    result = await poll_device(device, reader=reader)

    assert result.success is True
    assert result.measurements == {}
    assert result.protocol == "snmp"


async def test_default_community_is_public_when_not_set():
    device = _device([{"name": "x", "oid": "1.2"}], connection={})

    captured_community = []

    async def reader(host, port, community, *_):
        captured_community.append(community)
        return 1

    await poll_device(device, reader=reader)

    assert captured_community == ["public"]
