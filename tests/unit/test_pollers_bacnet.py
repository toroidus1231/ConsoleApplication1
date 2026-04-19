"""Tests for Module 7 — BACnet/IP Poller.

Injects a fake reader so BAC0 never has to load. BAC0 is a heavy sync library
with network side effects; unit tests should not need it.
"""

import pytest

from src.pollers.bacnet import poll_device
from src.types import DeviceInfo


def _device(objects, connection=None, ip="10.0.0.5"):
    return DeviceInfo(
        device_id="dev-1",
        name="fake-bms",
        primary_ip=ip,
        device_type_slug="trane-xm",
        config_context={
            "protocol": "bacnet_ip",
            "connection": connection or {},
            "objects": objects,
        },
        protocol="bacnet_ip",
        site="DC1",
        rack="",
        position=0,
    )


async def test_happy_path_reads_all_objects():
    device = _device([
        {"name": "zone_temp", "type": "analogInput", "instance": 1, "property": "presentValue"},
        {"name": "zone_setpoint", "type": "analogValue", "instance": 2},
    ])

    calls: list[tuple] = []

    def reader(host, obj_type, instance, prop):
        calls.append((host, obj_type, instance, prop))
        return {("analogInput", 1): 21.5, ("analogValue", 2): 22.0}[(obj_type, instance)]

    result = await poll_device(device, reader=reader)

    assert result.success is True
    assert result.protocol == "bacnet_ip"
    assert result.source_ip == "10.0.0.5"
    assert result.measurements == {"zone_temp": 21.5, "zone_setpoint": 22.0}
    assert result.raw_bytes == {"zone_temp": "21.5", "zone_setpoint": "22.0"}
    # Default property is presentValue.
    assert calls[0] == ("10.0.0.5", "analogInput", 1, "presentValue")
    assert calls[1] == ("10.0.0.5", "analogValue", 2, "presentValue")


async def test_scale_applied_to_numeric_values():
    device = _device([
        {"name": "flow_cfm", "type": "analogInput", "instance": 1, "scale": 0.01},
    ])

    def reader(*_):
        return 7500  # raw counts

    result = await poll_device(device, reader=reader)

    assert result.measurements["flow_cfm"] == pytest.approx(75.0)


async def test_reader_exception_captured_as_per_object_error():
    device = _device([
        {"name": "ok_obj", "type": "analogInput", "instance": 1},
        {"name": "bad_obj", "type": "analogInput", "instance": 2},
    ])

    def reader(host, obj_type, instance, prop):
        if instance == 2:
            raise TimeoutError("BACnet read timeout")
        return 42.0

    result = await poll_device(device, reader=reader)

    assert result.success is False
    assert result.measurements == {"ok_obj": 42.0}
    assert "TimeoutError" in result.errors["bad_obj"]


async def test_non_numeric_value_reported_as_error():
    device = _device([
        {"name": "status", "type": "multiStateValue", "instance": 1},
    ])

    def reader(*_):
        return "running"  # BACnet enumerated state strings don't coerce to float

    result = await poll_device(device, reader=reader)

    assert result.success is False
    assert "Non-numeric value 'running'" in result.errors["status"]
    # Raw string is still preserved for the record.
    assert result.raw_bytes["status"] == "running"


async def test_no_objects_returns_empty_success():
    device = _device([])

    async def _unused(*_):  # pragma: no cover — assert never called
        raise AssertionError("reader should not be called with no objects")

    result = await poll_device(device, reader=lambda *a: _unused(*a))

    assert result.success is True
    assert result.measurements == {}
    assert result.protocol == "bacnet_ip"


async def test_falls_back_to_connection_host_when_no_primary_ip():
    device = _device(
        [{"name": "t", "type": "analogInput", "instance": 1}],
        connection={"host": "10.99.0.1"},
        ip="",
    )

    captured_host = []

    def reader(host, *_):
        captured_host.append(host)
        return 1.0

    result = await poll_device(device, reader=reader)

    assert captured_host == ["10.99.0.1"]
    assert result.source_ip == "10.99.0.1"
