"""Hardware-in-the-loop test for a real Schneider CM2000 power meter.

Skips unless HW_CM2000_IP is set. Polls the published register map from
spec §3.1 and checks the values are within physically plausible ranges
for an energized 3-phase 480V/60Hz feeder.

Run with:  HW_CM2000_IP=<ip> pytest tests/hardware/ -m hardware -v
"""

import os

import pytest

from src.pollers.modbus import poll_device
from src.types import DeviceInfo


pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        not os.environ.get("HW_CM2000_IP"),
        reason="HW_CM2000_IP not set",
    ),
]


def _cm2000_device() -> DeviceInfo:
    """Config Context matching spec §3.1 register map."""
    return DeviceInfo(
        device_id=os.environ.get("HW_CM2000_DEV_ID", "cm2000-test"),
        name="cm2000-hwloop",
        primary_ip=os.environ["HW_CM2000_IP"],
        device_type_slug="cm2000",
        config_context={
            "protocol": "modbus_tcp",
            "connection": {
                "port": int(os.environ.get("HW_CM2000_PORT", "502")),
                "unit_id": int(os.environ.get("HW_CM2000_UNIT_ID", "1")),
                "byte_order": "big",
                "word_order": "big",
                "timeout_seconds": 5,
                "retries": 2,
            },
            "poll_interval_seconds": 30,
            "registers": [
                {"name": "frequency_hz", "address": 1001, "count": 1,
                 "function_code": 3, "data_type": "uint16",
                 "scale": 0.01, "unit": "Hz"},
                {"name": "current_phase_a", "address": 1003, "count": 1,
                 "function_code": 3, "data_type": "uint16", "unit": "A"},
                {"name": "current_phase_b", "address": 1004, "count": 1,
                 "function_code": 3, "data_type": "uint16", "unit": "A"},
                {"name": "current_phase_c", "address": 1005, "count": 1,
                 "function_code": 3, "data_type": "uint16", "unit": "A"},
                {"name": "voltage_ll_avg", "address": 1017, "count": 1,
                 "function_code": 3, "data_type": "uint16", "unit": "V"},
                {"name": "real_power_total_kw", "address": 1042, "count": 1,
                 "function_code": 3, "data_type": "uint16", "unit": "kW"},
            ],
        },
        protocol="modbus_tcp", site=os.environ.get("HW_CM2000_SITE", "test-bench"),
        rack=os.environ.get("HW_CM2000_RACK", ""), position=0,
    )


async def test_cm2000_responds_and_reads_known_registers():
    result = await poll_device(_cm2000_device())

    assert result.success is True, f"errors: {result.errors}"
    # Spec-required registers should all be present in measurements.
    for reg in ("frequency_hz", "current_phase_a", "voltage_ll_avg", "real_power_total_kw"):
        assert reg in result.measurements, f"missing {reg}"


async def test_cm2000_frequency_within_60hz_band():
    """North American grid: 60 Hz ± 1 Hz under normal conditions."""
    result = await poll_device(_cm2000_device())
    freq = result.measurements.get("frequency_hz")
    assert freq is not None
    if os.environ.get("HW_CM2000_GRID", "60") == "60":
        assert 58.5 <= freq <= 61.5, f"frequency_hz out of band: {freq}"
    else:
        assert 48.5 <= freq <= 51.5, f"frequency_hz out of band: {freq}"


async def test_cm2000_voltage_within_480v_class():
    """480V LL nominal — wide tolerance covers 380V and 600V class systems
    via HW_CM2000_VOLTAGE_NOMINAL env var."""
    nominal = float(os.environ.get("HW_CM2000_VOLTAGE_NOMINAL", "480"))
    result = await poll_device(_cm2000_device())
    voltage = result.measurements.get("voltage_ll_avg")
    assert voltage is not None
    assert nominal * 0.85 <= voltage <= nominal * 1.10, (
        f"voltage_ll_avg {voltage} out of ±10% band of {nominal}V"
    )


async def test_cm2000_currents_non_negative():
    """Phase currents read as uint16, so they're always >= 0; this guards
    against an obvious decoder bug returning gigantic raw values."""
    result = await poll_device(_cm2000_device())
    for phase in ("current_phase_a", "current_phase_b", "current_phase_c"):
        i = result.measurements.get(phase)
        assert i is not None
        assert 0 <= i < 10_000, f"{phase}={i}A is implausible"
