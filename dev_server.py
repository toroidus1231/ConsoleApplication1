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
from datetime import datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles

from src.api.server import Deps, create_app, fanout_loop
from src.attestation import AttestationEngine
from src.equipment_models.ats import ATSSpec, ats_run_record
from src.equipment_models.cable import CableSpec, hipot_run_record
from src.equipment_models.generator import GeneratorSpec, generator_run_record
from src.equipment_models.transformer import TransformerSpec, transformer_run_record
from src.equipment_models.ups import UPSSpec, ups_run_record
from src.equipment_registry import EquipmentRegistry
from src.equipment_views import build_panel as build_equipment_panel
from src.evidence_store import InMemoryEvidenceStore
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
from simulator.telemetry import live_telemetry, push_soe, telemetry_to_dict


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
    """Synthesize a realistic punch list from the twin: failed electrical
    commissioning tests get critical/major rows with test-specific
    remediation text matching what an actual commissioning engineer would
    write up."""
    from src.reconciliation import CrossReading, check_sensor_drift  # noqa: PLC0415

    # Critical (safety-impacting) tests vs major (non-safety) per spec §5.2.
    SAFETY_TESTS = {
        "ats_transfer", "breaker_trip_timing", "ups_battery_transfer",
        "sel_primary_injection", "generator_paralleling", "black_start",
        "cable_hipot",
    }

    # Test-specific remediation strings — same level of detail a CX engineer
    # would write up after a failed test.
    REMEDIATION = {
        "cable_hipot":
            "Hipot leakage exceeded 0.5 mA at 80% rated voltage. "
            "Megger insulation, check terminations for tracking, "
            "re-test before energizing.",
        "megger_insulation":
            "Insulation resistance below 100 MΩ at 1000 VDC. "
            "Verify dryness (PI test), inspect for moisture ingress, "
            "consider drying out before re-test.",
        "transformer_turns_ratio":
            "TTR deviation >0.5% from nameplate. Verify tap changer "
            "position, inspect for shorted turns, contact manufacturer.",
        "doble_power_factor":
            "Doble power factor >1% on bushing C1. Schedule bushing "
            "replacement at next outage; trend at 6-month intervals.",
        "polarization_index":
            "PI ratio <2.0 indicates damp insulation. Run heater 24h, "
            "re-test. If PI still <2.0, send winding for cleaning.",
        "dga_initial_sample":
            "Acetylene >2 ppm in initial sample — active arcing. "
            "STOP energization. Sample again in 24h. Escalate to OEM.",
        "sel_secondary_injection":
            "Pickup current 5% off relay setting. Re-verify SET M "
            "config matches coordination study, re-inject.",
        "sel_primary_injection":
            "Trip time outside +/-2 cycles of coordination study. "
            "Verify CT polarity and ratio, re-inject.",
        "breaker_contact_resistance":
            "Contact resistance >50 µΩ. Inspect contacts for pitting, "
            "polish or replace, re-test with Doble Vanguard.",
        "breaker_trip_timing":
            "Trip time exceeded 5 cycles target. Mechanism inspection "
            "required: check spring charge, latch wear, lubrication.",
        "ats_transfer":
            "ATS failed to transfer within 10s of utility loss. Verify "
            "controller logic, generator ready signal, transfer "
            "interlock. Coordinate with downstream UPS verification "
            "(spec §4.3).",
        "ups_battery_transfer":
            "Output voltage dropped below 228V during transfer. Battery "
            "string load test required; replace cells failing >20% "
            "capacity. Re-test transfer.",
        "generator_load_bank":
            "Failed to hold rated load for 4h continuous. Check fuel "
            "delivery, cooling system, exhaust backpressure. Trend "
            "exhaust temps and re-test.",
        "generator_paralleling":
            "Sync check failed: voltage/frequency outside +/-2% / +/-0.2 Hz "
            "for >5s. Verify governor and AVR tuning per OEM startup "
            "settings.",
        "black_start":
            "Black-start sequence failed: gen 2 did not assume load "
            "within 30s of gen 1 paralleling. Verify load-sharing "
            "controller, breaker close-permissive logic.",
        "ground_grid_resistance":
            "Fall-of-potential measurement >5Ω. Verify ground grid "
            "bonding, add supplemental rods, re-test before energizing.",
    }

    items = []
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
            remediation=REMEDIATION.get(r.test_name, f"Investigate failure of {r.test_name}."),
        ))

    # Design-vs-actual findings for the switchgear lineup
    items.append(PunchListItem(
        severity="critical", category="identity",
        device_id="sel-mv-tie-spare", device_name="SEL-751 · MV Tie spare",
        site="DC1-Ashburn", rack="MV-T",
        expected="sel-751", actual="NOT FOUND",
        source="reconciliation",
        remediation="Spare protective relay specified in coordination "
                    "study not installed in switchgear cubicle. "
                    "Coordinate with switchgear OEM for retrofit.",
    ))
    items.append(PunchListItem(
        severity="major", category="firmware",
        device_id="sel-mv-main-A", device_name="SEL-751 · MV Main A",
        site="DC1-Ashburn", rack="MV-A",
        expected="R109-V1", actual="R107-V0",
        source="reconciliation",
        remediation="SEL-751 firmware below coordination-study reference. "
                    "Schedule firmware upgrade to R109-V1 during planned "
                    "outage (requires re-injection per IEEE C37.230).",
    ))
    items.append(PunchListItem(
        severity="major", category="power",
        device_id="cm2000-A1-F3", device_name="CM2000 · A1-F3",
        site="DC1-Ashburn", rack="LV-A1",
        expected="mtz-fdr-A1-F3", actual="mtz-fdr-A1-F4",
        source="reconciliation",
        remediation="Power meter wired to wrong feeder per cable "
                    "schedule. Re-terminate CT secondaries per drawing "
                    "E-301 rev C.",
    ))
    items.append(PunchListItem(
        severity="minor", category="firmware",
        device_id="opt100-A1", device_name="Vaisala OPT100 · XFMR A1",
        site="DC1-Ashburn", rack="XFMR-A",
        expected="2.5.0", actual="2.4.9",
        source="reconciliation",
        remediation="Vendor firmware patch available — improves H2/CO "
                    "cross-sensitivity. Apply at next maintenance window.",
    ))
    items.append(PunchListItem(
        severity="info", category="identity",
        device_id="unknown-NB-MV", device_name="Unknown device on MV-B subnet",
        site="DC1-Ashburn", rack="MV-B",
        expected="NOT IN DESIGN", actual="schneider-rmu",
        source="discovery",
        remediation="Discovered Schneider RMU not in BIM design — "
                    "likely vendor add-on. Update single-line drawing.",
    ))

    # Sensor-drift findings from the bench-instrument cross-validation
    # workflow. The twin pretends a portable Fluke 8508A reference DMM
    # has been clamped to bus A1 and read at the same timestamp as the
    # permanent CM2000-A1 voltage sensor — divergence flagged by
    # check_sensor_drift().
    drift_readings = [
        # CM2000 on feeder A1-F1 reads 412V vs Fluke 8508A reference 478.4V
        # clamped to the same bus during commissioning sweep.
        CrossReading(
            physical_link="lv-bus-A1.voltage_ll_avg @ feeder F1 CT",
            primary_device_id="cm2000-A1-F1",
            primary_device_name="CM2000 · A1-F1",
            primary_reading=412.0,
            primary_timestamp_ns=1_712_847_600_000_000_000,
            primary_unit="V",
            reference_device_id="fluke-8508a-bench",
            reference_device_name="Fluke 8508A bench DMM",
            reference_reading=478.4,
            reference_timestamp_ns=1_712_847_600_000_500_000,
            reference_calibration_cert="sha256:f8508a-2026-q2",
        ),
        # Smaller drift on a different feeder
        CrossReading(
            physical_link="lv-bus-B1.voltage_ll_avg @ feeder F2 CT",
            primary_device_id="cm2000-B1-F2",
            primary_device_name="CM2000 · B1-F2",
            primary_reading=480.7,
            primary_timestamp_ns=1_712_847_600_000_000_000,
            primary_unit="V",
            reference_device_id="fluke-8508a-bench",
            reference_device_name="Fluke 8508A bench DMM",
            reference_reading=489.5,
            reference_timestamp_ns=1_712_847_600_000_500_000,
            reference_calibration_cert="sha256:f8508a-2026-q2",
        ),
    ]
    items.extend(check_sensor_drift(drift_readings))

    # Hipot test result attested via the SCPI poller (Vitrek 95X). MV cable
    # run between MV Main A and XFMR-A1 — leakage trending up across the
    # last three commissioning runs but still within tolerance.
    items.append(PunchListItem(
        severity="minor", category="insulation",
        device_id="cable-mv-main-A-to-xfmr-A1",
        device_name="MV cable: MV Main A → XFMR A1",
        site="DC1-Ashburn", rack="MV-A",
        expected="leakage <= 0.5 mA at 80% rated (Vitrek 95X cert sha256:vitrek-2026-q2)",
        actual="0.42 mA at 11.04 kV (last 3 runs: 0.31 → 0.37 → 0.42 mA)",
        source="active_test",
        remediation=(
            "Hipot result within tolerance but leakage trending up. "
            "Schedule Megger PI test at next maintenance window; "
            "inspect cable terminations for tracking."
        ),
    ))

    # Eaton InsulGard PD activity flagged on MV Bus A — partial discharge
    # magnitude rising over 24h trend. Per spec §3.4 evaluation rules.
    items.append(PunchListItem(
        severity="major", category="insulation",
        device_id="insulgard-mv-A",
        device_name="InsulGard PD · MV Bus A",
        site="DC1-Ashburn", rack="MV-A",
        expected="pd_magnitude_max <= 50 pC, no upward trend",
        actual="pd_magnitude_max = 84 pC, +35% over 24h",
        source="passive_read",
        remediation=(
            "PD activity rising on MV Bus A. Suspect cable joint or "
            "support insulator. Schedule offline PD scan at next outage; "
            "tag for visual inspection in switchgear cubicle."
        ),
    ))

    # DGA on XFMR-A1 — acetylene above warning threshold, possible arcing
    items.append(PunchListItem(
        severity="critical", category="insulation",
        device_id="opt100-A1",
        device_name="Vaisala OPT100 DGA · XFMR A1",
        site="DC1-Ashburn", rack="XFMR-A",
        expected="acetylene <= 2 ppm (spec §3.5: >2 ppm = active arcing)",
        actual="acetylene = 3.4 ppm",
        source="passive_read",
        remediation=(
            "STOP all active testing on XFMR-A1. Acetylene >2 ppm "
            "indicates active arcing per spec §3.5. Pull manual oil "
            "sample, send to lab for gas-in-oil + furan analysis. "
            "Coordinate de-energization with utility."
        ),
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


def _build_equipment_registry(today: datetime) -> EquipmentRegistry:
    """One source of truth for every equipment spec. Both the EvidenceStore
    seeds and the live-telemetry generator pull from this registry."""
    reg = EquipmentRegistry()

    # Cables — feeders behind each MV/LV breaker
    cable_cfg = {
        "mv-main-A":  (13.8, 2017, 220.0, 0.045),
        "mv-main-B":  (13.8, 2017, 220.0, 0.045),
        "mv-tie":     (13.8, 2017, 220.0, 0.045),
        "mtz-inc-A1": (0.48, 2021, 380.0, 0.030),
        "mtz-inc-A2": (0.48, 2021, 380.0, 0.030),
        "mtz-inc-B1": (0.48, 2021, 380.0, 0.030),
        "mtz-inc-B2": (0.48, 2021, 380.0, 0.030),
    }
    for cable_id, (kv, year, m_ohm, age) in cable_cfg.items():
        reg.cables[cable_id] = CableSpec(cable_id=cable_id, rated_kv=kv,
                                         install_year=year,
                                         initial_megohm=m_ohm,
                                         aging_per_year=age)

    # Transformers — XFMR-A1 has an emerging arcing fault
    reg.transformers["xfmr-A1"] = TransformerSpec(
        xfmr_id="xfmr-A1", install_year=2018,
        fault_state="active_arcing", fault_severity=1.1,
        fault_onset=today - timedelta(days=110))
    for xid in ("xfmr-A2", "xfmr-B1", "xfmr-B2"):
        reg.transformers[xid] = TransformerSpec(xfmr_id=xid, install_year=2018)

    # Generators
    reg.generators["gen-1"] = GeneratorSpec(gen_id="gen-1", install_year=2020,
                                            annual_runtime_hours=85)
    reg.generators["gen-2"] = GeneratorSpec(gen_id="gen-2", install_year=2020,
                                            annual_runtime_hours=82)

    # UPSes — stable per-UPS weak cell IDs
    reg.upses["ups-A"] = UPSSpec(ups_id="ups-A", install_year=2022,
                                 install_month=4, weak_cell_ids=[17, 142, 199])
    reg.upses["ups-B"] = UPSSpec(ups_id="ups-B", install_year=2022,
                                 install_month=4, weak_cell_ids=[34, 88, 211])

    # ATS units cross-reference gen + downstream UPSes
    for ats_id in ("ats-1", "ats-2"):
        reg.ats_units[ats_id] = ATSSpec(
            ats_id=ats_id,
            upstream_gen=reg.generators["gen-1"],
            downstream_ups=[reg.upses["ups-A"], reg.upses["ups-B"]])

    return reg


def _seed_evidence_store(store, registry: EquipmentRegistry, today: datetime):
    """Walk the registry and write historical runs into the store. Each
    series is the same per-device spec evaluated at past run dates so
    every panel's history is internally consistent."""
    for cable_id, spec in registry.cables.items():
        for days_ago in (540, 180, 90, 1):
            store.put(cable_id, "cable_hipot",
                      hipot_run_record(spec, today - timedelta(days=days_ago)))
    for xfmr_id, spec in registry.transformers.items():
        for days_ago in (180, 90, 30, 0):
            store.put(xfmr_id, "transformer_commissioning",
                      transformer_run_record(spec, today - timedelta(days=days_ago)))
    for gen_id, spec in registry.generators.items():
        for days_ago in (365, 180, 90, 0):
            store.put(gen_id, "generator_loadbank",
                      generator_run_record(spec, today - timedelta(days=days_ago)))
    for ups_id, spec in registry.upses.items():
        for days_ago in (180, 90, 30, 1):
            store.put(ups_id, "ups_battery_transfer",
                      ups_run_record(spec, today - timedelta(days=days_ago)))
    for ats_id, spec in registry.ats_units.items():
        for days_ago in (180, 90, 30, 0):
            store.put(ats_id, "ats_transfer",
                      ats_run_record(spec, today - timedelta(days=days_ago)))


def _equipment_provider(evidence_store):
    """Returns a function (kind, device_id) -> dict that always reads
    through the EvidenceStore. Every panel is now a view of records the
    test engine wrote (or, in demo, that the dev seed wrote through the
    same store interface)."""
    def provider(kind: str, device_id: str) -> dict:
        return build_equipment_panel(kind, device_id, evidence_store)
    return provider


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

    # Equipment registry: per-device specs driving every panel.
    # Live telemetry and the EvidenceStore both read from the same
    # registry so today's reading on a tile == today's DGA-history
    # tail value == today's run record, by construction.
    today = datetime(2026, 5, 1)
    registry = _build_equipment_registry(today)
    evidence_store = InMemoryEvidenceStore()
    _seed_evidence_store(evidence_store, registry, today)

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

    # Seed sequence-of-events log so the alarm banner has real content.
    push_soe("critical", "xfmr-A1",
             "DGA: acetylene 3.4 ppm — active arcing per spec §3.5. STOP energization.")
    push_soe("alarm", "mv-main-A",
             "Cable hipot leakage exceeded 0.5 mA at 80% rated voltage. Breaker locked out.")
    push_soe("alarm", "insulgard-mv-A",
             "PD magnitude 84 pC trending +35% / 24h.")
    push_soe("warn", "sel-mv-main-A",
             "51 PHASE TOC pickup 510 A (threshold 480 A) — clearing.")
    push_soe("info", "ats-1",
             "ATS transfer test scheduled 09:30 UTC (manual confirmation pending).")
    push_soe("info", "gen-1",
             "Standby — ready. Last load-bank test PASS @ 04:18 UTC.")
    push_soe("warn", "cm2000-A1-F1",
             "Sensor drift: 13.9% off Fluke 8508A reference (cert sha256:f8508a-2026-q2).")

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
        telemetry_provider=lambda: telemetry_to_dict(
            live_telemetry(devices, runs, registry=registry)),
        equipment_provider=_equipment_provider(evidence_store),
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
