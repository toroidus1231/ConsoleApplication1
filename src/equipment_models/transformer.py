"""Transformer commissioning record from a single spec + run date.

Gas concentrations evolve based on (a) calendar age — slow background
CO/CO2 from cellulose breakdown — and (b) the transformer's `fault_state`
which selects which fault gases are climbing. C2H2 is only produced by
arcing; H2 dominates partial-discharge; CH4 and C2H4 dominate thermal.

TTR / polarization-index / hipot history all derive from the same spec
so the panel is internally consistent across all four sub-tests.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

FaultState = Literal["healthy", "active_arcing", "partial_discharge", "overheating"]


@dataclass
class TransformerSpec:
    xfmr_id: str
    rated_kva: int = 2500
    primary_kv: float = 13.8
    secondary_v: float = 480.0
    vector_group: str = "Dyn1"
    install_year: int = 2018
    fault_state: FaultState = "healthy"
    fault_severity: float = 1.0   # 0..2 multiplier on fault gas levels
    # When the fault started, in absolute date. None = active since
    # install. For "emerging" faults set this within the past 6 months
    # so the DGA trend ramps up sharply on the panel.
    fault_onset: datetime | None = None
    initial_pi_megohm: float = 1100.0


def _age_years(spec: TransformerSpec, run_date: datetime) -> float:
    return max(0.0, (run_date - datetime(spec.install_year, 1, 1)).days / 365.25)


def _fault_progress(spec: TransformerSpec, run_date: datetime) -> float:
    """0..1 — how far along the fault has progressed at run_date.
    Saturates at 1 after ~120 days from onset."""
    if spec.fault_state == "healthy":
        return 0.0
    onset = spec.fault_onset or datetime(spec.install_year, 1, 1)
    days_since = (run_date - onset).days
    if days_since <= 0:
        return 0.0
    return min(1.0, days_since / 120.0)


def _gas_levels(spec: TransformerSpec, run_date: datetime) -> dict:
    """C2H2 / H2 / CH4 / C2H4 / C2H6 / CO / CO2 at run_date.

    Healthy → all gases low background. Fault state lifts the relevant
    gases proportionally to severity, age, and how far along the fault
    progression curve we are at this run_date."""
    age = _age_years(spec, run_date)
    s = spec.fault_severity
    p = _fault_progress(spec, run_date)

    # Background levels (cellulose ageing)
    h2  = 18.0 + 0.7 * age
    ch4 = 6.0  + 0.3 * age
    c2h6 = 3.0 + 0.2 * age
    c2h4 = 0.4 + 0.05 * age
    c2h2 = 0.05
    co  = 180.0 + 8.0 * age
    co2 = 1700.0 + 60.0 * age

    if spec.fault_state == "active_arcing":
        # IEC 60599 / Duval: arcing produces C2H2, H2, C2H4
        c2h2 += (3.0 * s + 0.04 * age) * p
        h2   += 30.0 * s * p
        c2h4 += 1.4 * s * p
    elif spec.fault_state == "partial_discharge":
        h2   += 60.0 * s * p
        ch4  += 5.0 * s * p
    elif spec.fault_state == "overheating":
        ch4  += (10.0 * s + 0.3 * age) * p
        c2h4 += 5.0 * s * p
        c2h6 += 3.0 * s * p
        co   += 90.0 * s * p

    return {
        "h2": h2, "ch4": ch4, "c2h6": c2h6, "c2h4": c2h4,
        "c2h2": c2h2, "co": co, "co2": co2,
    }


def _dga_history(spec: TransformerSpec, run_date: datetime, weeks: int = 14) -> list[dict]:
    """Weekly samples back from run_date. Gas levels increase smoothly to
    today's value so the trend chart is monotonic and reflects fault
    progression (or lack thereof)."""
    today_gases = _gas_levels(spec, run_date)
    fault_active = spec.fault_state != "healthy"

    history = []
    rng = random.Random(int(run_date.timestamp()) ^ hash(spec.xfmr_id))
    # 3 months ago: fault gases at ~15% of today; healthy gases stable.
    fault_gas_keys = {
        "active_arcing": ("c2h2", "h2", "c2h4"),
        "partial_discharge": ("h2", "ch4"),
        "overheating": ("ch4", "c2h4", "c2h6", "co"),
        "healthy": (),
    }[spec.fault_state]

    for w in range(weeks - 1, -1, -1):
        days_ago = w * 7
        frac = (weeks - 1 - w) / max(1, weeks - 1)  # 0 oldest → 1 newest
        sample = {}
        for gas, today_val in today_gases.items():
            if gas in fault_gas_keys and fault_active:
                start = today_val * 0.15
                v = start + (today_val - start) * frac
            else:
                v = today_val * (0.92 + 0.16 * frac)  # tiny background drift
            v += rng.uniform(-0.05, 0.05) if gas in ("c2h2", "c2h4") else rng.uniform(-1, 1)
            sample[gas] = round(max(0.0, v), 2)
        sample["days_ago"] = days_ago
        history.append(sample)
    return history


def _ttr_table(spec: TransformerSpec, run_date: datetime) -> list[dict]:
    rng = random.Random(int(run_date.timestamp()) ^ hash(spec.xfmr_id) ^ 0x717272)
    nominal = (spec.primary_kv * 1000) / spec.secondary_v
    out = []
    for tap in (-2, -1, 0, 1, 2):
        for phase in ("A", "B", "C"):
            expected = nominal * (1 + tap * 0.025)
            measured = expected + rng.uniform(-0.04, 0.04)
            err = (measured - expected) / expected * 100
            out.append({
                "tap": tap, "phase": phase,
                "expected": round(expected, 4),
                "measured": round(measured, 4),
                "deviation_pct": round(err, 3),
                "passed": abs(err) <= 0.5,
            })
    return out


def _polarization_index(spec: TransformerSpec, run_date: datetime) -> dict:
    """PI = R(10min) / R(1min). IEEE 43 acceptance ≥ 2.0 for class-A
    insulation. Healthy oil-paper transformer = 2.5–4.0; PI < 2.0 is
    flagged. Active arcing degrades PI rapidly via fault progression."""
    age = _age_years(spec, run_date)
    p = _fault_progress(spec, run_date)

    # Target PI: healthy ~2.6 with slow age-related drop; faults pull
    # it down sharply once they progress.
    target_pi = 2.6 - 0.04 * age
    if spec.fault_state == "active_arcing":
        target_pi -= 0.9 * p
    elif spec.fault_state == "partial_discharge":
        target_pi -= 0.7 * p
    elif spec.fault_state == "overheating":
        target_pi -= 0.4 * p
    target_pi = max(1.0, target_pi)

    # Solve exponential charging k so r10/r1 == target_pi exactly:
    #   r(t) = r0 * (1 + (k-1) * (1 - exp(-t/τ)))
    #   r10/r1 = (1 + (k-1)·B) / (1 + (k-1)·A)   with τ=3.5min
    TAU = 3.5
    A = 1 - math.exp(-1 / TAU)
    B = 1 - math.exp(-10 / TAU)
    denom = B - target_pi * A
    k_minus_1 = (target_pi - 1) / denom if abs(denom) > 1e-6 else 1.0

    r0 = spec.initial_pi_megohm * (1 - 0.02 * age)  # absolute IR drop
    curve = []
    for t in range(0, 11):
        r = r0 * (1 + k_minus_1 * (1 - math.exp(-t / TAU)))
        curve.append({"minute": t, "resistance_mohm": round(r, 1)})

    pi_value = curve[-1]["resistance_mohm"] / curve[1]["resistance_mohm"]
    return {
        "value": round(pi_value, 2),
        "curve": curve,
        "passed": pi_value >= 2.0,
    }


def _hipot_history(spec: TransformerSpec, run_date: datetime) -> list[dict]:
    """Transformer-bushing hipot at 80% rated. Leakage grows slowly; a
    fault state lifts it more aggressively."""
    age = _age_years(spec, run_date)
    base = 0.10 + 0.012 * age
    fault_lift = {"healthy": 0.0, "partial_discharge": 0.05,
                  "active_arcing": 0.08, "overheating": 0.04}[spec.fault_state]
    out = []
    for i, days in enumerate((-90, -60, -30, 0)):
        leak = base + i * 0.005 + fault_lift * (i / 3)
        out.append({
            "date_offset_days": days,
            "voltage_kv": round(spec.primary_kv * 0.8, 2),
            "leakage_ma": round(leak, 3),
            "duration_seconds": 60,
            "passed": leak < 0.5,
        })
    return out


def transformer_run_record(spec: TransformerSpec, run_date: datetime) -> dict:
    today_gases = _gas_levels(spec, run_date)
    return {
        "device_id": spec.xfmr_id,
        "test_type": "commissioning_pack",
        "rated_kva": spec.rated_kva,
        "voltage_class": f"{spec.primary_kv} kV / {int(spec.secondary_v)} V",
        "vector_group": spec.vector_group,
        "fault_state": spec.fault_state,
        "current_gases": {k: round(v, 2) for k, v in today_gases.items()},
        "dga_history": _dga_history(spec, run_date),
        "ttr": _ttr_table(spec, run_date),
        "polarization_index": _polarization_index(spec, run_date),
        "hipot_history": _hipot_history(spec, run_date),
        "passed": spec.fault_state == "healthy",
        "completed_at": run_date.isoformat(),
    }
