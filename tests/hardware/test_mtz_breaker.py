"""Hardware-in-the-loop test for a Schneider Masterpact MTZ via IFE.

Read-only — does NOT issue trip commands. Active trip testing is part of
the commissioning run with manual_confirmation gates (Module 10), not
the test harness.

Skips unless HW_MTZ_IP is set.
"""

import os

import pytest

from src.pollers.modbus import poll_device
from src.types import DeviceInfo


pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        not os.environ.get("HW_MTZ_IP"),
        reason="HW_MTZ_IP not set",
    ),
]


def _mtz_device() -> DeviceInfo:
    return DeviceInfo(
        device_id="mtz-hwloop", name="mtz-hwloop",
        primary_ip=os.environ["HW_MTZ_IP"],
        device_type_slug="masterpact-mtz",
        config_context={
            "protocol": "modbus_tcp",
            "connection": {
                "port": int(os.environ.get("HW_MTZ_PORT", "502")),
                "unit_id": int(os.environ.get("HW_MTZ_UNIT_ID", "1")),
                "byte_order": "big", "word_order": "big",
                "timeout_seconds": 5, "retries": 2,
            },
            "poll_interval_seconds": 30,
            "registers": [
                {"name": "breaker_position", "address": 32000, "count": 1,
                 "function_code": 3, "data_type": "uint16", "unit": "enum"},
                {"name": "cradle_position", "address": 32001, "count": 1,
                 "function_code": 3, "data_type": "uint16", "unit": "enum"},
                {"name": "current_ia", "address": 32010, "count": 2,
                 "function_code": 3, "data_type": "float32", "unit": "A"},
                {"name": "active_power_total", "address": 32030, "count": 2,
                 "function_code": 3, "data_type": "float32", "unit": "kW"},
                {"name": "contact_wear_percent", "address": 32070, "count": 1,
                 "function_code": 3, "data_type": "uint16", "unit": "%"},
            ],
        },
        protocol="modbus_tcp", site="test-bench", rack="", position=0,
    )


async def test_mtz_responds():
    result = await poll_device(_mtz_device())
    assert result.success is True, f"errors: {result.errors}"


async def test_mtz_position_is_known_enum():
    """Per spec §3.2: 0=open, 1=closed, 2=tripped."""
    result = await poll_device(_mtz_device())
    pos = result.measurements.get("breaker_position")
    assert pos in {0, 1, 2}, f"unexpected breaker_position {pos}"


async def test_mtz_cradle_position_is_known_enum():
    """0=connected, 1=test, 2=withdrawn."""
    result = await poll_device(_mtz_device())
    pos = result.measurements.get("cradle_position")
    assert pos in {0, 1, 2}, f"unexpected cradle_position {pos}"


async def test_mtz_contact_wear_in_range():
    result = await poll_device(_mtz_device())
    wear = result.measurements.get("contact_wear_percent")
    assert wear is not None
    assert 0 <= wear <= 100, f"contact_wear_percent={wear}% out of range"
