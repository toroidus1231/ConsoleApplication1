"""Tests for Module 9 — NVML/DCGM Poller.

Uses an in-memory fake NVML client that matches the handle-based ``NvmlLike``
surface the poller needs (get_handle + per-metric accessors). This avoids
needing the pynvml native library or an NVIDIA driver in unit tests — pynvml is
never imported because tests always inject a fake client.
"""

import math

import pytest

from src.pollers.nvml_poller import poll_device
from src.types import DeviceInfo


class FakeHandle:
    """Opaque GPU handle carrying canned per-GPU metric values."""

    def __init__(self, index, values):
        self.index = index
        self.values = values


class FakeNvml:
    """In-memory NvmlLike fake.

    ``gpus`` maps a GPU index -> dict of canned values keyed by accessor name,
    e.g. {"temperature_gpu": 65.0, "power_mw": 250000, "memory": (a, b),
    "ecc": {True: 3, False: 0}, "nvlink_state": {0: True, 1: False}, ...}.

    ``raise_on`` is an optional set of accessor names; calling one raises to
    exercise per-metric error isolation. Missing GPU indexes raise on
    ``get_handle``.
    """

    def __init__(self, gpus=None, raise_on=None):
        self.gpus = gpus or {}
        self.raise_on = set(raise_on or ())
        self.handles_requested: list[int] = []
        self.shutdown_called = False

    def _maybe_raise(self, name):
        if name in self.raise_on:
            raise RuntimeError(f"NVML failure reading {name}")

    def get_handle(self, index):
        self.handles_requested.append(index)
        if index not in self.gpus:
            raise ValueError(f"No such GPU index: {index}")
        return FakeHandle(index, self.gpus[index])

    def temperature_gpu(self, handle):
        self._maybe_raise("temperature_gpu")
        return handle.values["temperature_gpu"]

    def power_usage_milliwatts(self, handle):
        self._maybe_raise("power_usage_milliwatts")
        return handle.values["power_mw"]

    def memory_info(self, handle):
        self._maybe_raise("memory_info")
        return handle.values["memory"]

    def ecc_errors(self, handle, uncorrected):
        self._maybe_raise("ecc_errors")
        return handle.values["ecc"][uncorrected]

    def nvlink_state(self, handle, link):
        self._maybe_raise("nvlink_state")
        return handle.values["nvlink_state"][link]

    def nvlink_error_count(self, handle, link):
        self._maybe_raise("nvlink_error_count")
        return handle.values["nvlink_errors"][link]

    def pcie_link_gen(self, handle):
        self._maybe_raise("pcie_link_gen")
        return handle.values["pcie_gen"]

    def pcie_link_width(self, handle):
        self._maybe_raise("pcie_link_width")
        return handle.values["pcie_width"]

    def fan_speed_percent(self, handle):
        self._maybe_raise("fan_speed_percent")
        return handle.values["fan"]

    def utilization_gpu_percent(self, handle):
        self._maybe_raise("utilization_gpu_percent")
        return handle.values["util"]


def _device(metrics, gpu_index=0, primary_ip="10.0.0.5"):
    return DeviceInfo(
        device_id="dev-1",
        name="fake-gpu",
        primary_ip=primary_ip,
        device_type_slug="dgx-h100",
        config_context={
            "protocol": "nvml",
            "gpu_index": gpu_index,
            "metrics": metrics,
        },
        protocol="nvml",
        site="DC1",
        rack="A3",
        position=10,
    )


def _gpu0(**overrides):
    """A fully-populated canned GPU-0 value set; overridable per test."""
    values = {
        "temperature_gpu": 65.0,
        "power_mw": 250000,  # 250.0 W
        "memory": (8_000_000_000, 80_000_000_000),
        "ecc": {True: 3, False: 0},
        "nvlink_state": {0: True, 1: False},
        "nvlink_errors": {0: 7},
        "pcie_gen": 5,
        "pcie_width": 16,
        "fan": 42.0,
        "util": 88.0,
    }
    values.update(overrides)
    return values


async def test_happy_path_reads_several_metric_types():
    device = _device([
        {"name": "temp_gpu", "metric": "temperature_gpu", "scale": 1.0},
        {"name": "power_w", "metric": "power_usage_w"},
        {"name": "ecc_uncorrected", "metric": "ecc_uncorrected_total"},
        {"name": "mem_used", "metric": "memory_used_bytes"},
        {"name": "nvlink0", "metric": "nvlink_state", "args": {"link": 0}},
    ])
    fake = FakeNvml(gpus={0: _gpu0()})

    result = await poll_device(device, client=fake)

    assert result.success is True
    assert result.protocol == "nvml"
    assert result.source_ip == "10.0.0.5"
    assert result.errors == {}
    assert result.measurements["temp_gpu"] == 65.0
    assert result.measurements["power_w"] == pytest.approx(250.0)
    assert result.measurements["ecc_uncorrected"] == 3
    assert result.measurements["mem_used"] == 8_000_000_000
    # nvlink up -> 1.0
    assert result.measurements["nvlink0"] == 1.0
    # The poller fetched the handle for the configured GPU index.
    assert fake.handles_requested == [0]
    # Caller owns the injected client — shutdown is not called on it.
    assert fake.shutdown_called is False


async def test_power_usage_w_divides_milliwatts_by_1000():
    device = _device([
        {"name": "power_w", "metric": "power_usage_w"},
    ])
    fake = FakeNvml(gpus={0: _gpu0(power_mw=123456)})  # 123.456 W

    result = await poll_device(device, client=fake)

    assert result.success is True
    assert result.measurements["power_w"] == pytest.approx(123.456)
    # raw_bytes records the pre-scale (pre-conversion result) numeric value.
    assert result.raw_bytes["power_w"] == "123.456"


async def test_memory_info_tuple_used_and_total():
    device = _device([
        {"name": "mem_used", "metric": "memory_used_bytes"},
        {"name": "mem_total", "metric": "memory_total_bytes"},
    ])
    fake = FakeNvml(gpus={0: _gpu0(memory=(16_000_000_000, 80_000_000_000))})

    result = await poll_device(device, client=fake)

    assert result.success is True
    assert result.measurements["mem_used"] == 16_000_000_000
    assert result.measurements["mem_total"] == 80_000_000_000


async def test_nvlink_state_down_maps_to_zero():
    device = _device([
        {"name": "nvlink1", "metric": "nvlink_state", "args": {"link": 1}},
    ])
    fake = FakeNvml(gpus={0: _gpu0()})  # link 1 is down

    result = await poll_device(device, client=fake)

    assert result.success is True
    assert result.measurements["nvlink1"] == 0.0
    assert result.raw_bytes["nvlink1"] == "0.0"


async def test_nvlink_metric_uses_args_link_index():
    # Two links with different states; selecting by args.link must pick the right one.
    device = _device([
        {"name": "link0", "metric": "nvlink_state", "args": {"link": 0}},
        {"name": "link1", "metric": "nvlink_state", "args": {"link": 1}},
        {"name": "link0_err", "metric": "nvlink_error_count", "args": {"link": 0}},
    ])
    fake = FakeNvml(gpus={0: _gpu0(
        nvlink_state={0: True, 1: False},
        nvlink_errors={0: 7},
    )})

    result = await poll_device(device, client=fake)

    assert result.success is True
    assert result.measurements["link0"] == 1.0
    assert result.measurements["link1"] == 0.0
    assert result.measurements["link0_err"] == 7


async def test_scale_is_applied_to_numeric_values():
    # Fan reported as a fraction-of-100 needing x10 scaling, contrived to verify scale.
    device = _device([
        {"name": "temp_scaled", "metric": "temperature_gpu", "scale": 0.5},
    ])
    fake = FakeNvml(gpus={0: _gpu0(temperature_gpu=80.0)})

    result = await poll_device(device, client=fake)

    assert result.success is True
    assert result.measurements["temp_scaled"] == pytest.approx(40.0)
    # raw_bytes is the pre-scale value.
    assert result.raw_bytes["temp_scaled"] == "80.0"


async def test_unknown_metric_key_reports_error():
    device = _device([
        {"name": "mystery", "metric": "flux_capacitance_gigawatts"},
    ])
    fake = FakeNvml(gpus={0: _gpu0()})

    result = await poll_device(device, client=fake)

    assert result.success is False
    assert "mystery" in result.errors
    assert "Unknown metric" in result.errors["mystery"]
    assert "mystery" not in result.measurements


async def test_metric_raising_is_captured_others_succeed():
    device = _device([
        {"name": "temp_gpu", "metric": "temperature_gpu"},
        {"name": "power_w", "metric": "power_usage_w"},
    ])
    # Reading power raises; temperature still succeeds.
    fake = FakeNvml(gpus={0: _gpu0()}, raise_on={"power_usage_milliwatts"})

    result = await poll_device(device, client=fake)

    assert result.success is False
    assert result.measurements == {"temp_gpu": 65.0}
    assert "power_w" in result.errors
    assert "RuntimeError" in result.errors["power_w"]
    # The successful metric still recorded its raw value.
    assert result.raw_bytes["temp_gpu"] == "65.0"


async def test_nan_value_reports_error_without_crashing():
    device = _device([
        {"name": "temp_gpu", "metric": "temperature_gpu"},
    ])
    fake = FakeNvml(gpus={0: _gpu0(temperature_gpu=float("nan"))})

    result = await poll_device(device, client=fake)

    assert result.success is False
    assert "temp_gpu" in result.errors
    assert "NaN/Inf" in result.errors["temp_gpu"]
    assert "temp_gpu" not in result.measurements


async def test_inf_value_reports_error():
    device = _device([
        {"name": "power_w", "metric": "power_usage_w"},
    ])
    fake = FakeNvml(gpus={0: _gpu0(power_mw=float("inf"))})

    result = await poll_device(device, client=fake)

    assert result.success is False
    assert "NaN/Inf" in result.errors["power_w"]


async def test_empty_metrics_succeeds():
    device = _device([])
    fake = FakeNvml(gpus={0: _gpu0()})

    result = await poll_device(device, client=fake)

    assert result.success is True
    assert result.measurements == {}
    assert result.raw_bytes == {}
    assert result.errors == {}


async def test_non_default_gpu_index_fetches_that_handle():
    device = _device(
        [{"name": "temp_gpu", "metric": "temperature_gpu"}],
        gpu_index=3,
    )
    fake = FakeNvml(gpus={3: _gpu0(temperature_gpu=70.0)})

    result = await poll_device(device, client=fake)

    assert result.success is True
    assert result.measurements["temp_gpu"] == 70.0
    assert fake.handles_requested == [3]


async def test_empty_primary_ip_is_allowed():
    device = _device(
        [{"name": "temp_gpu", "metric": "temperature_gpu"}],
        primary_ip="",
    )
    fake = FakeNvml(gpus={0: _gpu0()})

    result = await poll_device(device, client=fake)

    assert result.success is True
    assert result.source_ip == ""


async def test_module_imports_without_pynvml():
    # The module under test must import (and run) without the pynvml native lib.
    import sys

    assert "pynvml" not in sys.modules
