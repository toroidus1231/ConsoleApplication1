"""Module 6: Modbus Worker.

Wires the Module 3 Modbus poller into the generic ProtocolWorker loop
(``src/workers/base.py``). This is the reference worker; the BACnet, SNMP and
NVML workers (Modules 7-9) are the same one-liner with their own poller.
"""

from __future__ import annotations

import asyncio

from ..pollers.modbus import poll_device
from .base import ProtocolWorker


def make_modbus_worker(
    config,
    influx,
    attestation,
    event_queue: asyncio.Queue | None = None,
) -> ProtocolWorker:
    """Build a ProtocolWorker bound to the Modbus poller from platform config."""
    return ProtocolWorker(
        protocol="modbus_tcp",
        poll_fn=poll_device,
        influx=influx,
        attestation=attestation,
        event_queue=event_queue,
        netbox_url=config.netbox_url,
        netbox_token=config.netbox_token,
        max_concurrent_polls=config.max_concurrent_polls,
    )
