"""Switchgear-commissioning facility model for the digital twin.

Models a realistic main-electrical-room lineup the spec actually targets:

    Utility A 13.8kV ─┬─ MV Main A ──┐
                     │              ├─ MV Tie ──┐
    Utility B 13.8kV ─┴─ MV Main B ──┤          │
                                     │          │
                       MV Bus A ─────┘          │
                       MV Bus B ────────────────┘

    MV Bus A ─┬─ XFMR-A1 (2500 kVA, 13.8kV→480V) ─ LV Bus A1
              ├─ XFMR-A2 (2500 kVA)               ─ LV Bus A2
              └─ Gen Paralleling Bus
    MV Bus B ─┬─ XFMR-B1                          ─ LV Bus B1
              └─ XFMR-B2                          ─ LV Bus B2

    LV Bus A1 ─┬─ ATS-1 ─ Critical Bus 1 ─ UPS-A ─ PDU lineup A
               ├─ MTZ Feeder breakers (×6)
               └─ CM2000 power meters per feeder

    Generators: 2× 1500 kW diesel on paralleling switchgear, feed
    LV Bus A1/B1 via ATS-1/2 on utility loss.

    Monitoring:
      - SEL-751 protective relays on every MV breaker + main LV
      - Eaton InsulGard PD monitors on each MV bus section
      - Vaisala OPT100 DGA monitors on each oil-filled XFMR
      - Qualitrol 118ITM on each dry-type XFMR
      - Schneider CM2000 power meters on every critical feeder

This is what the spec §3 register maps were written for.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FacilityDevice:
    device_id: str
    name: str
    device_type_slug: str
    protocol: str
    site: str
    rack: str
    position: int
    primary_ip: str
    power_source_id: Optional[str]
    test_status: str = "pending"
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
    """Realistic MV/LV switchgear commissioning fleet + acceptance test
    history. Deterministic — RNG seeded so tests are stable."""
    devices: list[FacilityDevice] = []

    # --- Utility feeds (no rack U) ---------------------------------------
    devices.append(FacilityDevice(
        device_id="util-A", name="Utility Feed A · 13.8kV",
        device_type_slug="utility-mv", protocol="snmp",
        site="DC1-Ashburn", rack="MV-A", position=0,
        primary_ip="10.4.0.1", power_source_id=None,
    ))
    devices.append(FacilityDevice(
        device_id="util-B", name="Utility Feed B · 13.8kV",
        device_type_slug="utility-mv", protocol="snmp",
        site="DC1-Ashburn", rack="MV-B", position=0,
        primary_ip="10.4.0.2", power_source_id=None,
    ))

    # --- MV main + tie breakers (Schneider GMA-class) --------------------
    for side, ip_oct in (("A", 10), ("B", 11)):
        devices.append(FacilityDevice(
            device_id=f"mv-main-{side}",
            name=f"MV Main Breaker {side} · 1200A",
            device_type_slug="schneider-gma-1200a", protocol="modbus_tcp",
            site="DC1-Ashburn", rack=f"MV-{side}", position=20,
            primary_ip=f"10.4.0.{ip_oct}",
            power_source_id=f"util-{side}",
        ))
        devices.append(FacilityDevice(
            device_id=f"sel-mv-main-{side}",
            name=f"SEL-751 · MV Main {side}",
            device_type_slug="sel-751", protocol="modbus_tcp",
            site="DC1-Ashburn", rack=f"MV-{side}", position=22,
            primary_ip=f"10.4.0.{ip_oct + 100}",
            power_source_id=f"mv-main-{side}",
        ))
        devices.append(FacilityDevice(
            device_id=f"insulgard-mv-{side}",
            name=f"InsulGard PD · MV Bus {side}",
            device_type_slug="eaton-insulgard", protocol="modbus_tcp",
            site="DC1-Ashburn", rack=f"MV-{side}", position=18,
            primary_ip=f"10.4.0.{ip_oct + 50}",
            power_source_id=f"mv-main-{side}",
        ))

    devices.append(FacilityDevice(
        device_id="mv-tie",
        name="MV Tie Breaker · 1200A",
        device_type_slug="schneider-gma-1200a", protocol="modbus_tcp",
        site="DC1-Ashburn", rack="MV-T", position=20,
        primary_ip="10.4.0.20", power_source_id="mv-main-A",
    ))
    devices.append(FacilityDevice(
        device_id="sel-mv-tie",
        name="SEL-751 · MV Tie",
        device_type_slug="sel-751", protocol="modbus_tcp",
        site="DC1-Ashburn", rack="MV-T", position=22,
        primary_ip="10.4.0.120", power_source_id="mv-tie",
    ))

    # --- Step-down transformers (oil + dry type, with monitors) ----------
    for side in ("A", "B"):
        for n in (1, 2):
            xfmr_id = f"xfmr-{side}{n}"
            ip = f"10.4.{1 if side == 'A' else 2}.{n}"
            devices.append(FacilityDevice(
                device_id=xfmr_id,
                name=f"XFMR {side}{n} · 2500 kVA · 13.8kV/480V",
                device_type_slug="oil-xfmr-2500kva", protocol="modbus_tcp",
                site="DC1-Ashburn", rack=f"XFMR-{side}", position=10 + (n - 1) * 10,
                primary_ip=ip, power_source_id=f"mv-main-{side}",
            ))
            # DGA monitor on each oil-filled transformer (spec §3.5)
            devices.append(FacilityDevice(
                device_id=f"opt100-{side}{n}",
                name=f"Vaisala OPT100 DGA · XFMR {side}{n}",
                device_type_slug="vaisala-opt100", protocol="modbus_tcp",
                site="DC1-Ashburn", rack=f"XFMR-{side}", position=12 + (n - 1) * 10,
                primary_ip=f"10.4.{3 if side == 'A' else 4}.{n}",
                power_source_id=xfmr_id,
            ))
            # Qualitrol winding-temp monitor (spec §3.6)
            devices.append(FacilityDevice(
                device_id=f"qualitrol-{side}{n}",
                name=f"Qualitrol 118ITM · XFMR {side}{n}",
                device_type_slug="qualitrol-118itm", protocol="modbus_tcp",
                site="DC1-Ashburn", rack=f"XFMR-{side}", position=14 + (n - 1) * 10,
                primary_ip=f"10.4.{5 if side == 'A' else 6}.{n}",
                power_source_id=xfmr_id,
            ))

    # --- LV switchgear lineups -------------------------------------------
    # Each LV bus: incoming MTZ, outgoing MTZ feeders, CM2000 metering.
    for side in ("A", "B"):
        for n in (1, 2):
            bus_id = f"lv-bus-{side}{n}"
            xfmr_id = f"xfmr-{side}{n}"

            # Incoming Masterpact MTZ from XFMR
            devices.append(FacilityDevice(
                device_id=f"mtz-inc-{side}{n}",
                name=f"MTZ Incoming · LV Bus {side}{n} · 4000A",
                device_type_slug="schneider-mtz-4000a", protocol="modbus_tcp",
                site="DC1-Ashburn", rack=f"LV-{side}{n}", position=40,
                primary_ip=f"10.4.10.{(0 if side == 'A' else 10) + n}",
                power_source_id=xfmr_id,
            ))
            # SEL-751 on the incoming
            devices.append(FacilityDevice(
                device_id=f"sel-inc-{side}{n}",
                name=f"SEL-751 · LV Inc {side}{n}",
                device_type_slug="sel-751", protocol="modbus_tcp",
                site="DC1-Ashburn", rack=f"LV-{side}{n}", position=42,
                primary_ip=f"10.4.11.{(0 if side == 'A' else 10) + n}",
                power_source_id=f"mtz-inc-{side}{n}",
            ))
            # 4 outgoing MTZ feeders + CM2000 power meter per feeder
            for f in range(1, 5):
                feeder = f"mtz-fdr-{side}{n}-F{f}"
                devices.append(FacilityDevice(
                    device_id=feeder,
                    name=f"MTZ Feeder · {side}{n}-F{f} · 1200A",
                    device_type_slug="schneider-mtz-1200a", protocol="modbus_tcp",
                    site="DC1-Ashburn", rack=f"LV-{side}{n}", position=38 - (f - 1) * 4,
                    primary_ip=f"10.4.12.{(0 if side == 'A' else 20) + (n - 1) * 8 + f}",
                    power_source_id=f"mtz-inc-{side}{n}",
                ))
                devices.append(FacilityDevice(
                    device_id=f"cm2000-{side}{n}-F{f}",
                    name=f"CM2000 · {side}{n}-F{f}",
                    device_type_slug="cm2000", protocol="modbus_tcp",
                    site="DC1-Ashburn", rack=f"LV-{side}{n}", position=37 - (f - 1) * 4,
                    primary_ip=f"10.4.13.{(0 if side == 'A' else 20) + (n - 1) * 8 + f}",
                    power_source_id=feeder,
                ))

    # --- Standby generators + paralleling switchgear ---------------------
    devices.append(FacilityDevice(
        device_id="gen-1", name="Standby Gen 1 · 1500 kW · Caterpillar 3516B",
        device_type_slug="cat-3516b", protocol="modbus_tcp",
        site="DC1-Ashburn", rack="GEN", position=10,
        primary_ip="10.4.20.1", power_source_id=None,
    ))
    devices.append(FacilityDevice(
        device_id="gen-2", name="Standby Gen 2 · 1500 kW · Caterpillar 3516B",
        device_type_slug="cat-3516b", protocol="modbus_tcp",
        site="DC1-Ashburn", rack="GEN", position=20,
        primary_ip="10.4.20.2", power_source_id=None,
    ))
    devices.append(FacilityDevice(
        device_id="par-bus", name="Generator Paralleling Bus",
        device_type_slug="paralleling-switchgear", protocol="modbus_tcp",
        site="DC1-Ashburn", rack="GEN", position=30,
        primary_ip="10.4.20.10", power_source_id="gen-1",
    ))
    for n in (1, 2):
        devices.append(FacilityDevice(
            device_id=f"ats-{n}",
            name=f"ATS {n} · ASCO 7000 · 4000A",
            device_type_slug="asco-7000", protocol="modbus_tcp",
            site="DC1-Ashburn", rack=f"LV-A{n}", position=44,
            primary_ip=f"10.4.21.{n}",
            power_source_id="par-bus",
        ))

    # --- Critical-bus UPS (industrial scale) -----------------------------
    for side in ("A", "B"):
        devices.append(FacilityDevice(
            device_id=f"ups-{side}",
            name=f"UPS {side} · APC Symmetra MW · 750 kW",
            device_type_slug="apc-symmetra-mw", protocol="modbus_tcp",
            site="DC1-Ashburn", rack=f"UPS-{side}", position=20,
            primary_ip=f"10.4.30.{1 if side == 'A' else 2}",
            power_source_id=f"ats-{1 if side == 'A' else 2}",
        ))

    # ---------------------------------------------------------------------
    # Test history — these are the actual electrical commissioning tests
    # ---------------------------------------------------------------------
    rng = random.Random(42)
    now = time.time()
    runs: list[FacilityTestRun] = []

    test_catalog = {
        # Cable + insulation
        "cable_hipot": [
            "mv-main-A", "mv-main-B", "mv-tie",
            "mtz-inc-A1", "mtz-inc-A2", "mtz-inc-B1", "mtz-inc-B2",
        ],
        "megger_insulation": [
            "xfmr-A1", "xfmr-A2", "xfmr-B1", "xfmr-B2",
            "mv-main-A", "mv-main-B",
        ],
        # Transformer-specific
        "transformer_turns_ratio": [d.device_id for d in devices if d.device_id.startswith("xfmr-")],
        "doble_power_factor": [d.device_id for d in devices if d.device_id.startswith("xfmr-")],
        "polarization_index": [d.device_id for d in devices if d.device_id.startswith("xfmr-")],
        "dga_initial_sample": [d.device_id for d in devices if d.device_id.startswith("xfmr-")],
        # Protective relays
        "sel_secondary_injection": [d.device_id for d in devices if d.device_id.startswith("sel-")],
        "sel_primary_injection":   ["sel-mv-main-A", "sel-mv-main-B", "sel-mv-tie"],
        # Breaker mechanical/electrical
        "breaker_contact_resistance": [
            "mv-main-A", "mv-main-B", "mv-tie",
            "mtz-inc-A1", "mtz-inc-A2", "mtz-inc-B1", "mtz-inc-B2",
        ],
        "breaker_trip_timing": [
            "mtz-inc-A1", "mtz-inc-A2", "mtz-inc-B1", "mtz-inc-B2",
        ],
        # Power-system tests
        "ats_transfer": ["ats-1", "ats-2"],
        "ups_battery_transfer": ["ups-A", "ups-B"],
        "generator_load_bank": ["gen-1", "gen-2"],
        "generator_paralleling": ["par-bus"],
        "black_start": ["par-bus"],
        # Ground grid
        "ground_grid_resistance": ["util-A", "util-B"],
    }

    statuses_pool = ["passed"] * 18 + ["failed"] * 3 + ["aborted"] * 1
    test_offsets = 0
    for test_name, candidate_devices in test_catalog.items():
        for dev_id in candidate_devices:
            status = rng.choice(statuses_pool)
            test_offsets += 1
            started = now - rng.randint(60, 86400 * 5)  # last 5 days
            duration = rng.uniform(30, 1800)
            test_id = f"t-{test_offsets:04d}"
            runs.append(FacilityTestRun(
                test_id=test_id, device_id=dev_id, test_name=test_name,
                status=status,
                started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started + duration)),
                duration_seconds=round(duration, 2),
                evidence_hashes=[f"{rng.getrandbits(256):064x}"],
            ))
            for d in devices:
                if d.device_id == dev_id:
                    d.test_status = status
                    d.last_test_id = test_id
                    d.last_test_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started + duration))

    return devices, runs


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
