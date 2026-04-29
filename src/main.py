"""Platform entry point.

Wires every module together and runs all the long-lived asyncio tasks under
a single event loop (per contracts spec §1.1: one process, no threads).
"""

from __future__ import annotations

import asyncio
import os

import uvicorn
from minio import Minio

from .api.server import Deps, create_app, fanout_loop
from .attestation import AttestationEngine
from .config import load_config
from .influx_writer import InfluxWriter
from .orchestrator import Orchestrator, build_power_graph_from_connections
from .test_engine import TestEngine
from .types import Event
from .workers.modbus_worker import ModbusWorker


async def main() -> None:
    config_path = os.environ.get("PLATFORM_CONFIG", "/app/config/platform.yml")
    config = load_config(config_path)

    influx = InfluxWriter(
        url=config.influxdb_url,
        token=config.influxdb_token,
        org=config.influxdb_org,
        bucket=config.influxdb_bucket,
    )

    minio_client = Minio(
        config.minio_endpoint,
        access_key=config.minio_access_key,
        secret_key=config.minio_secret_key,
        secure=False,
    )
    attestation = AttestationEngine(
        facility_name=config.facility_name,
        minio_bucket=config.minio_bucket,
        minio_client=minio_client,
        wal_dir="/app/data/wal",
    )

    event_queue: asyncio.Queue[Event] = asyncio.Queue()

    test_engine = TestEngine(config, influx, attestation, event_queue)

    async def device_loader(device_id):
        # Lazy import to avoid pulling NetBox client on every test run.
        from .netbox_reader import get_devices_by_protocol  # noqa: PLC0415

        for protocol in ("modbus_tcp", "bacnet_ip", "snmp", "nvml"):
            for d in await get_devices_by_protocol(
                config.netbox_url, config.netbox_token, protocol
            ):
                if d.device_id == device_id:
                    return d
        return None

    orchestrator = Orchestrator(
        config,
        executor=test_engine.execute,
        event_queue=event_queue,
        device_loader=device_loader,
        power_graph=build_power_graph_from_connections([]),  # TODO: hydrate from NetBox
    )

    modbus_worker = ModbusWorker(config, influx, attestation, event_queue)

    deps = Deps(
        api_key=config.api_key,
        cors_origins=config.cors_origins,
        orchestrator=orchestrator,
        test_engine=test_engine,
        attestation=attestation,
        influx=influx,
        netbox=object(),
        event_queue=event_queue,
    )
    app = create_app(deps)

    api_server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=config.api_port, log_level=config.log_level.lower())
    )

    await asyncio.gather(
        attestation.run(),
        attestation.wal_recovery_loop(),
        modbus_worker.run(),
        orchestrator.run(),
        fanout_loop(deps),
        api_server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
