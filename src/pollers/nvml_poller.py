"""Module 9: NVML/DCGM Poller.

Reads NVIDIA GPU metrics via pynvml. The Config Context names which GPU
(``gpu_index``) and which metrics to pull, mapping a user-chosen field name to
a standard NVML metric source.

Config Context shape:
    {
      "protocol": "nvml",
      "gpu_index": 0,
      "metrics": [
        {"name": "temp_c",     "source": "temperature"},
        {"name": "power_w",    "source": "power_usage"},
        {"name": "mem_used",   "source": "memory_used"},
        {"name": "util_pct",   "source": "gpu_utilization"},
        {"name": "ecc_errors", "source": "ecc_uncorrected_total"}
      ]
    }

Supported ``source`` values and their units:

  temperature             °C
  power_usage             W            (pynvml returns mW; we divide by 1000)
  gpu_utilization         %
  memory_utilization      %
  memory_used             bytes
  memory_free             bytes
  memory_total            bytes
  fan_speed               %
  ecc_uncorrected_total   count

pynvml is synchronous; calls run via ``asyncio.to_thread``. Tests inject a
``reader(gpu_index, source) -> value`` callable so pynvml is never loaded.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable

from ..types import DeviceInfo, PollResult


# reader(gpu_index, source_name) -> numeric
Reader = Callable[[int, str], float]


SUPPORTED_SOURCES = {
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


def _default_reader(gpu_index: int, source: str) -> float:
    """pynvml bridge. Imported lazily to keep test envs light."""
    import pynvml  # noqa: PLC0415

    pynvml.nvmlInit()
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_index)
        if source == "temperature":
            return float(pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU))
        if source == "power_usage":
            return float(pynvml.nvmlDeviceGetPowerUsage(handle)) / 1000.0
        if source == "gpu_utilization":
            return float(pynvml.nvmlDeviceGetUtilizationRates(handle).gpu)
        if source == "memory_utilization":
            return float(pynvml.nvmlDeviceGetUtilizationRates(handle).memory)
        if source == "memory_used":
            return float(pynvml.nvmlDeviceGetMemoryInfo(handle).used)
        if source == "memory_free":
            return float(pynvml.nvmlDeviceGetMemoryInfo(handle).free)
        if source == "memory_total":
            return float(pynvml.nvmlDeviceGetMemoryInfo(handle).total)
        if source == "fan_speed":
            return float(pynvml.nvmlDeviceGetFanSpeed(handle))
        if source == "ecc_uncorrected_total":
            return float(
                pynvml.nvmlDeviceGetTotalEccErrors(
                    handle,
                    pynvml.NVML_MEMORY_ERROR_TYPE_UNCORRECTED,
                    pynvml.NVML_VOLATILE_ECC,
                )
            )
        raise ValueError(f"Unsupported NVML source: {source}")
    finally:
        pynvml.nvmlShutdown()


async def poll_device(
    device: DeviceInfo,
    *,
    reader: Reader | None = None,
) -> PollResult:
    """Poll all metrics in ``device.config_context["metrics"]``."""
    reader = reader or _default_reader

    ctx = device.config_context
    gpu_index = int(ctx.get("gpu_index", 0))
    host = device.primary_ip or "localhost"

    measurements: dict = {}
    raw_bytes: dict = {}
    errors: dict = {}

    for metric in ctx.get("metrics", []) or []:
        name = metric["name"]
        source = metric["source"]
        scale = float(metric.get("scale", 1.0))

        if source not in SUPPORTED_SOURCES:
            errors[name] = f"Unsupported NVML source: {source}"
            continue

        try:
            raw = await asyncio.to_thread(reader, gpu_index, source)
        except Exception as e:  # noqa: BLE001 — per-metric error, poll continues
            errors[name] = f"{type(e).__name__}: {e}"
            continue

        raw_bytes[name] = str(raw)

        try:
            measurements[name] = float(raw) * scale
        except (TypeError, ValueError) as e:
            errors[name] = f"Non-numeric NVML value '{raw}': {e}"

    return PollResult(
        device_id=device.device_id,
        timestamp_ns=time.time_ns(),
        measurements=measurements,
        raw_bytes=raw_bytes,
        protocol="nvml",
        source_ip=host,
        success=len(errors) == 0,
        errors=errors,
    )
