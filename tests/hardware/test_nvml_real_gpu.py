"""Hardware-in-the-loop test for NVML against a real NVIDIA GPU.

Skips when HW_NVML is not set or pynvml can't initialize. Use this on
GPU nodes during commissioning to confirm we can actually read the
metrics the spec lists in §3.7.
"""

import os

import pytest

from src.pollers.nvml_poller import poll_device
from src.types import DeviceInfo


pytestmark = [pytest.mark.hardware]


def _gpu_device(gpu_index: int = 0) -> DeviceInfo:
    return DeviceInfo(
        device_id=f"gpu-{gpu_index}", name=f"gpu-{gpu_index}",
        primary_ip="localhost", device_type_slug="nvidia-gpu",
        config_context={
            "protocol": "nvml",
            "gpu_index": gpu_index,
            "metrics": [
                {"name": "temp_c", "source": "temperature"},
                {"name": "power_w", "source": "power_usage"},
                {"name": "mem_used", "source": "memory_used"},
                {"name": "mem_total", "source": "memory_total"},
                {"name": "util_pct", "source": "gpu_utilization"},
            ],
        },
        protocol="nvml", site="gpu-node", rack="", position=0,
    )


def _nvml_available() -> bool:
    if not os.environ.get("HW_NVML"):
        return False
    try:
        import pynvml  # noqa: PLC0415
        pynvml.nvmlInit()
        try:
            count = pynvml.nvmlDeviceGetCount()
            return count > 0
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return False


@pytest.mark.skipif(not _nvml_available(), reason="NVML or HW_NVML not available")
async def test_gpu_temperature_in_plausible_range():
    result = await poll_device(_gpu_device())
    temp = result.measurements.get("temp_c")
    assert temp is not None, f"errors: {result.errors}"
    # 0°C is below ambient; 100°C is hard throttle threshold for most GPUs.
    assert 5 <= temp <= 100, f"temperature {temp}°C is implausible"


@pytest.mark.skipif(not _nvml_available(), reason="NVML or HW_NVML not available")
async def test_gpu_memory_used_is_at_most_total():
    result = await poll_device(_gpu_device())
    used = result.measurements.get("mem_used")
    total = result.measurements.get("mem_total")
    assert used is not None and total is not None
    assert 0 <= used <= total


@pytest.mark.skipif(not _nvml_available(), reason="NVML or HW_NVML not available")
async def test_gpu_power_within_card_envelope():
    """Most cards: 50–700W. Wider range to be safe for unusual SKUs."""
    result = await poll_device(_gpu_device())
    power = result.measurements.get("power_w")
    assert power is not None
    assert 0 <= power <= 1500, f"power_w={power}W is implausible"
