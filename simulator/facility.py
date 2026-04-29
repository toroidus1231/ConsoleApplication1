"""A multi-device facility model for driving the UI without real NetBox.

Models a small datacenter:

    utility ─┐
             ├─ ats-1 ─┬─ ups-A ─┬─ pdu-A1 ─ server-A1-{01..04}
             │         │         └─ pdu-A2 ─ server-A2-{01..04}
             │         └─ ups-B ─┬─ pdu-B1 ─ server-B1-{01..04}
             │                   └─ pdu-B2 ─ server-B2-{01..04}
             └─ gen-1 (standby)

Each device has rack/position/protocol metadata, a `status` (passed/failed/
running/queued/pending) and contributes to test history. The model is
deterministic so tests asserting against it are stable.
"""

from __future__ import annotations

import random
import time
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class FacilityDevice:
    device_id: str
    name: str
    device_type_slug: str
    protocol: str
    site: str
    rack: str
    position: int  # rack U
    primary_ip: str
    power_source_id: Optional[str]  # device_id of upstream feed
    test_status: str = "pending"  # pending | queued | running | passed | failed | aborted
    last_test_id: Optional[str] = None
    last_test_at: Optional[str] = None


@dataclass
class FacilityTestRun:
    test_id: str
    device_id: str
    test_name: str
    status: str  # passed | failed | aborted | running | queued
    started_at: str
    completed_at: Optional[str]
    duration_seconds: float
    evidence_hashes: list[str] = field(default_factory=list)


def build_facility() -> tuple[list[FacilityDevice], list[FacilityTestRun]]:
    """Return a deterministic facility fleet + recent test history."""
    devices: list[FacilityDevice] = []

    # Source / utility nodes — no rack U, but they live in NetBox as devices
    devices.append(FacilityDevice(
        device_id="utility-1", name="utility-feed", device_type_slug="utility",
        protocol="snmp", site="DC1-Ashburn", rack="MV1", position=0,
        primary_ip="10.4.0.1", power_source_id=None,
    ))
    devices.append(FacilityDevice(
        device_id="gen-1", name="diesel-gen-1", device_type_slug="cat-3516",
        protocol="modbus_tcp", site="DC1-Ashburn", rack="MV1", position=4,
        primary_ip="10.4.0.10", power_source_id=None,
    ))

    # ATS
    devices.append(FacilityDevice(
        device_id="ats-1", name="ats-main", device_type_slug="asco-7000",
        protocol="modbus_tcp", site="DC1-Ashburn", rack="MV1", position=8,
        primary_ip="10.4.0.20", power_source_id="utility-1",
    ))

    # UPSes
    devices.append(FacilityDevice(
        device_id="ups-A", name="ups-A", device_type_slug="apc-symmetra",
        protocol="modbus_tcp", site="DC1-Ashburn", rack="UPS-A", position=10,
        primary_ip="10.4.1.10", power_source_id="ats-1",
    ))
    devices.append(FacilityDevice(
        device_id="ups-B", name="ups-B", device_type_slug="apc-symmetra",
        protocol="modbus_tcp", site="DC1-Ashburn", rack="UPS-B", position=10,
        primary_ip="10.4.1.20", power_source_id="ats-1",
    ))

    # PDUs
    for ups in ("A", "B"):
        for slot in (1, 2):
            devices.append(FacilityDevice(
                device_id=f"pdu-{ups}{slot}",
                name=f"pdu-{ups}{slot}",
                device_type_slug="apc-rack-pdu",
                protocol="snmp", site="DC1-Ashburn",
                rack=f"{ups}{slot}", position=42,
                primary_ip=f"10.4.{2 if ups == 'A' else 3}.{slot * 10}",
                power_source_id=f"ups-{ups}",
            ))

    # Servers — 4 per PDU
    for ups in ("A", "B"):
        for slot in (1, 2):
            pdu = f"pdu-{ups}{slot}"
            for i in range(1, 5):
                devices.append(FacilityDevice(
                    device_id=f"srv-{ups}{slot}-{i:02d}",
                    name=f"dgx-h100-{ups}{slot}-{i:02d}",
                    device_type_slug="dgx-h100",
                    protocol="redfish", site="DC1-Ashburn",
                    rack=f"{ups}{slot}", position=40 - (i - 1) * 4,
                    primary_ip=f"10.4.{4 if ups == 'A' else 5}.{slot * 10 + i}",
                    power_source_id=pdu,
                ))

    # Power meters — bonus, attached to PDUs
    for ups in ("A", "B"):
        for slot in (1, 2):
            devices.append(FacilityDevice(
                device_id=f"cm2000-{ups}{slot}",
                name=f"cm2000-{ups}{slot}",
                device_type_slug="cm2000",
                protocol="modbus_tcp", site="DC1-Ashburn",
                rack=f"{ups}{slot}", position=43,
                primary_ip=f"10.4.{6 if ups == 'A' else 7}.{slot * 10}",
                power_source_id=f"pdu-{ups}{slot}",
            ))

    # Pre-populated test history covering passed/failed/aborted/running mix.
    # Deterministic — same seed every run.
    rng = random.Random(42)
    now = time.time()
    runs: list[FacilityTestRun] = []
    test_catalog = {
        "ups_battery_transfer": ["ups-A", "ups-B"],
        "ats_transfer": ["ats-1"],
        "breaker_trip": ["ats-1"],
        "pdu_branch_check": [d.device_id for d in devices if d.device_id.startswith("pdu-")],
        "gpu_thermal_under_load": [d.device_id for d in devices if d.device_id.startswith("srv-")][:6],
        "cooling_redundancy": ["ats-1"],
    }

    statuses_pool = ["passed"] * 7 + ["failed"] * 2 + ["aborted"] * 1
    test_offsets = 0
    for test_name, candidate_devices in test_catalog.items():
        for dev_id in candidate_devices:
            status = rng.choice(statuses_pool)
            test_offsets += 1
            started = now - (rng.randint(60, 86400))
            duration = rng.uniform(8, 120)
            test_id = f"t-{test_offsets:04d}"
            runs.append(FacilityTestRun(
                test_id=test_id, device_id=dev_id, test_name=test_name,
                status=status,
                started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started + duration)),
                duration_seconds=round(duration, 2),
                evidence_hashes=[f"{rng.getrandbits(256):064x}"],
            ))
            # Also annotate the device with its latest result.
            for d in devices:
                if d.device_id == dev_id:
                    d.test_status = status
                    d.last_test_id = test_id
                    d.last_test_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started + duration))

    return devices, runs


# Helpers used by the UI ----------------------------------------------------


def device_to_api_dict(d: FacilityDevice) -> dict:
    return {
        "device_id": d.device_id,
        "name": d.name,
        "device_type_slug": d.device_type_slug,
        "protocol": d.protocol,
        "site": d.site,
        "rack": d.rack,
        "position": d.position,
        "primary_ip": d.primary_ip,
        "power_source_id": d.power_source_id,
        "test_status": d.test_status,
        "last_test_id": d.last_test_id,
        "last_test_at": d.last_test_at,
    }


def power_graph(devices: list[FacilityDevice]) -> dict:
    """Return {nodes, edges} suitable for React Flow ingestion.

    Nodes carry test_status so the UI can color them. Edges are the
    powered_device → power_source connections.
    """
    nodes = [
        {
            "id": d.device_id,
            "label": d.name,
            "device_type": d.device_type_slug,
            "rack": d.rack,
            "position": d.position,
            "test_status": d.test_status,
        }
        for d in devices
    ]
    edges = [
        {"id": f"{d.device_id}->{d.power_source_id}",
         "source": d.power_source_id,
         "target": d.device_id}
        for d in devices if d.power_source_id
    ]
    return {"nodes": nodes, "edges": edges}
