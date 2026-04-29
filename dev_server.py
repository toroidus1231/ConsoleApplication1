"""Dev server: runs the full FastAPI app against in-memory fakes plus a rich
digital-twin facility model (29 devices in a power DAG) and serves the
built React frontend as static files.

Run:
    python3 dev_server.py
Then open http://localhost:8080 — paste API key "demo" when prompted.
"""

import asyncio
import math
import random
import time
from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles

from src.api.server import Deps, create_app, fanout_loop
from src.attestation import AttestationEngine
from src.orchestrator import Orchestrator, build_power_graph_from_connections
from src.test_engine import TestEngine
from src.types import AttestationRecord, DeviceInfo, Event, PollResult, PunchListItem, TestRequest

from simulator.facility import build_facility, device_to_api_dict, power_graph
from simulator.sim import (
    UPS_REG_BATTERY_PCT,
    UPS_REG_OUTPUT_VOLTAGE,
    UPS_REG_STATUS,
    UPS_REG_TEST_INITIATE,
    UPSSimulator,
)


# ---------------------------------------------------------------------------
# In-memory fakes
# ---------------------------------------------------------------------------


class DemoMinio:
    def __init__(self):
        self.store = {}

    def put_object(self, bucket_name, object_name, data, length, content_type=""):
        if object_name in self.store:
            return
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


class DemoReconciliation:
    def __init__(self, items):
        self.items = items


# ---------------------------------------------------------------------------
# Punch list + checklists derived from the facility
# ---------------------------------------------------------------------------


def punchlist_for(devices, runs):
    """Synthesize a realistic punch list from the twin: failed tests get
    critical/major rows, model mismatches and discovery anomalies fill in
    the rest."""
    items = []
    SAFETY_TESTS = {"ups_battery_transfer", "ats_transfer", "breaker_trip"}
    for r in runs:
        if r.status == "passed":
            continue
        is_safety = r.test_name in SAFETY_TESTS
        items.append(PunchListItem(
            severity="critical" if is_safety else "major",
            category="test_failure",
            device_id=r.device_id,
            device_name=next((d.name for d in devices if d.device_id == r.device_id),
                             r.device_id),
            site="DC1-Ashburn",
            rack=next((d.rack for d in devices if d.device_id == r.device_id), ""),
            expected="passed", actual=r.status,
            source="active_test",
            evidence_hash=r.evidence_hashes[0] if r.evidence_hashes else None,
            test_id=r.test_id,
            remediation=f"Investigate failure of {r.test_name}.",
        ))

    # A handful of design-vs-actual findings for variety
    items.append(PunchListItem(
        severity="critical", category="identity",
        device_id="srv-A1-09", device_name="dgx-h100-A1-09",
        site="DC1-Ashburn", rack="A1",
        expected="dgx-h100", actual="NOT FOUND",
        source="reconciliation",
        remediation="Server in design but not discovered. Verify network and BMC.",
    ))
    items.append(PunchListItem(
        severity="major", category="firmware",
        device_id="cm2000-A1", device_name="cm2000-A1",
        site="DC1-Ashburn", rack="A1",
        expected="3.2.1", actual="2.0.0",
        source="reconciliation",
        remediation="Update CM2000 firmware to 3.2.1.",
    ))
    items.append(PunchListItem(
        severity="minor", category="firmware",
        device_id="ups-A", device_name="ups-A",
        site="DC1-Ashburn", rack="UPS-A",
        expected="2.5.0", actual="2.4.9",
        source="reconciliation",
        remediation="Symmetra firmware patch available.",
    ))
    items.append(PunchListItem(
        severity="info", category="identity",
        device_id="unknown-N3-99", device_name="unknown-N3-99",
        site="DC1-Ashburn", rack="N3",
        expected="NOT IN DESIGN", actual="ex4300",
        source="discovery",
        remediation="Discovered Juniper switch not in BIM design.",
    ))
    return items


def checklists_for(devices):
    """Per-device-type checklist items (Module 19 schema, spec §7)."""
    by_dev = {}
    for d in devices:
        items = []
        if d.device_type_slug.startswith("apc-rack-pdu"):
            items = [
                {"id": "mounting_bolts", "description": "Verify rack mounting bolts torqued to spec",
                 "category": "structural", "requires_photo": False,
                 "acceptance_criteria": "All bolts torqued to 45 ft-lbs", "completed": False},
                {"id": "cable_labels", "description": "Verify branch cable labels match cable schedule",
                 "category": "labeling", "requires_photo": True,
                 "acceptance_criteria": "Both ends labeled per BIM", "completed": False},
                {"id": "leds", "description": "Verify branch LEDs all green",
                 "category": "cosmetic", "requires_photo": True,
                 "acceptance_criteria": "Solid green on every populated outlet", "completed": False},
            ]
        elif d.device_type_slug.startswith("dgx-h100"):
            items = [
                {"id": "rails", "description": "Verify slide rails fully seated",
                 "category": "structural", "requires_photo": False,
                 "acceptance_criteria": "No play in either rail", "completed": False},
                {"id": "cable_routing", "description": "Verify NDR Infiniband cables routed per design",
                 "category": "labeling", "requires_photo": True,
                 "acceptance_criteria": "Bend radius >= 4× cable diameter", "completed": False},
                {"id": "bezel", "description": "Verify front bezel installed and undamaged",
                 "category": "cosmetic", "requires_photo": False,
                 "acceptance_criteria": "Clean, no scratches, locked", "completed": False},
            ]
        if items:
            by_dev[d.device_id] = items
    return by_dev


# ---------------------------------------------------------------------------
# UPS simulator → poller bridge so /tests/run does something visible
# ---------------------------------------------------------------------------


def _ups_test_device(dev_id="ups-A"):
    return DeviceInfo(
        device_id=dev_id, name=dev_id, primary_ip="10.4.1.10",
        device_type_slug="apc-symmetra",
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
                "monitor_interval_ms": 50,
                "monitor_duration_seconds": 5.0,
                "acceptance": [
                    {"register": "output_voltage", "metric": "settled_value",
                     "operator": "gte", "value": 4700},
                ],
                "restore": {"address": UPS_REG_TEST_INITIATE, "value": 0},
                "restore_verify": [{"register": "ups_status", "operator": "eq", "value": 1}],
                "restore_timeout_seconds": 1,
            }],
        },
        protocol="modbus_tcp", site="DC1-Ashburn", rack="UPS-A", position=10,
    )


def _build_bridge(simulator):
    name_to_addr = {
        "ups_status": UPS_REG_STATUS,
        "output_voltage": UPS_REG_OUTPUT_VOLTAGE,
        "battery_pct": UPS_REG_BATTERY_PCT,
        "battery_voltage": UPS_REG_OUTPUT_VOLTAGE,
    }

    async def poller(device):
        simulator.advance(0.05)  # 50 ms per poll
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
# Main wiring
# ---------------------------------------------------------------------------


async def _seed_attestation_chain(attest, devices):
    """Drop a few sample records on the chain so the attestation viewer
    has something to verify and the punch list evidence modal can resolve
    by hash."""
    rng = random.Random(11)
    for d in devices[:8]:
        await attest.submit(AttestationRecord(
            timestamp_ns=int(time.time_ns()) + rng.randint(0, 10_000),
            device_id=d.device_id, measurement="frequency_hz",
            value=60.0 + rng.uniform(-0.05, 0.05),
            raw_bytes=f"{rng.getrandbits(32):08x}",
            protocol=d.protocol, source_ip=d.primary_ip,
            worker_id="dev-server",
        ))
    await attest.drain()


async def main():
    config = DemoConfig()
    devices, runs = build_facility()
    graph = power_graph(devices)

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
    test_device = _ups_test_device()

    async def device_loader(_id):
        return test_device

    orch = Orchestrator(
        config, executor=test_engine.execute, event_queue=events,
        device_loader=device_loader,
        power_graph=build_power_graph_from_connections(
            [(d.device_id, d.power_source_id) for d in devices if d.power_source_id]
        ),
    )

    await _seed_attestation_chain(attest, devices)

    # Match attestation hashes to punch-list evidence_hash so the modal can
    # resolve real chain records (replace the random hashes with real ones).
    real_hashes = []
    for k in sorted(attest._minio.store):
        import json as _json
        rec = _json.loads(attest._minio.store[k])
        real_hashes.append(rec["hash"])

    punch_items = punchlist_for(devices, runs)
    for i, item in enumerate(punch_items):
        if item.evidence_hash is not None and real_hashes:
            item.evidence_hash = real_hashes[i % len(real_hashes)]

    deps = Deps(
        api_key="demo",
        cors_origins=["http://localhost:8080"],
        orchestrator=orch,
        test_engine=test_engine,
        attestation=attest,
        influx=influx,
        netbox=object(),
        reconciliation=DemoReconciliation(punch_items),
        event_queue=events,
        facility_devices=[device_to_api_dict(d) for d in devices],
        facility_power_graph=graph,
        facility_test_runs=[
            {"test_id": r.test_id, "device_id": r.device_id, "test_name": r.test_name,
             "status": r.status, "started_at": r.started_at,
             "completed_at": r.completed_at, "duration_seconds": r.duration_seconds,
             "evidence_hashes": r.evidence_hashes}
            for r in runs
        ],
        facility_checklists=checklists_for(devices),
        discovery_state={
            "scan-1": {
                "scan_id": "scan-1", "status": "completed",
                "subnets": ["10.4.0.0/16"],
                "devices_found": 28, "devices_classified": 27,
                "devices_unmatched": 1, "errors": [],
                "devices": [
                    {"ip": d.primary_ip, "protocol": d.protocol,
                     "identity": d.name, "device_type_slug": d.device_type_slug,
                     "matched_exact": True}
                    for d in devices
                ],
            }
        },
    )
    app = create_app(deps)

    frontend = Path(__file__).parent / "frontend" / "build"
    if frontend.exists():
        from fastapi import Request
        from fastapi.responses import FileResponse, HTMLResponse

        index_html = (frontend / "index.html").read_text()
        app.mount("/assets", StaticFiles(directory=str(frontend / "assets")), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str, request: Request):
            if full_path.startswith("api/"):
                return HTMLResponse("Not Found", status_code=404)
            asset = frontend / full_path
            if asset.is_file():
                return FileResponse(asset)
            return HTMLResponse(index_html)

    consumer = asyncio.create_task(attest.run())
    orch_task = asyncio.create_task(orch.run())
    fanout = asyncio.create_task(fanout_loop(deps))

    # Continuous low-rate sample stream so the live-test page always has
    # data, plus a periodic UPS test run so the orchestrator queue and
    # timeline are populated.
    async def event_pump():
        i = 0
        live_test_id = None
        while True:
            await asyncio.sleep(0.4)
            t = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            for d in devices[:6]:
                v = 60.0 + math.sin((i + hash(d.device_id) % 100) * 0.1) * 0.05
                await events.put(Event(
                    event_type="poll_result", timestamp=t,
                    data={"device_id": d.device_id, "test_id": None,
                          "measurements": {"frequency_hz": round(v, 4)}},
                ))
            i += 1
            # Every 30 ticks (~12s) launch a UPS test so live page strip-
            # charts get a transient.
            if i % 30 == 0:
                req = TestRequest(test_id=f"live-{i}", device_id="ups-A",
                                  test_name="ups_battery_transfer")
                await orch.submit(req)
                live_test_id = req.test_id

    pump = asyncio.create_task(event_pump())

    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="warning"))
    try:
        await asyncio.gather(consumer, orch_task, fanout, pump, server.serve())
    finally:
        for t in (consumer, orch_task, fanout, pump):
            t.cancel()


if __name__ == "__main__":
    asyncio.run(main())
