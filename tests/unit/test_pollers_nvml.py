"""Tests for Module 9 — NVML/DCGM Poller.

Injects a fake reader. pynvml is never imported in the test env (and doesn't
need to be — NVML is not portable to test infra).
"""

import pytest

from src.pollers.nvml_poller import SUPPORTED_SOURCES, poll_device
from src.types import DeviceInfo


def _gpu_device(metrics, gpu_index=0, ip="10.0.0.50"):
    return DeviceInfo(
        device_id="gpu-0",
        name="H100-node-1",
        primary_ip=ip,
        device_type_slug="h100",
        config_context={
            "protocol": "nvml",
            "gpu_index": gpu_index,
            "metrics": metrics,
        },
        protocol="nvml",
        site="DC1",
        rack="B7",
        position=15,
    )


async def test_happy_path_reads_multiple_metrics():
    device = _gpu_device([
        {"name": "temp_c", "source": "temperature"},
        {"name": "power_w", "source": "power_usage"},
        {"name": "mem_used_bytes", "source": "memory_used"},
    ])

    calls: list[tuple] = []

    def reader(gpu_index, source):
        calls.append((gpu_index, source))
        return {
            "temperature": 65.0,
            "power_usage": 350.0,
            "memory_used": 42_000_000_000.0,
        }[source]

    result = await poll_device(device, reader=reader)

    assert result.success is True
    assert result.protocol == "nvml"
    assert result.source_ip == "10.0.0.50"
    assert result.measurements == {
        "temp_c": 65.0,
        "power_w": 350.0,
        "mem_used_bytes": 42_000_000_000.0,
    }
    assert all(gi == 0 for gi, _ in calls)


async def test_gpu_index_threaded_through():
    device = _gpu_device(
        [{"name": "t", "source": "temperature"}],
        gpu_index=3,
    )

    captured_index = []

    def reader(gpu_index, source):
        captured_index.append(gpu_index)
        return 55.0

    await poll_device(device, reader=reader)

    assert captured_index == [3]


async def test_unsupported_source_reported_as_error():
    device = _gpu_device([
        {"name": "good", "source": "temperature"},
        {"name": "bad", "source": "quantum_flux_capacitor"},
    ])

    def reader(gpu_index, source):
        return 1.0

    result = await poll_device(device, reader=reader)

    assert result.success is False
    assert result.measurements == {"good": 1.0}
    assert "Unsupported NVML source" in result.errors["bad"]


async def test_reader_exception_captured_per_metric():
    device = _gpu_device([
        {"name": "ok", "source": "temperature"},
        {"name": "err", "source": "fan_speed"},
    ])

    def reader(gpu_index, source):
        if source == "fan_speed":
            raise RuntimeError("NVMLError_NotSupported")
        return 65.0

    result = await poll_device(device, reader=reader)

    assert result.success is False
    assert result.measurements == {"ok": 65.0}
    assert "RuntimeError" in result.errors["err"]


async def test_scale_applied_to_raw_value():
    device = _gpu_device([
        {"name": "power_kw", "source": "power_usage", "scale": 0.001},
    ])

    def reader(*_):
        return 350.0  # W

    result = await poll_device(device, reader=reader)

    assert result.measurements["power_kw"] == pytest.approx(0.35)


async def test_default_gpu_index_zero():
    device_ctx_without_index = DeviceInfo(
        device_id="gpu-0",
        name="node",
        primary_ip="10.0.0.50",
        device_type_slug="h100",
        config_context={
            "protocol": "nvml",
            "metrics": [{"name": "t", "source": "temperature"}],
        },
        protocol="nvml",
        site="DC1",
        rack="",
        position=0,
    )

    captured = []

    def reader(gpu_index, source):
        captured.append(gpu_index)
        return 50.0

    await poll_device(device_ctx_without_index, reader=reader)

    assert captured == [0]


async def test_no_primary_ip_defaults_to_localhost():
    device = _gpu_device(
        [{"name": "t", "source": "temperature"}],
        ip="",
    )

    def reader(*_):
        return 65.0

    result = await poll_device(device, reader=reader)

    assert result.source_ip == "localhost"


async def test_all_documented_sources_are_in_supported_set():
    """Guard against typos in the SUPPORTED_SOURCES set vs the docstring."""
    expected = {
        "temperature",
        "power_usage",
        "gpu_utilization",
        "memory_utilization",
        "memory_used",
        "memory_free",
        "memory_total",
        "fan_speed",
        "ecc_uncorrected_total",
    }
    assert SUPPORTED_SOURCES == expected
