"""UPS battery transfer test record from a single spec + run date.

Cell-level impedance grows with calendar age plus per-cell variation.
Weak-cell IDs are stable across runs (a specific cell that is failing
doesn't randomly switch identity between commissioning visits). The
transfer-waveform voltage dip and switchover time are functions of the
battery's overall health, so all three displays agree.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class UPSSpec:
    ups_id: str
    rated_kw: float = 750.0
    cells_count: int = 240
    cell_nominal_v: float = 3.65
    cell_weak_threshold_v: float = 3.55
    install_year: int = 2022
    install_month: int = 1
    # Per-cell deterministic IDs that are degrading. In production this
    # comes from prior IR-test history per cell; for the demo it's a
    # stable hash-derived subset.
    weak_cell_ids: list[int] = field(default_factory=list)
    manufacturer: str = "APC"
    model: str = "Symmetra MW"


def _battery_age_years(spec: UPSSpec, run_date: datetime) -> float:
    install = datetime(spec.install_year, spec.install_month, 1)
    return max(0.0, (run_date - install).days / 365.25)


def _resolve_weak_cells(spec: UPSSpec) -> list[int]:
    """Stable per-UPS weak-cell IDs. Same UPS → same cells across runs."""
    if spec.weak_cell_ids:
        return sorted(spec.weak_cell_ids)
    rng = random.Random(hash(spec.ups_id))
    count = 3
    return sorted(rng.sample(range(1, spec.cells_count + 1), count))


def ups_run_record(spec: UPSSpec, run_date: datetime) -> dict:
    age_years = _battery_age_years(spec, run_date)
    weak_ids = set(_resolve_weak_cells(spec))
    rng = random.Random(int(run_date.timestamp()) ^ hash(spec.ups_id))

    # Per-cell voltages. Healthy cells drift down ~0.005 V/year; weak
    # cells drift faster.
    cells = []
    for i in range(1, spec.cells_count + 1):
        if i in weak_ids:
            base = spec.cell_weak_threshold_v - 0.05 - 0.015 * age_years
        else:
            base = spec.cell_nominal_v - 0.005 * age_years
        v = base + rng.uniform(-0.015, 0.015)
        cells.append({"id": i, "voltage": round(v, 3),
                      "ok": v >= spec.cell_weak_threshold_v})

    weak_cells_now = [c for c in cells if not c["ok"]]

    # Transfer waveform: deeper dip + slower recovery as battery ages.
    # IEEE 1184 acceptance: min input ≥ 460V during transfer (95% of 480V).
    # New battery: dip ~14 V → min ≈ 466 V, switchover ≈ 8.0 ms
    # 4-yr battery: dip ~18 V → min ≈ 462 V, switchover ≈ 8.7 ms
    nominal_v = 480.0
    dip_v = 14.0 + 1.0 * age_years
    switchover_ms = 8.0 + 0.18 * age_years
    waveform = []
    for i in range(600):
        ms = i * 100 / 1000.0  # 0..60s in 0.1s steps; expressed as ms
        ms_real = i * 100
        if ms_real < 5:
            v = nominal_v + rng.uniform(-0.5, 0.5)
        elif ms_real < switchover_ms * 1000:
            v = nominal_v - dip_v + rng.uniform(-1, 1)
        elif ms_real < 50:
            v = (nominal_v - dip_v) + dip_v * (1 - math.exp(-(ms_real - switchover_ms * 1000) / 12))
        else:
            v = (nominal_v - 5) + math.sin(ms_real / 1500) * 1.5 + rng.uniform(-0.2, 0.2)
        waveform.append({
            "t_ms": ms_real,
            "output_voltage": round(v, 2),
            "battery_pct": round(96 - ms_real / 60_000 * 4, 2),
            "load_pct": round(62 + math.sin(ms_real / 2000) * 4, 1),
        })

    min_v = min(p["output_voltage"] for p in waveform)
    runtime_min = max(0.0, 22.0 - 1.5 * age_years - 0.4 * len(weak_cells_now))
    battery_pct = max(0.0, 100.0 - 1.5 * age_years)

    return {
        "device_id": spec.ups_id,
        "test_type": "battery_transfer",
        "rated_kw": spec.rated_kw,
        "manufacturer": spec.manufacturer,
        "model": spec.model,
        "transfer_waveform_60s": waveform,
        "cells_count": spec.cells_count,
        "all_cells": cells,
        "weak_cells": weak_cells_now,
        "min_voltage_v": round(min_v, 1),
        "switchover_ms": round(switchover_ms, 1),
        "runtime_min": round(runtime_min, 1),
        "battery_pct": round(battery_pct, 1),
        "passed": min_v >= 460.0 and len(weak_cells_now) <= 5,
        "completed_at": run_date.isoformat(),
    }
