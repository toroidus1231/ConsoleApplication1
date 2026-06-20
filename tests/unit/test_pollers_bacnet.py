"""Tests for Module 7 — BACnet/IP Poller.

Uses an in-memory fake BACnet client whose synchronous ``read(request)`` matches
BAC0's surface: it takes a single request string ("IP objectType instance
property") and returns the property value (or raises). This avoids needing the
heavy native BAC0 stack — which is intentionally NOT imported at module top
level — in unit tests.
"""

import math

import pytest

from src.pollers.bacnet import poll_device
from src.types import DeviceInfo


class FakeBacnetClient:
    """In-memory BAC0-compatible fake.

    ``values`` is a dict {request_string: value}. A read of a request maps to its
    canned value. Missing requests raise KeyError (a stand-in for a BACnet read
    failure). If ``read_exc`` is set, every read raises it.
    """

    def __init__(self, values=None, read_exc=None):
        self.values = values or {}
        self.read_exc = read_exc
        self.requests: list[str] = []

    def read(self, request):
        self.requests.append(request)
        if self.read_exc:
            raise self.read_exc
        if request not in self.values:
            raise KeyError(f"unknown object: {request}")
        return self.values[request]


def _device(objects, connection=None):
    return DeviceInfo(
        device_id="dev-1",
        name="fake",
        primary_ip="10.0.0.5",
        device_type_slug="vav-controller",
        config_context={
            "protocol": "bacnet_ip",
            "connection": connection or {"host": "10.0.0.5"},
            "objects": objects,
        },
        protocol="bacnet_ip",
        site="DC1",
        rack="A3",
        position=10,
    )


async def test_decodes_multiple_objects():
    device = _device([
        {"name": "zone_temp", "type": "analogInput", "instance": 1,
         "property": "presentValue", "scale": 1.0},
        {"name": "setpoint", "type": "analogValue", "instance": 2,
         "property": "presentValue", "scale": 1.0},
    ])
    fake = FakeBacnetClient(values={
        "10.0.0.5 analogInput 1 presentValue": 21.5,
        "10.0.0.5 analogValue 2 presentValue": 22.0,
    })

    result = await poll_device(device, client=fake)

    assert result.success is True
    assert result.measurements == {"zone_temp": pytest.approx(21.5), "setpoint": pytest.approx(22.0)}
    assert result.raw_bytes == {"zone_temp": "21.5", "setpoint": "22.0"}
    assert result.protocol == "bacnet_ip"
    assert result.source_ip == "10.0.0.5"


async def test_exact_request_string_passed_to_read():
    device = _device([
        {"name": "zone_temp", "type": "analogInput", "instance": 1,
         "property": "presentValue", "scale": 1.0},
    ])
    fake = FakeBacnetClient(values={"10.0.0.5 analogInput 1 presentValue": 21.5})

    await poll_device(device, client=fake)

    assert fake.requests == ["10.0.0.5 analogInput 1 presentValue"]


async def test_default_property_is_present_value():
    # No "property" key -> defaults to presentValue in the request string.
    device = _device([
        {"name": "zone_temp", "type": "analogInput", "instance": 7, "scale": 1.0},
    ])
    fake = FakeBacnetClient(values={"10.0.0.5 analogInput 7 presentValue": 19.0})

    result = await poll_device(device, client=fake)

    assert fake.requests == ["10.0.0.5 analogInput 7 presentValue"]
    assert result.measurements["zone_temp"] == pytest.approx(19.0)


async def test_scale_is_applied():
    device = _device([
        {"name": "pressure_x10", "type": "analogInput", "instance": 1,
         "property": "presentValue", "scale": 0.1},
    ])
    fake = FakeBacnetClient(values={"10.0.0.5 analogInput 1 presentValue": 480.0})

    result = await poll_device(device, client=fake)

    assert result.measurements["pressure_x10"] == pytest.approx(48.0)


async def test_one_object_raises_others_succeed():
    device = _device([
        {"name": "good", "type": "analogInput", "instance": 1,
         "property": "presentValue", "scale": 1.0},
        {"name": "bad", "type": "analogInput", "instance": 2,
         "property": "presentValue", "scale": 1.0},
    ])
    # Only the "good" request has a canned value; "bad" raises KeyError.
    fake = FakeBacnetClient(values={"10.0.0.5 analogInput 1 presentValue": 21.5})

    result = await poll_device(device, client=fake)

    assert result.success is False
    assert result.measurements == {"good": pytest.approx(21.5)}
    assert "bad" in result.errors
    assert "KeyError" in result.errors["bad"]


async def test_nan_value_reports_error():
    device = _device([
        {"name": "zone_temp", "type": "analogInput", "instance": 1,
         "property": "presentValue", "scale": 1.0},
    ])
    fake = FakeBacnetClient(values={"10.0.0.5 analogInput 1 presentValue": float("nan")})

    result = await poll_device(device, client=fake)

    assert result.success is False
    assert "zone_temp" in result.errors
    assert "NaN/Inf" in result.errors["zone_temp"]
    assert "zone_temp" not in result.measurements


async def test_inf_value_reports_error():
    device = _device([
        {"name": "zone_temp", "type": "analogInput", "instance": 1,
         "property": "presentValue", "scale": 1.0},
    ])
    fake = FakeBacnetClient(values={"10.0.0.5 analogInput 1 presentValue": float("inf")})

    result = await poll_device(device, client=fake)

    assert result.success is False
    assert "NaN/Inf" in result.errors["zone_temp"]


async def test_empty_objects_list_succeeds_empty():
    device = _device([])
    fake = FakeBacnetClient()

    result = await poll_device(device, client=fake)

    assert result.success is True
    assert result.measurements == {}
    assert result.raw_bytes == {}
    assert result.errors == {}
    assert result.protocol == "bacnet_ip"
    assert fake.requests == []


async def test_source_ip_falls_back_to_connection_host():
    # primary_ip empty -> source_ip and request host come from connection.host.
    device = DeviceInfo(
        device_id="dev-2",
        name="fake",
        primary_ip="",
        device_type_slug="vav-controller",
        config_context={
            "protocol": "bacnet_ip",
            "connection": {"host": "10.0.0.9"},
            "objects": [
                {"name": "zone_temp", "type": "analogInput", "instance": 1,
                 "property": "presentValue", "scale": 1.0},
            ],
        },
        protocol="bacnet_ip",
        site="DC1",
        rack="A3",
        position=10,
    )
    fake = FakeBacnetClient(values={"10.0.0.9 analogInput 1 presentValue": 20.0})

    result = await poll_device(device, client=fake)

    assert result.source_ip == "10.0.0.9"
    assert fake.requests == ["10.0.0.9 analogInput 1 presentValue"]
    assert result.measurements["zone_temp"] == pytest.approx(20.0)


async def test_read_exception_captured_as_per_object_error():
    device = _device([
        {"name": "zone_temp", "type": "analogInput", "instance": 1,
         "property": "presentValue", "scale": 1.0},
    ])
    fake = FakeBacnetClient(read_exc=TimeoutError("read timeout"))

    result = await poll_device(device, client=fake)

    assert result.success is False
    assert "TimeoutError" in result.errors["zone_temp"]
