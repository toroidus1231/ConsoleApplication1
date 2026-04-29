"""Dev server: runs the full FastAPI app against in-memory fakes and serves
the built React frontend as static files. Use this to demo the UI without
NetBox / InfluxDB / MinIO.

Run:
    python3 dev_server.py
Then open http://localhost:8080 — paste API key "demo" when prompted.
"""

import asyncio
import time
from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles

from src.api.server import Deps, create_app, fanout_loop
from src.attestation import AttestationEngine
from src.orchestrator import Orchestrator, build_power_graph_from_connections
from src.test_engine import TestEngine
from src.types import DeviceInfo, Event, PollResult, PunchListItem, TestRequest, TestResult

from simulator.sim import (
    UPS_REG_BATTERY_PCT,
    UPS_REG_OUTPUT_VOLTAGE,
    UPS_REG_STATUS,
    UPS_REG_TEST_INITIATE,
    UPSSimulator,
)


# ---------------------------------------------------------------------------
# In-memory fakes for MinIO / Influx / NetBox so the demo runs standalone
# ---------------------------------------------------------------------------


class DemoMinio:
    def __init__(self):
        self.store = {}

    def put_object(self, bucket_name, object_name, data, length, content_type=""):
        if object_name in self.store:
            return  # idempotent in demo mode
        body = data.read() if hasattr(data, "read") else bytes(data)
        self.store[object_name] = body

    def get_object(self, bucket_name, object_name):
        class S:
            def __init__(self, b): self._b = b
            def read(self): return self._b
            def close(self): pass
        return S(self.store[object_name])

    def list_objects(self, bucket_name, prefix="", recursive=True):
        class O:
            def __init__(self, n): self.object_name = n
        return [O(k) for k in self.store if k.startswith(prefix)]


class DemoInflux:
    def __init__(self):
        self.writes = []

    async def write_poll(self, result, test_id=None):
        self.writes.append((result, test_id))


class DemoConfig:
    facility_name = "DC1-Ashburn"
    minio_bucket = "attestation"
    max_concurrent_polls = 10
    max_concurrent_tests = 5


# ---------------------------------------------------------------------------
# Fake reconciliation: seed a few punch list items so the UI shows content.
# ---------------------------------------------------------------------------


class DemoReconciliation:
    def __init__(self, items):
        self.items = items


def _sample_punchlist():
    return [
        PunchListItem(
            severity="critical", category="identity",
            device_id="ups-A1-03", device_name="ups-A1-03",
            site="DC1-Ashburn", rack="A1",
            expected="apc-symmetra", actual="NOT FOUND",
            source="reconciliation",
            remediation="Device in design but not discovered. Verify power and network.",
        ),
        PunchListItem(
            severity="major", category="firmware",
            device_id="cm2000-A3-01", device_name="cm2000-A3-01",
            site="DC1-Ashburn", rack="A3",
            expected="3.2.1", actual="2.0.0",
            source="reconciliation",
            remediation="Update firmware to spec version.",
        ),
        PunchListItem(
            severity="major", category="power",
            device_id="server-B7-12", device_name="dgx-h100-12",
            site="DC1-Ashburn", rack="B7",
            expected="pdu-B7-A", actual="pdu-B6-A",
            source="reconciliation",
            remediation="Recable to design power source pdu-B7-A.",
        ),
        PunchListItem(
            severity="minor", category="firmware",
            device_id="opt100-T1-01", device_name="opt100-T1-01",
            site="DC1-Ashburn", rack="T1",
            expected="2.5.0", actual="2.4.9",
            source="reconciliation",
            remediation="Vaisala firmware update available.",
        ),
        PunchListItem(
            severity="info", category="identity",
            device_id="unknown-1", device_name="unknown-N3-99",
            site="DC1-Ashburn", rack="N3",
            expected="NOT IN DESIGN", actual="ex4300",
            source="discovery",
            remediation="Device discovered that is not in the design.",
        ),
    ]


# ---------------------------------------------------------------------------
# UPS simulator → poller bridge so /tests/run actually does something visible
# ---------------------------------------------------------------------------


def _ups_device():
    return DeviceInfo(
        device_id="ups-1", name="ups-A1", primary_ip="10.0.0.5",
        device_type_slug="generic-ups",
        config_context={
            "protocol": "modbus_tcp",
            "active_tests": [{
                "name": "ups_battery_transfer",
                "preconditions": [
                    {"register": "battery_pct", "operator": "gte", "value": 80},
                    {"register": "ups_status", "operator": "eq", "value": 1},
                ],
                "command": {"address": UPS_REG_TEST_INITIATE, "value": 1, "function_code": 6},
                "monitor": ["output_voltage", "battery_pct"],
                "monitor_interval_ms": 5,
                "monitor_duration_seconds": 0.2,
                "acceptance": [
                    {"register": "output_voltage", "metric": "settled_value",
                     "operator": "gte", "value": 4700},
                ],
                "restore": {"address": UPS_REG_TEST_INITIATE, "value": 0},
                "restore_verify": [{"register": "ups_status", "operator": "eq", "value": 1}],
                "restore_timeout_seconds": 1,
            }],
        },
        protocol="modbus_tcp", site="DC1-Ashburn", rack="A1", position=1,
    )


def _build_bridge(simulator):
    name_to_addr = {
        "ups_status": UPS_REG_STATUS,
        "output_voltage": UPS_REG_OUTPUT_VOLTAGE,
        "battery_pct": UPS_REG_BATTERY_PCT,
        "battery_voltage": UPS_REG_OUTPUT_VOLTAGE,
    }

    async def poller(device):
        simulator.advance(0.005)
        return PollResult(
            device_id=device.device_id, timestamp_ns=time.time_ns(),
            measurements={n: float(simulator.registers.read(a)) for n, a in name_to_addr.items()},
            raw_bytes={n: f"{simulator.registers.read(a):04x}" for n, a in name_to_addr.items()},
            protocol="modbus_tcp", source_ip=device.primary_ip, success=True,
        )

    async def writer(device, address, value, function_code=6):
        simulator.registers.write(address, int(value))
        return True, None

    return poller, writer


# ---------------------------------------------------------------------------
# Wire everything up
# ---------------------------------------------------------------------------


async def _seed_attestation_chain(attest):
    """Drop a handful of sample records on the chain so the attestation viewer
    has something to show."""
    from src.types import AttestationRecord
    for i in range(5):
        await attest.submit(AttestationRecord(
            timestamp_ns=int(time.time_ns()) + i,
            device_id=f"cm2000-A3-0{i}", measurement="frequency_hz",
            value=60.0 + (i * 0.01), raw_bytes=f"177{i}",
            protocol="modbus_tcp", source_ip=f"10.0.0.{10 + i}",
            worker_id="dev-server",
        ))
    await attest.drain()


async def main():
    config = DemoConfig()
    attest = AttestationEngine(
        facility_name=config.facility_name,
        minio_bucket=config.minio_bucket,
        minio_client=DemoMinio(),
        wal_dir="/tmp/dev-wal",
        worker_id="dev-server",
    )
    influx = DemoInflux()
    events: asyncio.Queue[Event] = asyncio.Queue()

    sim = UPSSimulator()
    poller, writer = _build_bridge(sim)
    test_engine = TestEngine(config, influx, attest, events,
                             poller=poller, writer=writer,
                             manual_confirm_timeout=300.0)

    device = _ups_device()

    async def device_loader(_id):
        return device

    orch = Orchestrator(
        config, executor=test_engine.execute, event_queue=events,
        device_loader=device_loader,
        power_graph=build_power_graph_from_connections([]),
    )

    await _seed_attestation_chain(attest)

    deps = Deps(
        api_key="demo",
        cors_origins=["http://localhost:8080"],
        orchestrator=orch,
        test_engine=test_engine,
        attestation=attest,
        influx=influx,
        netbox=object(),
        reconciliation=DemoReconciliation(_sample_punchlist()),
        event_queue=events,
    )
    app = create_app(deps)

    # Serve the built React frontend at "/" with SPA fallback so client-side
    # routes (/devices, /punchlist, ...) load index.html instead of 404'ing.
    frontend = Path(__file__).parent / "frontend" / "build"
    if frontend.exists():
        from fastapi import Request
        from fastapi.responses import FileResponse, HTMLResponse

        index_html = (frontend / "index.html").read_text()

        # Mount /assets first so the bundle JS/CSS hit StaticFiles (304/200).
        app.mount("/assets", StaticFiles(directory=str(frontend / "assets")), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str, request: Request):
            # API routes are mounted earlier; this only catches non-/api paths
            # that didn't match anywhere else. Always return the SPA shell.
            if full_path.startswith("api/"):
                return HTMLResponse("Not Found", status_code=404)
            asset = frontend / full_path
            if asset.is_file():
                return FileResponse(asset)
            return HTMLResponse(index_html)

    # Background tasks
    consumer = asyncio.create_task(attest.run())
    orch_task = asyncio.create_task(orch.run())
    fanout = asyncio.create_task(fanout_loop(deps))

    # Periodically push events so the dashboard's live feed is populated.
    async def event_pump():
        i = 0
        while True:
            await asyncio.sleep(2.0)
            await events.put(Event(
                event_type="poll_result",
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                data={"device_id": "cm2000-A3-01", "measurements": {"frequency_hz": 60.0 + 0.001 * i}},
            ))
            i += 1
    pump = asyncio.create_task(event_pump())

    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="warning"))
    try:
        await asyncio.gather(consumer, orch_task, fanout, pump, server.serve())
    finally:
        for t in (consumer, orch_task, fanout, pump):
            t.cancel()


if __name__ == "__main__":
    asyncio.run(main())
