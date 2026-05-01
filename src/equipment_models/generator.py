"""Generator load-bank + black-start record from a single spec + run date.

All four 25/50/75/100% load-bank steps actuals derive from the same
rated_kw and a per-generator condition factor. Older generators or
those with more operating hours show: slightly lower kW achieved at
each step, slightly higher oil/coolant temps, slower black-start
ignition. Per-step PASS/FAIL falls out of these.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime


@dataclass
class GeneratorSpec:
    gen_id: str
    rated_kw: int = 1500
    manufacturer: str = "Caterpillar"
    model: str = "3516B"
    install_year: int = 2020
    operating_hours_at_install: int = 0
    annual_runtime_hours: int = 80   # typical standby gen ~80h/yr
    voltage_v: float = 480.0
    nominal_freq_hz: float = 60.0
    nominal_rpm: int = 1800


def _age_years(spec: GeneratorSpec, run_date: datetime) -> float:
    return max(0.0, (run_date - datetime(spec.install_year, 1, 1)).days / 365.25)


def _operating_hours(spec: GeneratorSpec, run_date: datetime) -> float:
    return spec.operating_hours_at_install + spec.annual_runtime_hours * _age_years(spec, run_date)


def _condition(spec: GeneratorSpec, run_date: datetime) -> float:
    """Fraction in [0,1]. 1.0 = brand new, 0.85 = 10k hours of standby use.
    Drives kW droop, temp lift, ramp-time deltas."""
    hours = _operating_hours(spec, run_date)
    return max(0.7, 1.0 - hours / 80_000.0)


def _loadbank_trace(spec: GeneratorSpec, run_date: datetime) -> list[dict]:
    """4-step ramp 25/50/75/100% rated. One sample / 30 s for an hour
    per step (4h total)."""
    cond = _condition(spec, run_date)
    rng = random.Random(int(run_date.timestamp()) ^ hash(spec.gen_id))
    trace = []
    rpm = 0
    t = 0.0
    for target in (0.25, 0.50, 0.75, 1.00):
        for s in range(60 * 60):
            if rpm < spec.nominal_rpm:
                rpm = min(spec.nominal_rpm, rpm + 600)
            kw_target = spec.rated_kw * target * cond
            kw = kw_target * (1 + math.sin(t / 11) * 0.012) + rng.uniform(-3, 3)
            oil_t = 92 + (target * 16) + (1.0 - cond) * 6 + math.sin(t / 19) * 1.5
            cool_t = 78 + (target * 12) + (1.0 - cond) * 4 + math.sin(t / 23) * 1.0
            v = spec.voltage_v + math.sin(t / 13) * 1.5 + rng.uniform(-0.5, 0.5)
            f = spec.nominal_freq_hz + math.sin(t / 17) * 0.04
            if s % 30 == 0:
                trace.append({
                    "t_seconds": round(t, 0),
                    "kw": round(kw, 0),
                    "voltage_v": round(v, 1),
                    "freq_hz": round(f, 3),
                    "rpm": rpm,
                    "oil_temp_c": round(oil_t, 1),
                    "coolant_temp_c": round(cool_t, 1),
                })
            t += 1.0
    return trace


def _startup_sequence(spec: GeneratorSpec, run_date: datetime) -> list[dict]:
    """Black-start step timings widen with operating hours."""
    cond = _condition(spec, run_date)
    drift = (1.0 - cond) * 1.5  # seconds of extra time per step

    return [
        {"step": "Pre-lube oil pump",
         "expected": "≥20 psi within 5 s",
         "actual": f"{round(23.0 - drift, 1)} psi @ {round(4.2 + drift * 0.4, 1)} s",
         "passed": (4.2 + drift * 0.4) <= 5.0},
        {"step": "Crank engagement",
         "expected": "≤300 ms",
         "actual": f"{int(240 + drift * 8)} ms",
         "passed": (240 + drift * 8) <= 300},
        {"step": "Ignition",
         "expected": "Combustion within 8 s of crank",
         "actual": f"{round(5.4 + drift * 0.5, 1)} s",
         "passed": (5.4 + drift * 0.5) <= 8.0},
        {"step": "Ramp to rated RPM",
         "expected": "1800 RPM within 12 s",
         "actual": f"{round(10.1 + drift * 0.4, 1)} s",
         "passed": (10.1 + drift * 0.4) <= 12.0},
        {"step": "AVR / governor settle",
         "expected": "V ±2%, f ±0.2 Hz, 5 s",
         "actual": f"stable @ {round(6.8 + drift * 0.3, 1)} s",
         "passed": True},
        {"step": "Ready signal",
         "expected": "Asserted within 15 s of crank",
         "actual": f"{round(12.4 + drift * 0.4, 1)} s",
         "passed": (12.4 + drift * 0.4) <= 15.0},
        {"step": "Close to bus",
         "expected": "On 'transfer requested'",
         "actual": "manual hold (commissioning mode)",
         "passed": True},
        {"step": "Cooldown after stop",
         "expected": "5 min idle",
         "actual": "—",
         "passed": None},
    ]


def generator_run_record(spec: GeneratorSpec, run_date: datetime) -> dict:
    startup = _startup_sequence(spec, run_date)
    return {
        "device_id": spec.gen_id,
        "test_type": "loadbank_4h",
        "rated_kw": spec.rated_kw,
        "manufacturer": spec.manufacturer,
        "model": spec.model,
        "operating_hours": round(_operating_hours(spec, run_date), 1),
        "loadbank_trace": _loadbank_trace(spec, run_date),
        "startup_sequence": startup,
        "passed_overall": all(s["passed"] is not False for s in startup),
        "completed_at": run_date.isoformat(),
    }


def gen_build_time_seconds(spec: GeneratorSpec, run_date: datetime) -> float:
    """Cross-reference for ATSSpec: how long the gen takes from utility-loss
    command to ≥ rated voltage. Scales with operating hours."""
    cond = _condition(spec, run_date)
    return round(7.5 + (1.0 - cond) * 4.0, 2)


def gen_voltage_at_build(spec: GeneratorSpec, run_date: datetime) -> int:
    """Steady-state V the gen settles to. ~488V brand new, ~482V worn."""
    cond = _condition(spec, run_date)
    return int(spec.voltage_v + 8 * cond)
