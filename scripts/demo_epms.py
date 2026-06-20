"""Demo server: serves the built frontend + a Platform seeded with EPMS data.

Boots the real FastAPI app (Module 21) over an in-memory Platform populated with
Schneider CM2000 power meters and Masterpact MTZ breakers (EPMS), power-category
punch-list findings, and a background SSE publisher emitting live power readings —
so the dashboard renders as a real Electrical Power Monitoring System would.

    python -m scripts.demo_epms   # serves on http://127.0.0.1:8099
"""

from __future__ import annotations

import asyncio
import os
import random

import uvicorn

import src.api.server as server_mod
from src.api.server import create_app
from src.api.state import Platform
from src.config import PlatformConfig
from src.timeutil import iso_now, ns_now
from src.types import DeviceInfo, Event, PollResult, PunchListItem, TestResult

# Point the static mount at the repo's built frontend (not the container path).
server_mod.FRONTEND_DIR = os.path.abspath("frontend/build")

API_KEY = "demo-key"

CONFIG = PlatformConfig(
    facility_name="DC1-Ashburn — EPMS", scan_subnets=["10.0.2.0/24"],
    netbox_url="http://netbox:8080", netbox_token="x",
    influxdb_url="http://influxdb:8086", influxdb_token="x", influxdb_org="commissioning",
    influxdb_bucket="commissioning", minio_endpoint="minio:9000", minio_access_key="x",
    minio_secret_key="x", minio_bucket="attestation", minio_retention_days=2555,
    max_concurrent_polls=50, max_concurrent_tests=5, api_port=8099, api_key=API_KEY,
    cors_origins=["*"], ntp_server="pool.ntp.org", log_level="INFO",
)

# --- EPMS device fleet (Schneider power meters + Masterpact breakers) ---------
CM2000_REGS = ["frequency_hz", "voltage_ll_avg", "current_3phase_avg",
               "real_power_total_kw", "apparent_power_kva", "power_factor_total"]


def _meter(did, name, rack, slug="cm2000"):
    return DeviceInfo(
        device_id=did, name=name, primary_ip=f"10.0.2.{did[-2:] if did[-2:].isdigit() else 10}",
        device_type_slug=slug,
        config_context={"protocol": "modbus_tcp", "active_tests": (
            [{"name": "breaker_trip_timing", "description": "Masterpact MTZ trip timing",
              "preconditions": [{"register": "breaker_position", "operator": "eq", "value": 1}]}]
            if slug == "masterpact-mtz" else [])},
        protocol="modbus_tcp", site="DC1-Ashburn", rack=rack, position=int(did[-2:] or 1))


DEVICES = [
    _meter("d11", "CM2000-A3-Main", "A3"),
    _meter("d12", "CM2000-B4-PDU", "B4"),
    _meter("d13", "CM2000-C1-UPS-Out", "C1"),
    _meter("d21", "MTZ-Main-1", "A1", slug="masterpact-mtz"),
    _meter("d22", "MTZ-Tie", "A1", slug="masterpact-mtz"),
    _meter("d23", "PM8000-D2-Branch", "D2", slug="pm8000"),
]


def _poll(did, regs):
    return PollResult(device_id=did, timestamp_ns=ns_now(), measurements=regs,
                      raw_bytes={k: "0000" for k in regs}, protocol="modbus_tcp",
                      source_ip="10.0.2.11", success=True)


LAST_POLL = {
    "d11": _poll("d11", {"frequency_hz": 60.01, "voltage_ll_avg": 480.2, "current_3phase_avg": 451.0,
                          "real_power_total_kw": 312.5, "apparent_power_kva": 330.1, "power_factor_total": 0.95}),
    "d12": _poll("d12", {"frequency_hz": 59.99, "voltage_ll_avg": 479.6, "current_3phase_avg": 388.0,
                          "real_power_total_kw": 268.0, "apparent_power_kva": 327.0, "power_factor_total": 0.82}),
    "d21": _poll("d21", {"breaker_position": 1.0, "current_ia": 612.0, "voltage_v12": 480.4,
                          "active_power_total": 505.2}),
}

PUNCH = [
    PunchListItem(severity="critical", category="power", device_id="d12", device_name="CM2000-B4-PDU",
                  site="DC1-Ashburn", rack="B4", expected="phase imbalance < 10%", actual="17.2%",
                  source="reconciliation", remediation="Rebalance single-phase branch loads across A/B/C.",
                  status="open"),
    PunchListItem(severity="major", category="power", device_id="d22", device_name="MTZ-Tie",
                  site="DC1-Ashburn", rack="A1", expected="power factor >= 0.95", actual="0.82",
                  source="passive_read", remediation="Verify PF correction bank staging.", status="open"),
    PunchListItem(severity="major", category="power", device_id="d23", device_name="PM8000-D2-Branch",
                  site="DC1-Ashburn", rack="D2", expected="neutral < 50% phase", actual="63%",
                  source="passive_read", remediation="Investigate harmonic/triplen neutral current.", status="open"),
    PunchListItem(severity="minor", category="firmware", device_id="d21", device_name="MTZ-Main-1",
                  site="DC1-Ashburn", rack="A1", expected="Micrologic FW 2.1", actual="3.0",
                  source="reconciliation", remediation="Confirm trip-curve compatibility for FW 3.0.", status="open"),
    PunchListItem(severity="info", category="identity", device_id="d99", device_name="unknown-meter",
                  site="DC1-Ashburn", rack="D2", expected="NOT IN DESIGN", actual="acuvim-ii",
                  source="discovery", remediation="Add to design or remove from network.", status="open"),
]

TESTS = {
    "t-1": TestResult(test_id="t-1", device_id="d21", test_name="breaker_trip_timing",
                      status="passed", started_at=iso_now(), completed_at=iso_now(),
                      duration_seconds=4.6),
}


class _Worker:
    def __init__(self, n, errs=0):
        self.devices = list(range(n))
        self.unreachable = set(range(errs))


class _Reader:
    def verify_chain(self):
        return {"chain_valid": True, "breaks": [], "length": 184213,
                "first": {"sequence": 1}, "last": {"sequence": 184213}}

    def chain(self, *a):
        return []

    def get_by_hash(self, h):
        return None

    def verify(self, h):
        return {"valid": True, "chain_position": 0, "chain_length": 184213, "breaks": []}


async def _health():
    return {"netbox": "ok", "influxdb": "ok", "minio": "ok"}


platform = Platform(config=CONFIG)
platform.get_devices = lambda: _devices()
platform.last_poll = LAST_POLL
platform.punchlist = PUNCH
platform.test_results = TESTS
platform.attestation_reader = _Reader()
platform.health_probe = _health
platform.workers = {"modbus_tcp": _Worker(24), "bacnet_ip": _Worker(12, 1),
                    "snmp": _Worker(8), "nvml": _Worker(16)}


async def _devices():
    return DEVICES


app = create_app(platform)


@app.on_event("startup")
async def _start():
    asyncio.create_task(_publisher())


async def _publisher():
    """Emit live EPMS telemetry so the dashboard's activity feed is populated."""
    await asyncio.sleep(0.5)
    i = 0
    while True:
        dev = DEVICES[i % 3]  # rotate the three CM2000 meters
        kw = round(260 + random.uniform(-20, 60), 1)
        platform.publish(Event("poll_result", iso_now(), {
            "device_id": dev.device_id, "protocol": "modbus_tcp",
            "measurements": {"voltage_ll_avg": round(480 + random.uniform(-2, 2), 1),
                             "current_3phase_avg": round(kw * 1.2, 1),
                             "real_power_total_kw": kw,
                             "power_factor_total": round(random.uniform(0.82, 0.98), 2)}}))
        if i % 7 == 3:
            platform.publish(Event("test_completed", iso_now(),
                                   {"test_id": "t-1", "device_id": "d21", "status": "passed"}))
        if i % 11 == 5:
            platform.publish(Event("worker_health", iso_now(),
                                   {"device_id": "d12", "protocol": "modbus_tcp", "status": "RECOVERED"}))
        i += 1
        await asyncio.sleep(1.0)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8099, log_level="warning")
