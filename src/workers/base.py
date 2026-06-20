"""Module 6 (core): generic protocol worker — poll → attest → write loop.

Contracts spec §2.1: a worker runs as one async task per protocol, polling every
device of that protocol, then for each result writing telemetry to InfluxDB and
submitting each measurement to the attestation engine. The Modbus, BACnet, SNMP
and NVML workers are all the *same* loop with a different ``poll_fn`` and protocol
label, so the loop lives here once and the per-protocol workers (Modules 6-9) are
thin wrappers around it.

Health model (contracts spec §1.3): three consecutive poll failures mark a device
UNREACHABLE. We emit a ``worker_health`` event on the transition into and out of
UNREACHABLE rather than on every cycle, so the SSE stream isn't flooded.

Everything external is injected: the poll function, the InfluxDB writer, the
attestation engine, and the NetBox device reader. That keeps the loop unit-testable
with in-memory fakes and no live devices.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Protocol

from ..netbox_reader import get_devices_by_protocol
from ..timeutil import iso_now
from ..types import AttestationRecord, DeviceInfo, Event, PollResult


UNREACHABLE_THRESHOLD = 3
DEFAULT_POLL_INTERVAL_SECONDS = 30

PollFn = Callable[[DeviceInfo], Awaitable[PollResult]]
DeviceReader = Callable[[str, str, str], Awaitable[list[DeviceInfo]]]


class InfluxLike(Protocol):
    async def write_poll(self, result: PollResult, test_id: str | None = ...) -> None: ...


class AttestationLike(Protocol):
    async def submit(self, record: AttestationRecord) -> None: ...


class ProtocolWorker:
    """Continuous poll loop for every device of one protocol.

    Args:
        protocol: Protocol label, e.g. ``"modbus_tcp"``. Also the NetBox filter.
        poll_fn: Async callable taking a DeviceInfo and returning a PollResult.
        influx: InfluxDB writer (anything with ``write_poll``).
        attestation: Attestation engine (anything with ``submit``).
        event_queue: Queue the API server drains for SSE. May be None in tests.
        netbox_url / netbox_token: For device refresh.
        max_concurrent_polls: Semaphore bound on simultaneous device polls.
        device_reader: Injectable NetBox reader (defaults to Module 2).
    """

    def __init__(
        self,
        protocol: str,
        poll_fn: PollFn,
        *,
        influx: InfluxLike,
        attestation: AttestationLike,
        event_queue: asyncio.Queue | None = None,
        netbox_url: str = "",
        netbox_token: str = "",
        max_concurrent_polls: int = 50,
        device_reader: DeviceReader = get_devices_by_protocol,
    ):
        self.protocol = protocol
        self._poll_fn = poll_fn
        self._influx = influx
        self._attestation = attestation
        self._event_queue = event_queue
        self._netbox_url = netbox_url
        self._netbox_token = netbox_token
        self._max_concurrent_polls = max_concurrent_polls
        self._device_reader = device_reader

        self.devices: list[DeviceInfo] = []
        self.failures: dict[str, int] = {}
        self.unreachable: set[str] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def refresh_devices(self) -> list[DeviceInfo]:
        """Reload this protocol's device list from NetBox."""
        self.devices = await self._device_reader(
            self._netbox_url, self._netbox_token, self.protocol
        )
        return self.devices

    async def run(self) -> None:
        """Refresh devices once, then poll forever. Cancel the task to stop."""
        await self.refresh_devices()
        while True:
            await self.poll_once()
            await asyncio.sleep(self._min_interval())

    async def poll_once(self) -> list[PollResult]:
        """Poll every device once, bounded by the concurrency semaphore."""
        sem = asyncio.Semaphore(self._max_concurrent_polls)
        results = await asyncio.gather(
            *(self._poll_one(sem, d) for d in self.devices)
        )
        return list(results)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _min_interval(self) -> int:
        """Shortest poll_interval_seconds across devices (contracts spec §2)."""
        return min(
            (
                int(d.config_context.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS))
                for d in self.devices
            ),
            default=DEFAULT_POLL_INTERVAL_SECONDS,
        )

    async def _poll_one(self, sem: asyncio.Semaphore, device: DeviceInfo) -> PollResult:
        async with sem:
            result = await self._poll_fn(device)
        await self._handle_result(device, result)
        return result

    async def _handle_result(self, device: DeviceInfo, result: PollResult) -> None:
        if not result.success:
            await self._record_failure(device)
            return

        # Recovery: clear failure state and announce if it had been unreachable.
        self.failures[device.device_id] = 0
        if device.device_id in self.unreachable:
            self.unreachable.discard(device.device_id)
            await self._emit_health(device, "RECOVERED")

        await self._influx.write_poll(result)
        for name, value in result.measurements.items():
            await self._attestation.submit(
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
        await self._emit(
            Event(
                event_type="poll_result",
                timestamp=iso_now(),
                data={
                    "device_id": device.device_id,
                    "protocol": self.protocol,
                    "measurements": result.measurements,
                },
            )
        )

    async def _record_failure(self, device: DeviceInfo) -> None:
        count = self.failures.get(device.device_id, 0) + 1
        self.failures[device.device_id] = count
        if count >= UNREACHABLE_THRESHOLD and device.device_id not in self.unreachable:
            self.unreachable.add(device.device_id)
            await self._emit_health(device, "UNREACHABLE")

    async def _emit_health(self, device: DeviceInfo, status: str) -> None:
        await self._emit(
            Event(
                event_type="worker_health",
                timestamp=iso_now(),
                data={
                    "device_id": device.device_id,
                    "protocol": self.protocol,
                    "status": status,
                    "consecutive_failures": self.failures.get(device.device_id, 0),
                },
            )
        )

    async def _emit(self, event: Event) -> None:
        if self._event_queue is not None:
            await self._event_queue.put(event)
