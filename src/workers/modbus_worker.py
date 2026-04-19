"""Module 6: Modbus Worker.

Combines Modules 2 (NetBox reader) + 3 (Modbus poller) + 4 (InfluxDB writer) +
5 (attestation engine) into a continuous poll-hash-write loop. One task per
process. Respects ``workers.max_concurrent_polls`` from platform config.

After 3 consecutive failed polls a device is flagged UNREACHABLE via
``event_queue``. One successful poll resets the failure count.

Dependencies are injected (poller, device loader) so the worker can be unit
tested without a real NetBox or Modbus simulator.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable

from ..attestation import AttestationEngine
from ..influx_writer import InfluxWriter
from ..netbox_reader import get_devices_by_protocol
from ..pollers.modbus import poll_device as modbus_poll_device
from ..types import AttestationRecord, DeviceInfo, Event, PollResult


UNREACHABLE_THRESHOLD = 3


Poller = Callable[[DeviceInfo], Awaitable[PollResult]]
DeviceLoader = Callable[[], Awaitable[list[DeviceInfo]]]


class ModbusWorker:
    def __init__(
        self,
        config,
        influx: InfluxWriter,
        attestation: AttestationEngine,
        event_queue: asyncio.Queue[Event],
        *,
        poller: Poller | None = None,
        device_loader: DeviceLoader | None = None,
    ):
        self.config = config
        self.influx = influx
        self.attestation = attestation
        self.event_queue = event_queue
        self._poller = poller or modbus_poll_device
        self._device_loader = device_loader or self._default_device_loader
        self.devices: list[DeviceInfo] = []
        self.failures: dict[str, int] = {}
        self._unreachable: set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def refresh_devices(self) -> None:
        self.devices = await self._device_loader()

    async def poll_all_once(self) -> tuple[int, int]:
        """Poll every known device once in parallel (respecting the configured
        concurrency cap) and return ``(success_count, failure_count)``."""
        sem = asyncio.Semaphore(self.config.max_concurrent_polls)
        results = await asyncio.gather(
            *(self._poll_one(sem, device) for device in self.devices)
        )
        successes = sum(1 for ok in results if ok)
        return successes, len(results) - successes

    async def run(self) -> None:
        """Main loop: refresh devices, poll all, sleep until the next cycle.

        Sleep duration is the minimum ``poll_interval_seconds`` across loaded
        devices (default 30s when unspecified). Cancel this task to stop.
        """
        await self.refresh_devices()
        while True:
            await self.poll_all_once()
            await asyncio.sleep(self._next_interval_seconds())

    def _next_interval_seconds(self) -> int:
        return min(
            (
                int(d.config_context.get("poll_interval_seconds", 30))
                for d in self.devices
            ),
            default=30,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _default_device_loader(self) -> list[DeviceInfo]:
        return await get_devices_by_protocol(
            self.config.netbox_url, self.config.netbox_token, "modbus_tcp"
        )

    async def _poll_one(self, sem: asyncio.Semaphore, device: DeviceInfo) -> bool:
        async with sem:
            result = await self._poller(device)

            if not result.success:
                await self._handle_failure(device)
                return False

            await self._handle_success(device, result)
            return True

    async def _handle_failure(self, device: DeviceInfo) -> None:
        self.failures[device.device_id] = self.failures.get(device.device_id, 0) + 1
        if (
            self.failures[device.device_id] >= UNREACHABLE_THRESHOLD
            and device.device_id not in self._unreachable
        ):
            self._unreachable.add(device.device_id)
            await self.event_queue.put(
                Event(
                    event_type="worker_health",
                    timestamp=_now_iso(),
                    data={"device_id": device.device_id, "status": "UNREACHABLE"},
                )
            )

    async def _handle_success(self, device: DeviceInfo, result: PollResult) -> None:
        # Clear any prior failure streak and (if we'd flagged it) announce recovery.
        had_failures = self.failures.get(device.device_id, 0) > 0
        self.failures[device.device_id] = 0
        if device.device_id in self._unreachable:
            self._unreachable.discard(device.device_id)
            await self.event_queue.put(
                Event(
                    event_type="worker_health",
                    timestamp=_now_iso(),
                    data={"device_id": device.device_id, "status": "RECOVERED"},
                )
            )
        elif had_failures:
            # Transient failure recovered before we'd flagged UNREACHABLE — no event.
            pass

        await self.influx.write_poll(result)

        for name, value in result.measurements.items():
            await self.attestation.submit(
                AttestationRecord(
                    timestamp_ns=result.timestamp_ns,
                    device_id=result.device_id,
                    measurement=name,
                    value=float(value),
                    raw_bytes=result.raw_bytes.get(name, ""),
                    protocol=result.protocol,
                    source_ip=result.source_ip,
                    worker_id="",  # filled in by the attestation engine
                )
            )

        await self.event_queue.put(
            Event(
                event_type="poll_result",
                timestamp=_now_iso(),
                data={
                    "device_id": device.device_id,
                    "measurements": dict(result.measurements),
                },
            )
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
