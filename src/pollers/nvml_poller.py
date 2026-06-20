"""Module 9: NVML/DCGM Poller.

Reads every GPU metric defined in a device's Config Context for a single GPU
index via NVML/DCGM. Returns a PollResult. Each metric is read independently so
one bad metric is reported as a per-metric error rather than failing the whole
poll. Detects NaN/Inf on numeric values and reports them as per-metric errors
rather than crashing.

``pynvml`` is a heavy native dependency (it needs the NVIDIA driver) and is
imported lazily inside the default client so this module (and its tests) import
cleanly without it installed. Tests inject a fake ``client`` and never touch
pynvml.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Callable, Protocol

from ..types import DeviceInfo, PollResult


class NvmlLike(Protocol):
    """Handle-based surface the poller needs from an NVML backend.

    A handle is an opaque GPU handle obtained from ``get_handle(index)``; the
    poller passes it back into every per-metric accessor.
    """

    def get_handle(self, index: int) -> Any: ...
    def temperature_gpu(self, handle: Any) -> float: ...
    def power_usage_milliwatts(self, handle: Any) -> float: ...
    def memory_info(self, handle: Any) -> tuple[int, int]: ...  # (used, total)
    def ecc_errors(self, handle: Any, uncorrected: bool) -> int: ...
    def nvlink_state(self, handle: Any, link: int) -> bool: ...
    def nvlink_error_count(self, handle: Any, link: int) -> int: ...
    def pcie_link_gen(self, handle: Any) -> int: ...
    def pcie_link_width(self, handle: Any) -> int: ...
    def fan_speed_percent(self, handle: Any) -> float: ...
    def utilization_gpu_percent(self, handle: Any) -> float: ...


# Dispatch table: metric key -> reader(client, handle, args) -> float.
# Each lambda reads one metric from the NVML handle and returns the raw
# (pre-scale) numeric value. ``args`` carries metric-specific parameters such
# as the NVLink index.
METRIC_MAP: dict[str, Callable[[NvmlLike, Any, dict], float]] = {
    "temperature_gpu": lambda c, h, a: float(c.temperature_gpu(h)),
    "power_usage_w": lambda c, h, a: c.power_usage_milliwatts(h) / 1000.0,
    "memory_used_bytes": lambda c, h, a: float(c.memory_info(h)[0]),
    "memory_total_bytes": lambda c, h, a: float(c.memory_info(h)[1]),
    "ecc_uncorrected_total": lambda c, h, a: float(c.ecc_errors(h, True)),
    "ecc_corrected_total": lambda c, h, a: float(c.ecc_errors(h, False)),
    "nvlink_state": lambda c, h, a: 1.0 if c.nvlink_state(h, int(a["link"])) else 0.0,
    "nvlink_error_count": lambda c, h, a: float(c.nvlink_error_count(h, int(a["link"]))),
    "pcie_link_gen": lambda c, h, a: float(c.pcie_link_gen(h)),
    "pcie_link_width": lambda c, h, a: float(c.pcie_link_width(h)),
    "fan_speed_percent": lambda c, h, a: float(c.fan_speed_percent(h)),
    "utilization_gpu_percent": lambda c, h, a: float(c.utilization_gpu_percent(h)),
}


async def poll_device(
    device: DeviceInfo,
    *,
    client: NvmlLike | None = None,
) -> PollResult:
    """Poll all GPU metrics defined in ``device.config_context``.

    Args:
        device: Device to poll. ``config_context`` carries ``gpu_index`` and a
            ``metrics`` list of ``{"name", "metric", "scale"?, "args"?}`` items.
        client: Optional NVML-like client (tests inject a fake here). When None,
            a pynvml-backed client is constructed lazily so pynvml is only
            imported when actually polling.
    """
    source_ip = device.primary_ip
    gpu_index = int(device.config_context.get("gpu_index", 0))

    owns_client = client is None
    if client is None:
        client = _PynvmlClient()

    measurements: dict = {}
    raw_bytes: dict = {}
    errors: dict = {}

    try:
        handle = await asyncio.to_thread(client.get_handle, gpu_index)

        for metric_def in device.config_context.get("metrics", []) or []:
            name = metric_def["name"]
            try:
                value, raw_str, err = await _read_metric(client, handle, metric_def)
                if err is not None:
                    errors[name] = err
                    continue
                raw_bytes[name] = raw_str
                measurements[name] = value
            except Exception as e:  # noqa: BLE001 — any read failure is per-metric
                errors[name] = f"{type(e).__name__}: {e}"
    finally:
        if owns_client:
            try:
                client.shutdown()
            except Exception:  # noqa: BLE001 — shutdown failures are non-fatal
                pass

    return PollResult(
        device_id=device.device_id,
        timestamp_ns=time.time_ns(),
        measurements=measurements,
        raw_bytes=raw_bytes,
        protocol="nvml",
        source_ip=source_ip,
        success=len(errors) == 0,
        errors=errors,
    )


async def _read_metric(
    client: NvmlLike,
    handle: Any,
    metric_def: dict,
) -> tuple[Any, str, str | None]:
    """Read and scale a single GPU metric. Returns (value, raw_str, error)."""
    metric = metric_def["metric"]
    scale = float(metric_def.get("scale", 1.0))
    args = metric_def.get("args", {}) or {}

    reader = METRIC_MAP.get(metric)
    if reader is None:
        return None, "", f"Unknown metric: {metric}"

    raw = await asyncio.to_thread(reader, client, handle, args)
    raw_str = str(raw)

    if isinstance(raw, float) and (math.isnan(raw) or math.isinf(raw)):
        return None, raw_str, f"NaN/Inf detected in metric {metric}. Raw: {raw_str}"

    value = raw
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = value * scale

    return value, raw_str, None


class _PynvmlClient:
    """pynvml-backed NvmlLike client. Imports pynvml lazily (heavy native lib).

    A single instance owns one NVML session: the first ``get_handle`` call
    initializes NVML and ``shutdown`` tears it down.
    """

    def __init__(self) -> None:
        self._nvml: Any = None

    def _ensure(self) -> Any:
        if self._nvml is None:
            import pynvml  # noqa: PLC0415 — lazy import (needs NVIDIA driver)

            pynvml.nvmlInit()
            self._nvml = pynvml
        return self._nvml

    def get_handle(self, index: int) -> Any:
        nvml = self._ensure()
        return nvml.nvmlDeviceGetHandleByIndex(index)

    def temperature_gpu(self, handle: Any) -> float:
        nvml = self._ensure()
        return float(nvml.nvmlDeviceGetTemperature(handle, nvml.NVML_TEMPERATURE_GPU))

    def power_usage_milliwatts(self, handle: Any) -> float:
        nvml = self._ensure()
        return float(nvml.nvmlDeviceGetPowerUsage(handle))

    def memory_info(self, handle: Any) -> tuple[int, int]:
        nvml = self._ensure()
        info = nvml.nvmlDeviceGetMemoryInfo(handle)
        return int(info.used), int(info.total)

    def ecc_errors(self, handle: Any, uncorrected: bool) -> int:
        nvml = self._ensure()
        counter = (
            nvml.NVML_VOLATILE_ECC
            if uncorrected
            else nvml.NVML_AGGREGATE_ECC
        )
        error_type = (
            nvml.NVML_MEMORY_ERROR_TYPE_UNCORRECTED
            if uncorrected
            else nvml.NVML_MEMORY_ERROR_TYPE_CORRECTED
        )
        return int(nvml.nvmlDeviceGetTotalEccErrors(handle, error_type, counter))

    def nvlink_state(self, handle: Any, link: int) -> bool:
        nvml = self._ensure()
        return bool(nvml.nvmlDeviceGetNvLinkState(handle, link))

    def nvlink_error_count(self, handle: Any, link: int) -> int:
        nvml = self._ensure()
        return int(
            nvml.nvmlDeviceGetNvLinkErrorCounter(
                handle, link, nvml.NVML_NVLINK_ERROR_DL_CRC_DATA
            )
        )

    def pcie_link_gen(self, handle: Any) -> int:
        nvml = self._ensure()
        return int(nvml.nvmlDeviceGetCurrPcieLinkGeneration(handle))

    def pcie_link_width(self, handle: Any) -> int:
        nvml = self._ensure()
        return int(nvml.nvmlDeviceGetCurrPcieLinkWidth(handle))

    def fan_speed_percent(self, handle: Any) -> float:
        nvml = self._ensure()
        return float(nvml.nvmlDeviceGetFanSpeed(handle))

    def utilization_gpu_percent(self, handle: Any) -> float:
        nvml = self._ensure()
        return float(nvml.nvmlDeviceGetUtilizationRates(handle).gpu)

    def shutdown(self) -> None:
        if self._nvml is not None:
            self._nvml.nvmlShutdown()
            self._nvml = None
