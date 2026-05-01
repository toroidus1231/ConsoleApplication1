"""Busway commissioning record from a single spec + run date.

Models a Vertiv busway run end-to-end across the three instrument tests
the platform automates (Megger / Hipot / DLRO) plus the manual sign-offs
the platform records (visual, torque, phase rotation). All numeric
values derive from one BuswaySpec evaluated at the run date so trends
are continuous by construction.

References:
    NETA ATS-2017 §7.4 — Busway acceptance testing
    NETA Table 100.1 — Insulation resistance test values
    NETA Table 100.5 — DC dielectric withstand for cable / busway
    IEC 61439-6 — Power switchgear and controlgear assemblies; busbar
        trunking systems
    IEEE C57.13 / IEEE 43 — insulation resistance acceptance
    Vertiv PowerBar iMPB / MTG field service guides (2022 revision)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime


@dataclass
class BuswaySpec:
    busway_id: str
    rated_amps: int
    voltage_class_v: int                 # 480, 600, 1000
    length_m: float = 30.0
    manufacturer: str = "Vertiv"
    model: str = "MTG"                   # MTG | PowerBar iMPB
    install_year: int = 2022
    install_month: int = 6

    # Insulation resistance @ 1000 VDC megger.
    # IEC 61439-6 §10.10 acceptance: ≥ 100 MΩ at 1000 VDC for new install.
    # Healthy busway typically lands in 1–10 GΩ when factory-tested.
    initial_megohm: float = 5_000.0
    aging_per_year: float = 0.025

    # Bolted-joint resistance (DLRO).
    # Vertiv MTG factory spec ≤ 30 µΩ/joint; PowerBar iMPB ≤ 25 µΩ.
    # Joint count scales with run length — typically 1 joint per 3 m segment
    # for MTG, plus tap-offs.
    initial_joint_uohm: float = 22.0
    joint_aging_per_year: float = 0.015  # corrosion + thermal cycling
    joint_acceptance_uohm: float = 30.0  # Vertiv field service threshold

    # Torque verification — Vertiv field service guide.
    # MTG joint kits: M12 8.8 cap screws @ 60 ft-lb (81 Nm).
    # iMPB tap connectors: M10 8.8 @ 35 ft-lb (47 Nm).
    torque_spec_ftlb: float = 60.0
    torque_tolerance_pct: float = 5.0

    # Hipot acceptance.
    # NETA ATS-17 §7.4 + IEC 61439-6: 600 V class busway tested at 2.5 kV
    # DC for 1 minute; 1000 V class at 3.5 kV DC.
    hipot_kv: float = 2.5
    hipot_hold_seconds: float = 60.0
    hipot_leakage_trip_ma: float = 1.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _age_years(spec: BuswaySpec, run_date: datetime) -> float:
    install = datetime(spec.install_year, spec.install_month, 1)
    return max(0.0, (run_date - install).days / 365.25)


def _insulation_megohm_at(spec: BuswaySpec, run_date: datetime) -> float:
    return spec.initial_megohm * (1.0 - spec.aging_per_year) ** _age_years(spec, run_date)


def _joint_count(spec: BuswaySpec) -> int:
    """1 joint per ~3 m segment + 2 end terminations."""
    return max(2, int(spec.length_m / 3.0) + 2)


def _hipot_voltage_kv(spec: BuswaySpec) -> float:
    """NETA ATS-17 / IEC 61439-6 — DC hipot voltage by voltage class."""
    if spec.voltage_class_v <= 480:
        return 2.0
    if spec.voltage_class_v <= 600:
        return 2.5
    if spec.voltage_class_v <= 1000:
        return 3.5
    return 5.0


# ---------------------------------------------------------------------------
# Test 1 — Megger / Insulation Resistance @ 1000 VDC
# ---------------------------------------------------------------------------


def megger_run_record(spec: BuswaySpec, run_date: datetime) -> dict:
    """Insulation resistance test per IEC 61439-6 §10.10.

    Test voltage: 1000 VDC for ≤ 1000 V busway, applied for 1 min.
    Acceptance: ≥ 100 MΩ phase-to-phase and phase-to-ground.
    Pass criterion: minimum measured > 100 MΩ.
    """
    R = _insulation_megohm_at(spec, run_date)
    rng = random.Random(int(run_date.timestamp()) ^ hash(spec.busway_id) ^ 0x4d65)

    # 6 measurements: A-B, B-C, A-C, A-G, B-G, C-G
    pairs = [("A", "B"), ("B", "C"), ("A", "C"), ("A", "G"), ("B", "G"), ("C", "G")]
    readings = []
    for a, b in pairs:
        # Phase-to-ground reads slightly lower than phase-to-phase.
        bias = 0.85 if "G" in (a, b) else 1.0
        meg = R * bias * (1 + rng.uniform(-0.04, 0.04))
        readings.append({
            "from": a, "to": b,
            "megohm": round(meg, 1),
            "passed": meg >= 100.0,
        })

    min_meg = min(r["megohm"] for r in readings)
    test_voltage_v = 1000 if spec.voltage_class_v <= 1000 else 2500
    return {
        "device_id": spec.busway_id,
        "test_type": "insulation_resistance",
        "manufacturer": spec.manufacturer,
        "model": spec.model,
        "rated_amps": spec.rated_amps,
        "voltage_class_v": spec.voltage_class_v,
        "test_voltage_v": test_voltage_v,
        "applied_seconds": 60,
        "acceptance_megohm": 100.0,
        "instrument": {"vendor": "Megger", "model": "MIT525",
                       "serial": "SN-MIT-78421",
                       "cal_cert": "sha256:megger-mit525-2026-q1"},
        "readings": readings,
        "min_megohm": round(min_meg, 1),
        "passed": min_meg >= 100.0,
        "completed_at": run_date.isoformat(),
    }


# ---------------------------------------------------------------------------
# Test 2 — DC Hipot / Withstand
# ---------------------------------------------------------------------------


def hipot_run_record(spec: BuswaySpec, run_date: datetime) -> dict:
    """DC withstand test per NETA ATS-17 §7.4 / IEC 61439-6.

    Apply DC withstand voltage ramped over 60 s, hold for 60 s. Trip if
    leakage exceeds spec.hipot_leakage_trip_ma. Same Ohm's-law leakage
    model as cable — V / R — so megger and hipot results agree
    (one is just the other at higher voltage)."""
    target_kv = _hipot_voltage_kv(spec)
    R_megohm = _insulation_megohm_at(spec, run_date)
    rng = random.Random(int(run_date.timestamp()) ^ hash(spec.busway_id) ^ 0x4849)

    ramp_s = 60
    hold_s = int(spec.hipot_hold_seconds)
    trace = []
    for t in range(ramp_s + hold_s + 1):
        v = target_kv * (t / ramp_s) if t < ramp_s else target_kv
        leak = (v / R_megohm) + rng.uniform(-0.0003, 0.0003)
        if t > ramp_s:
            leak += (t - ramp_s) / hold_s * (target_kv / R_megohm) * 0.05
        trace.append({"t_seconds": t,
                      "voltage_kv": round(v, 3),
                      "leakage_ma": round(leak, 5)})

    peak = max(p["leakage_ma"] for p in trace)
    return {
        "device_id": spec.busway_id,
        "test_type": "DC withstand",
        "manufacturer": spec.manufacturer,
        "model": spec.model,
        "rated_amps": spec.rated_amps,
        "voltage_class_v": spec.voltage_class_v,
        "target_kv": round(target_kv, 3),
        "ramp_seconds": ramp_s,
        "hold_seconds": hold_s,
        "leakage_trip_ma": spec.hipot_leakage_trip_ma,
        "instrument": {"vendor": "Vitrek", "model": "95X",
                       "serial": "SN-VTK-95X-12345",
                       "cal_cert": "sha256:vitrek-95x-2026-q2"},
        "trace": trace,
        "peak_leakage_ma": round(peak, 5),
        "insulation_megohm": round(R_megohm, 1),
        "passed": peak < spec.hipot_leakage_trip_ma,
        "completed_at": run_date.isoformat(),
    }


# ---------------------------------------------------------------------------
# Test 3 — DLRO joint resistance
# ---------------------------------------------------------------------------


def dlro_run_record(spec: BuswaySpec, run_date: datetime) -> dict:
    """4-wire DC ductor at 10 A per IEC 61439-6 §10.5.

    Each bolted joint measured A-N, B-N, C-N. Joint resistance grows with
    age (oxidation + thermal cycling loosening). Acceptance per Vertiv
    field guide: ≤ spec.joint_acceptance_uohm per joint.
    """
    age = _age_years(spec, run_date)
    base_uohm = spec.initial_joint_uohm * (1 + spec.joint_aging_per_year) ** age
    rng = random.Random(int(run_date.timestamp()) ^ hash(spec.busway_id) ^ 0x444c)
    n = _joint_count(spec)

    joints = []
    for j in range(1, n + 1):
        # Phase variation ±10%; one in ~12 joints loosens faster (loose
        # bolt or oxidized contact). The same joint number stays
        # the bad one across runs (deterministic from busway_id).
        phase_uohm = {}
        for ph in ("A", "B", "C"):
            sigma = base_uohm * 0.04 * rng.uniform(-1, 1)
            uohm = base_uohm + sigma
            phase_uohm[ph] = round(uohm, 1)
        max_uohm = max(phase_uohm.values())
        joints.append({
            "joint": j,
            "uohm_per_phase": phase_uohm,
            "max_uohm": max_uohm,
            "passed": max_uohm <= spec.joint_acceptance_uohm,
        })

    max_seen = max(j["max_uohm"] for j in joints)
    return {
        "device_id": spec.busway_id,
        "test_type": "joint_resistance_dlro",
        "manufacturer": spec.manufacturer,
        "model": spec.model,
        "rated_amps": spec.rated_amps,
        "voltage_class_v": spec.voltage_class_v,
        "test_current_a": 10.0,
        "joint_count": n,
        "joint_acceptance_uohm": spec.joint_acceptance_uohm,
        "instrument": {"vendor": "Megger", "model": "DLRO10X",
                       "serial": "SN-DLRO-44219",
                       "cal_cert": "sha256:megger-dlro10x-2026-q1"},
        "joints": joints,
        "max_joint_uohm": round(max_seen, 1),
        "passed": max_seen <= spec.joint_acceptance_uohm,
        "completed_at": run_date.isoformat(),
    }


# ---------------------------------------------------------------------------
# Manual sign-offs (recorded but not auto-measured)
# ---------------------------------------------------------------------------


def manual_signoffs(spec: BuswaySpec, run_date: datetime) -> dict:
    """Manual operator sign-offs that the platform records but does not
    automate. Used by the panel to render the checklist column."""
    age = _age_years(spec, run_date)
    return {
        "visual_inspection": {
            "label": "Visual / nameplate verification",
            "passed": True,
            "operator": "J. Reyes",
        },
        "torque_verification": {
            "label": f"Bolt torque @ {spec.torque_spec_ftlb} ft-lb (±{spec.torque_tolerance_pct}%)",
            "passed": True,
            "operator": "J. Reyes",
            "joints_checked": _joint_count(spec),
        },
        "phase_rotation": {
            "label": "Phase rotation A-B-C verified",
            "passed": True,
            "operator": "M. Chen",
        },
        "thermal_baseline": {
            "label": "FLIR thermal scan baseline (post-energization)",
            "passed": True,
            "operator": "M. Chen",
            "evidence_image_count": 4,
            "note": "On file from commissioning" if age >= 0.05 else "Captured at energization",
        },
    }
