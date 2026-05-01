"""DC hipot test record produced from a single cable spec + age.

One cable, one ageing model. Every run a cable produces — current and
historical — comes from this function. The current peak leakage and the
historical peaks are then guaranteed to be on the same curve, because
they're evaluated at different points on it.

References:
    IEEE 400-2012 — Guide for Field Testing and Evaluation of the
    Insulation of Shielded Power Cable Systems Rated 5 kV and Above.
    IEEE 400.2-2013 — DC Withstand testing methodology, trip thresholds.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime


@dataclass
class CableSpec:
    """Per-cable configuration. In production this comes from the cable
    schedule (BIM/IFC import or NetBox cable model). In demo it is
    constructed in dev_server.py per cable_id."""
    cable_id: str
    rated_kv: float                  # nameplate phase-to-phase voltage
    test_factor: float = 0.8         # IEEE 400.2 — DC at 80% of rated AC
    initial_megohm: float = 35_000.0 # healthy IR for a new MV XLPE cable
    aging_per_year: float = 0.045    # fractional IR loss per year
    install_year: int = 2020
    instrument_vendor: str = "Vitrek"
    instrument_model: str = "95X"
    instrument_serial: str = "SN-12345"
    instrument_cal_cert: str = "sha256:vitrek-2026-q2"
    leakage_trip_ma: float = 0.5     # IEEE 400.2 trip — instrument-set


def insulation_megohm_at(spec: CableSpec, run_date: datetime) -> float:
    """Deterministic IR estimate at `run_date`. Single source of truth."""
    years = (run_date - datetime(spec.install_year, 1, 1)).days / 365.25
    years = max(0.0, years)
    return spec.initial_megohm * (1.0 - spec.aging_per_year) ** years


def hipot_run_record(spec: CableSpec, run_date: datetime) -> dict:
    """Build a hipot test record for `spec` evaluated at `run_date`.

    Same-cable runs at different dates evaluate the same model at
    different ages; their leakage values therefore lie on a continuous
    degradation curve by construction."""
    target_kv = spec.rated_kv * spec.test_factor
    R_megohm = insulation_megohm_at(spec, run_date)
    rng = random.Random(int(run_date.timestamp()) ^ hash(spec.cable_id))

    ramp_s, hold_s = 60, 600
    trace = []
    for t in range(ramp_s + hold_s + 1):
        v = target_kv * (t / ramp_s) if t < ramp_s else target_kv
        # Ohm's-law leakage with µA-scale instrument noise.
        leak = (v / R_megohm) + rng.uniform(-0.0002, 0.0002)
        # Small thermal-creep increase during the hold (real cables show
        # this as the dielectric warms up under sustained DC stress).
        if t > ramp_s:
            base_at_v = target_kv / R_megohm
            leak += (t - ramp_s) / hold_s * base_at_v * 0.06
        trace.append({
            "t_seconds": t,
            "voltage_kv": round(v, 3),
            "leakage_ma": round(leak, 5),
            "phase": "DC+",
        })

    peak_leak = max(p["leakage_ma"] for p in trace)
    passed = peak_leak < spec.leakage_trip_ma

    return {
        "device_id": spec.cable_id,
        "test_type": "DC Hipot",
        "rated_kv": spec.rated_kv,
        "target_kv": round(target_kv, 3),
        "test_factor": spec.test_factor,
        "ramp_seconds": ramp_s,
        "hold_seconds": hold_s,
        "leakage_trip_threshold_ma": spec.leakage_trip_ma,
        "instrument": {
            "vendor": spec.instrument_vendor,
            "model": spec.instrument_model,
            "serial": spec.instrument_serial,
            "cal_cert": spec.instrument_cal_cert,
        },
        "trace": trace,
        "peak_leakage_ma": round(peak_leak, 5),
        "insulation_megohm": round(R_megohm, 1),
        "passed": passed,
        "fail_reason": None if passed else "leakage_exceeds_trip",
        "completed_at": run_date.isoformat(),
    }
