"""Tests for Module 2 — NetBox Device Reader."""

import httpx

from src.netbox_reader import get_devices_by_protocol


def _netbox_device(
    dev_id: int,
    name: str,
    slug: str,
    protocol: str,
    ip: str = "10.0.0.5/24",
    site: str = "DC1",
    rack: str = "A3",
    position: int = 10,
    extra_context: dict | None = None,
) -> dict:
    ctx = {"protocol": protocol}
    if extra_context:
        ctx.update(extra_context)
    return {
        "id": dev_id,
        "name": name,
        "primary_ip4": {"address": ip} if ip else None,
        "device_type": {"slug": slug},
        "config_context": ctx,
        "site": {"name": site},
        "rack": {"name": rack},
        "position": position,
    }


async def test_filters_by_protocol():
    modbus_dev = _netbox_device(1, "cm2000-a3", "cm2000", "modbus_tcp")
    snmp_dev = _netbox_device(2, "switch-01", "ex4300", "snmp")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Token tok"
        assert request.url.params.get("status") == "active"
        return httpx.Response(200, json={"results": [modbus_dev, snmp_dev], "next": None})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        got = await get_devices_by_protocol("http://nb", "tok", "modbus_tcp", client=c)

    assert len(got) == 1
    assert got[0].device_id == "1"
    assert got[0].name == "cm2000-a3"
    assert got[0].primary_ip == "10.0.0.5"
    assert got[0].device_type_slug == "cm2000"
    assert got[0].protocol == "modbus_tcp"
    assert got[0].site == "DC1"
    assert got[0].rack == "A3"
    assert got[0].position == 10


async def test_handles_pagination():
    page1 = {
        "results": [_netbox_device(1, "a", "cm2000", "modbus_tcp")],
        "next": "http://nb/api/dcim/devices/?limit=1000&offset=1000",
    }
    page2 = {
        "results": [_netbox_device(2, "b", "cm2000", "modbus_tcp")],
        "next": None,
    }
    pages = iter([page1, page2])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(pages))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        got = await get_devices_by_protocol("http://nb", "tok", "modbus_tcp", client=c)

    assert [d.device_id for d in got] == ["1", "2"]


async def test_falls_back_to_connection_host_when_no_primary_ip():
    dev = _netbox_device(
        1, "no-ip", "cm2000", "modbus_tcp", ip="", extra_context={"connection": {"host": "10.99.0.1"}}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [dev], "next": None})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        got = await get_devices_by_protocol("http://nb", "tok", "modbus_tcp", client=c)

    assert got[0].primary_ip == "10.99.0.1"


async def test_skips_devices_with_no_or_wrong_protocol():
    devs = [
        _netbox_device(1, "a", "cm2000", "modbus_tcp"),
        {"id": 2, "name": "no-context", "config_context": None, "device_type": {"slug": "x"}},
        _netbox_device(3, "c", "cm2000", "snmp"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": devs, "next": None})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        got = await get_devices_by_protocol("http://nb", "tok", "modbus_tcp", client=c)

    assert [d.device_id for d in got] == ["1"]


async def test_empty_result_returns_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [], "next": None})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        got = await get_devices_by_protocol("http://nb", "tok", "modbus_tcp", client=c)

    assert got == []
