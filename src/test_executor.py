"""Generic test executor.

Takes (Config Context, run_date, optional cross-references) and emits
a TestResult-shaped dict per active_test. One module — one dispatch
table on test["type"]. Adding a new product line requires only a JSON
Config Context drop. Adding a new TEST TYPE requires adding a handler
here. Adding a new EQUIPMENT FAMILY requires a new ageing-model kind
plus, separately, a category panel in the UI layer.

Test-type handlers ported from the (now-deleted) src/equipment_models/
classes. Same physics, same NETA/IEEE/IEC references; the only change
is the data flows in via dict instead of @dataclass.
"""

from __future__ import annotations

import math
import random
from datetime import datetime
from typing import Any


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def execute_test(
    *,
    device_id: str,
    config: dict,
    test_def: dict,
    run_date: datetime,
    cross_refs: dict[str, Any] | None = None,
) -> dict:
    """Run one test definition against a device's Config Context at
    `run_date` and return a TestResult-shaped dict.

    `cross_refs` is for tests that depend on other devices (e.g., an ATS
    transfer-test pulls gen build time from the upstream generator's
    config). Keys: "upstream_gen", "downstream_ups" (list).
    """
    test_type = test_def["type"]
    handler = _DISPATCH.get(test_type)
    if handler is None:
        raise KeyError(f"no executor for test type: {test_type!r}")
    return handler(device_id, config, test_def, run_date, cross_refs or {})


# ---------------------------------------------------------------------------
# Helpers shared across handlers
# ---------------------------------------------------------------------------


def _age_years(config: dict, run_date: datetime) -> float:
    p = config.get("ageing_model", {}).get("params", {})
    year = p.get("install_year", 2022)
    month = p.get("install_month", 1)
    return max(0.0, (run_date - datetime(year, month, 1)).days / 365.25)


def _seed(device_id: str, run_date: datetime, salt: int = 0) -> random.Random:
    return random.Random(int(run_date.timestamp()) ^ hash(device_id) ^ salt)


def _insulation_megohm(config: dict, run_date: datetime) -> float:
    p = config["ageing_model"]["params"]
    R0 = p.get("initial_megohm", p.get("initial_megohm_default", 1000.0))
    rate = p.get("aging_per_year", p.get("aging_per_year_default", 0.025))
    return R0 * (1.0 - rate) ** _age_years(config, run_date)


def _instrument_block(test_def: dict) -> dict:
    return dict(test_def.get("instrument", {}))


# ---------------------------------------------------------------------------
# Test type: dc_withstand (cable hipot, transformer hipot, busway hipot)
# ---------------------------------------------------------------------------


def _dc_withstand(device_id, config, test_def, run_date, cross):
    params = test_def["parameters"]
    accept = test_def["acceptance"]
    test_kind = params.get("test_kind", "fixed_kv")

    if test_kind == "fixed_kv":
        target_kv = params["target_kv"]
    else:  # ieee_400_maintenance — 0.8 × rated AC
        rated_kv = config["ratings"]["rated_kv"]
        target_kv = rated_kv * params.get("test_factor", 0.8)

    R = _insulation_megohm(config, run_date)
    rng = _seed(device_id, run_date, 0x4849)
    ramp_s = int(params.get("ramp_seconds", 60))
    hold_s = int(params.get("hold_seconds", 600))

    trace = []
    for t in range(ramp_s + hold_s + 1):
        v = target_kv * (t / ramp_s) if t < ramp_s else target_kv
        leak = (v / R) + rng.uniform(-0.0002, 0.0002)
        if t > ramp_s:
            leak += (t - ramp_s) / hold_s * (target_kv / R) * 0.06
        trace.append({"t_seconds": t,
                      "voltage_kv": round(v, 3),
                      "leakage_ma": round(leak, 5),
                      "phase": "DC+"})

    peak = max(p["leakage_ma"] for p in trace)
    return {
        "device_id": device_id,
        "test_name": test_def["name"],
        "test_type": "DC Hipot" if config["category"] == "cable" else "DC withstand",
        "spec_reference": test_def.get("spec_reference"),
        "manufacturer": config.get("manufacturer"),
        "model": config.get("model"),
        "rated_amps": config.get("ratings", {}).get("rated_amps"),
        "voltage_class_v": config.get("ratings", {}).get("voltage_class_v"),
        "rated_kv": config.get("ratings", {}).get("rated_kv"),
        "target_kv": round(target_kv, 3),
        "ramp_seconds": ramp_s,
        "hold_seconds": hold_s,
        "leakage_trip_threshold_ma": accept.get("max_leakage_ma", 1.0),
        "leakage_trip_ma": accept.get("max_leakage_ma", 1.0),
        "instrument": _instrument_block(test_def),
        "trace": trace,
        "peak_leakage_ma": round(peak, 5),
        "insulation_megohm": round(R, 1),
        "passed": peak < accept.get("max_leakage_ma", 1.0),
        "completed_at": run_date.isoformat(),
    }


# ---------------------------------------------------------------------------
# Test type: insulation_resistance (megger)
# ---------------------------------------------------------------------------


def _insulation_resistance(device_id, config, test_def, run_date, cross):
    params = test_def["parameters"]
    accept = test_def["acceptance"]
    R = _insulation_megohm(config, run_date)
    rng = _seed(device_id, run_date, 0x4d65)
    pairs = params.get("phase_pairs", [["A", "B"], ["B", "C"], ["A", "C"],
                                       ["A", "G"], ["B", "G"], ["C", "G"]])
    readings = []
    for pair in pairs:
        a, b = pair[0], pair[1]
        bias = 0.85 if "G" in (a, b) else 1.0
        meg = R * bias * (1 + rng.uniform(-0.04, 0.04))
        readings.append({
            "from": a, "to": b,
            "megohm": round(meg, 1),
            "passed": meg >= accept["min_megohm"],
        })
    min_meg = min(r["megohm"] for r in readings)
    return {
        "device_id": device_id,
        "test_name": test_def["name"],
        "test_type": "insulation_resistance",
        "spec_reference": test_def.get("spec_reference"),
        "manufacturer": config.get("manufacturer"),
        "model": config.get("model"),
        "rated_amps": config.get("ratings", {}).get("rated_amps"),
        "voltage_class_v": config.get("ratings", {}).get("voltage_class_v"),
        "test_voltage_v": params["test_voltage_v"],
        "applied_seconds": params.get("applied_seconds", 60),
        "acceptance_megohm": accept["min_megohm"],
        "instrument": _instrument_block(test_def),
        "readings": readings,
        "min_megohm": round(min_meg, 1),
        "passed": min_meg >= accept["min_megohm"],
        "completed_at": run_date.isoformat(),
    }


# ---------------------------------------------------------------------------
# Test type: joint_resistance_dlro
# ---------------------------------------------------------------------------


def _joint_resistance_dlro(device_id, config, test_def, run_date, cross):
    params = test_def["parameters"]
    accept = test_def["acceptance"]
    p = config["ageing_model"]["params"]
    rng = _seed(device_id, run_date, 0x444c)

    age = _age_years(config, run_date)
    base_uohm = p["initial_joint_uohm"] * (1 + p["joint_aging_per_year"]) ** age
    length_m = config["ratings"].get("length_m", config["ratings"].get("length_m_default", 30.0))
    n = max(2, int(length_m * params.get("joints_per_meter", 0.33)) + params.get("end_terminations", 2))
    phases = params.get("phases", ["A", "B", "C"])

    joints = []
    for j in range(1, n + 1):
        per_phase = {}
        for ph in phases:
            sigma = base_uohm * 0.04 * rng.uniform(-1, 1)
            per_phase[ph] = round(base_uohm + sigma, 1)
        max_u = max(per_phase.values())
        joints.append({
            "joint": j,
            "uohm_per_phase": per_phase,
            "max_uohm": max_u,
            "passed": max_u <= accept["max_uohm_per_joint"],
        })
    max_seen = max(j["max_uohm"] for j in joints)
    return {
        "device_id": device_id,
        "test_name": test_def["name"],
        "test_type": "joint_resistance_dlro",
        "spec_reference": test_def.get("spec_reference"),
        "manufacturer": config.get("manufacturer"),
        "model": config.get("model"),
        "rated_amps": config.get("ratings", {}).get("rated_amps"),
        "voltage_class_v": config.get("ratings", {}).get("voltage_class_v"),
        "test_current_a": params["test_current_a"],
        "joint_count": n,
        "joint_acceptance_uohm": accept["max_uohm_per_joint"],
        "instrument": _instrument_block(test_def),
        "joints": joints,
        "max_joint_uohm": round(max_seen, 1),
        "passed": max_seen <= accept["max_uohm_per_joint"],
        "completed_at": run_date.isoformat(),
    }


# ---------------------------------------------------------------------------
# Test type: dissolved_gas_analysis
# ---------------------------------------------------------------------------


def _fault_progress(config: dict, run_date: datetime) -> float:
    p = config["ageing_model"]["params"]
    state = p.get("fault_state", "healthy")
    if state == "healthy":
        return 0.0
    onset_str = p.get("fault_onset")
    if onset_str:
        if isinstance(onset_str, str):
            onset = datetime.fromisoformat(onset_str)
        else:
            onset = onset_str
    else:
        onset = datetime(p.get("install_year", 2018), 1, 1)
    days = (run_date - onset).days
    if days <= 0:
        return 0.0
    return min(1.0, days / 120.0)


def _gas_levels(config: dict, run_date: datetime) -> dict:
    p = config["ageing_model"]["params"]
    age = _age_years(config, run_date)
    sev = p.get("fault_severity", 1.0)
    prog = _fault_progress(config, run_date)
    state = p.get("fault_state", "healthy")

    h2  = 18.0 + 0.7 * age
    ch4 = 6.0  + 0.3 * age
    c2h6 = 3.0 + 0.2 * age
    c2h4 = 0.4 + 0.05 * age
    c2h2 = 0.05
    co  = 180.0 + 8.0 * age
    co2 = 1700.0 + 60.0 * age

    if state == "active_arcing":
        c2h2 += (3.0 * sev + 0.04 * age) * prog
        h2   += 30.0 * sev * prog
        c2h4 += 1.4 * sev * prog
    elif state == "partial_discharge":
        h2   += 60.0 * sev * prog
        ch4  += 5.0 * sev * prog
    elif state == "overheating":
        ch4  += (10.0 * sev + 0.3 * age) * prog
        c2h4 += 5.0 * sev * prog
        c2h6 += 3.0 * sev * prog
        co   += 90.0 * sev * prog
    return {"h2": h2, "ch4": ch4, "c2h6": c2h6, "c2h4": c2h4,
            "c2h2": c2h2, "co": co, "co2": co2}


def _dga_history(config, run_date, weeks=14):
    today = _gas_levels(config, run_date)
    state = config["ageing_model"]["params"].get("fault_state", "healthy")
    fault_keys = {
        "active_arcing": ("c2h2", "h2", "c2h4"),
        "partial_discharge": ("h2", "ch4"),
        "overheating": ("ch4", "c2h4", "c2h6", "co"),
        "healthy": (),
    }[state]
    rng = _seed(config["device_type_slug"], run_date, 0xd6a)
    out = []
    for w in range(weeks - 1, -1, -1):
        days_ago = w * 7
        frac = (weeks - 1 - w) / max(1, weeks - 1)
        sample = {"days_ago": days_ago}
        for gas, today_val in today.items():
            if gas in fault_keys and state != "healthy":
                start = today_val * 0.15
                v = start + (today_val - start) * frac
            else:
                v = today_val * (0.92 + 0.16 * frac)
            v += rng.uniform(-0.05, 0.05) if gas in ("c2h2", "c2h4") else rng.uniform(-1, 1)
            sample[gas] = round(max(0.0, v), 2)
        out.append(sample)
    return out


def _dissolved_gas_analysis(device_id, config, test_def, run_date, cross):
    today = _gas_levels(config, run_date)
    state = config["ageing_model"]["params"].get("fault_state", "healthy")
    accept = test_def.get("acceptance", {})
    pass_c2h2 = today["c2h2"] <= accept.get("max_c2h2_ppm", 999)
    return {
        "device_id": device_id,
        "test_name": test_def["name"],
        "test_type": "dissolved_gas_analysis",
        "spec_reference": test_def.get("spec_reference"),
        "manufacturer": config.get("manufacturer"),
        "model": config.get("model"),
        "fault_state": state,
        "current_gases": {k: round(v, 2) for k, v in today.items()},
        "dga_history": _dga_history(config, run_date,
                                    weeks=test_def["parameters"].get("history_weeks", 14)),
        "passed": pass_c2h2 and state == "healthy",
        "instrument": _instrument_block(test_def),
        "completed_at": run_date.isoformat(),
    }


# ---------------------------------------------------------------------------
# Test type: transformer_turns_ratio
# ---------------------------------------------------------------------------


def _transformer_turns_ratio(device_id, config, test_def, run_date, cross):
    rng = _seed(device_id, run_date, 0x717272)
    primary_kv = config["ratings"]["primary_kv"]
    secondary_v = config["ratings"]["secondary_v"]
    nominal = (primary_kv * 1000) / secondary_v
    accept = test_def["acceptance"]
    out = []
    for tap in test_def["parameters"]["taps"]:
        for phase in test_def["parameters"]["phases"]:
            expected = nominal * (1 + tap * 0.025)
            measured = expected + rng.uniform(-0.04, 0.04)
            err = (measured - expected) / expected * 100
            out.append({
                "tap": tap, "phase": phase,
                "expected": round(expected, 4),
                "measured": round(measured, 4),
                "deviation_pct": round(err, 3),
                "passed": abs(err) <= accept["max_deviation_pct"],
            })
    return {
        "device_id": device_id,
        "test_name": test_def["name"],
        "test_type": "transformer_turns_ratio",
        "spec_reference": test_def.get("spec_reference"),
        "ttr": out,
        "passed": all(r["passed"] for r in out),
        "instrument": _instrument_block(test_def),
        "completed_at": run_date.isoformat(),
    }


# ---------------------------------------------------------------------------
# Test type: polarization_index
# ---------------------------------------------------------------------------


def _polarization_index(device_id, config, test_def, run_date, cross):
    p = config["ageing_model"]["params"]
    age = _age_years(config, run_date)
    prog = _fault_progress(config, run_date)
    state = p.get("fault_state", "healthy")
    accept = test_def["acceptance"]

    target_pi = 2.6 - 0.04 * age
    if state == "active_arcing":
        target_pi -= 0.9 * prog
    elif state == "partial_discharge":
        target_pi -= 0.7 * prog
    elif state == "overheating":
        target_pi -= 0.4 * prog
    target_pi = max(1.0, target_pi)

    TAU = 3.5
    A = 1 - math.exp(-1 / TAU)
    B = 1 - math.exp(-10 / TAU)
    denom = B - target_pi * A
    k_minus_1 = (target_pi - 1) / denom if abs(denom) > 1e-6 else 1.0
    R0 = p.get("initial_pi_megohm", 1100.0) * (1 - 0.02 * age)

    curve = []
    for t in range(0, 11):
        r = R0 * (1 + k_minus_1 * (1 - math.exp(-t / TAU)))
        curve.append({"minute": t, "resistance_mohm": round(r, 1)})
    pi_value = curve[-1]["resistance_mohm"] / curve[1]["resistance_mohm"]
    return {
        "device_id": device_id,
        "test_name": test_def["name"],
        "test_type": "polarization_index",
        "spec_reference": test_def.get("spec_reference"),
        "polarization_index": {
            "value": round(pi_value, 2),
            "curve": curve,
            "passed": pi_value >= accept["min_pi"],
        },
        "passed": pi_value >= accept["min_pi"],
        "instrument": _instrument_block(test_def),
        "completed_at": run_date.isoformat(),
    }


# ---------------------------------------------------------------------------
# Test type: transformer_hipot_history
# ---------------------------------------------------------------------------


def _transformer_hipot_history(device_id, config, test_def, run_date, cross):
    p = config["ageing_model"]["params"]
    age = _age_years(config, run_date)
    state = p.get("fault_state", "healthy")
    base = 0.10 + 0.012 * age
    fault_lift = {"healthy": 0.0, "partial_discharge": 0.05,
                  "active_arcing": 0.08, "overheating": 0.04}[state]
    primary_kv = config["ratings"]["primary_kv"]
    out = []
    for i, days in enumerate((-90, -60, -30, 0)):
        leak = base + i * 0.005 + fault_lift * (i / 3)
        out.append({
            "date_offset_days": days,
            "voltage_kv": round(primary_kv * 0.8, 2),
            "leakage_ma": round(leak, 3),
            "duration_seconds": test_def["parameters"].get("duration_seconds", 60),
            "passed": leak < test_def["acceptance"]["max_leakage_ma"],
        })
    return {
        "device_id": device_id,
        "test_name": test_def["name"],
        "test_type": "transformer_hipot_history",
        "hipot_history": out,
        "passed": all(r["passed"] for r in out),
        "instrument": _instrument_block(test_def),
        "completed_at": run_date.isoformat(),
    }


# ---------------------------------------------------------------------------
# Test type: ups_battery_transfer
# ---------------------------------------------------------------------------


def _ups_battery_transfer(device_id, config, test_def, run_date, cross):
    p = config["ageing_model"]["params"]
    rats = config["ratings"]
    accept = test_def["acceptance"]
    rng = _seed(device_id, run_date, 0x7570)

    age = _age_years(config, run_date)
    weak_ids = set(p.get("weak_cell_ids") or [])
    if not weak_ids:
        rng_w = random.Random(hash(device_id))
        weak_ids = set(rng_w.sample(range(1, rats["cells_count"] + 1), 3))

    cells = []
    for i in range(1, rats["cells_count"] + 1):
        if i in weak_ids:
            base = rats["cell_weak_threshold_v"] - 0.05 - p["weak_cell_aging_per_year"] * age
        else:
            base = rats["cell_nominal_v"] - p["cell_aging_per_year"] * age
        v = base + rng.uniform(-0.015, 0.015)
        cells.append({"id": i, "voltage": round(v, 3),
                      "ok": v >= rats["cell_weak_threshold_v"]})
    weak_now = [c for c in cells if not c["ok"]]

    nominal_v = rats.get("nominal_v", 480.0)
    dip_v = p["dip_v_initial"] + p["dip_v_per_year"] * age
    switchover_ms = p["switchover_ms_initial"] + p["switchover_ms_per_year"] * age
    waveform = []
    for i in range(600):
        ms_real = i * 100
        if ms_real < 5:
            v = nominal_v + rng.uniform(-0.5, 0.5)
        elif ms_real < switchover_ms * 1000:
            v = nominal_v - dip_v + rng.uniform(-1, 1)
        elif ms_real < 50:
            v = (nominal_v - dip_v) + dip_v * (1 - math.exp(-(ms_real - switchover_ms * 1000) / 12))
        else:
            v = (nominal_v - 5) + math.sin(ms_real / 1500) * 1.5 + rng.uniform(-0.2, 0.2)
        waveform.append({"t_ms": ms_real,
                         "output_voltage": round(v, 2),
                         "battery_pct": round(96 - ms_real / 60_000 * 4, 2),
                         "load_pct": round(62 + math.sin(ms_real / 2000) * 4, 1)})
    min_v = min(p["output_voltage"] for p in waveform)
    runtime_min = max(0.0, 22.0 - 1.5 * age - 0.4 * len(weak_now))
    battery_pct = max(0.0, 100.0 - 1.5 * age)
    return {
        "device_id": device_id,
        "test_name": test_def["name"],
        "test_type": "battery_transfer",
        "spec_reference": test_def.get("spec_reference"),
        "rated_kw": rats.get("rated_kw"),
        "manufacturer": config.get("manufacturer"),
        "model": config.get("model"),
        "transfer_waveform_60s": waveform,
        "cells_count": rats["cells_count"],
        "all_cells": cells,
        "weak_cells": weak_now,
        "min_voltage_v": round(min_v, 1),
        "switchover_ms": round(switchover_ms, 1),
        "runtime_min": round(runtime_min, 1),
        "battery_pct": round(battery_pct, 1),
        "passed": (min_v >= accept["min_voltage_v"]
                   and switchover_ms <= accept["max_switchover_ms"]
                   and len(weak_now) <= accept["max_weak_cells"]),
        "instrument": _instrument_block(test_def),
        "completed_at": run_date.isoformat(),
    }


# ---------------------------------------------------------------------------
# Test type: generator_loadbank
# ---------------------------------------------------------------------------


def _generator_loadbank(device_id, config, test_def, run_date, cross):
    p = config["ageing_model"]["params"]
    rats = config["ratings"]
    accept = test_def["acceptance"]
    rng = _seed(device_id, run_date, 0x6c62)

    annual = p.get("annual_runtime_hours_default", 80)
    age = _age_years(config, run_date)
    hours = p.get("operating_hours_at_install_default", 0) + annual * age
    cond = max(0.7, 1.0 - hours / 80_000.0)

    rated_kw = rats["rated_kw"]
    voltage_v = rats["voltage_v"]
    rpm_target = rats["nominal_rpm"]
    freq_target = rats["nominal_freq_hz"]

    trace = []
    rpm = 0
    t = 0.0
    for target in test_def["parameters"]["steps"]:
        for s in range(60 * 60):
            if rpm < rpm_target:
                rpm = min(rpm_target, rpm + 600)
            kw_target = rated_kw * target * cond
            kw = kw_target * (1 + math.sin(t / 11) * 0.012) + rng.uniform(-3, 3)
            oil_t = 92 + (target * 16) + (1.0 - cond) * 6 + math.sin(t / 19) * 1.5
            cool_t = 78 + (target * 12) + (1.0 - cond) * 4 + math.sin(t / 23) * 1.0
            v = voltage_v + math.sin(t / 13) * 1.5 + rng.uniform(-0.5, 0.5)
            f = freq_target + math.sin(t / 17) * 0.04
            if s % 30 == 0:
                trace.append({"t_seconds": round(t, 0),
                              "kw": round(kw, 0), "voltage_v": round(v, 1),
                              "freq_hz": round(f, 3), "rpm": rpm,
                              "oil_temp_c": round(oil_t, 1),
                              "coolant_temp_c": round(cool_t, 1)})
            t += 1.0

    drift = (1.0 - cond) * 1.5
    startup = [
        {"step": "Pre-lube oil pump", "expected": "≥20 psi within 5 s",
         "actual": f"{round(23.0 - drift, 1)} psi @ {round(4.2 + drift * 0.4, 1)} s",
         "passed": (4.2 + drift * 0.4) <= 5.0},
        {"step": "Crank engagement", "expected": "≤300 ms",
         "actual": f"{int(240 + drift * 8)} ms",
         "passed": (240 + drift * 8) <= 300},
        {"step": "Ignition", "expected": "Combustion within 8 s of crank",
         "actual": f"{round(5.4 + drift * 0.5, 1)} s",
         "passed": (5.4 + drift * 0.5) <= 8.0},
        {"step": "Ramp to rated RPM", "expected": f"{rpm_target} RPM within 12 s",
         "actual": f"{round(10.1 + drift * 0.4, 1)} s",
         "passed": (10.1 + drift * 0.4) <= 12.0},
        {"step": "AVR / governor settle", "expected": "V ±2%, f ±0.2 Hz, 5 s",
         "actual": f"stable @ {round(6.8 + drift * 0.3, 1)} s", "passed": True},
        {"step": "Ready signal", "expected": "Asserted within 15 s of crank",
         "actual": f"{round(12.4 + drift * 0.4, 1)} s",
         "passed": (12.4 + drift * 0.4) <= accept.get("max_ready_signal_seconds", 15.0)},
        {"step": "Close to bus", "expected": "On 'transfer requested'",
         "actual": "manual hold (commissioning mode)", "passed": True},
        {"step": "Cooldown after stop", "expected": "5 min idle",
         "actual": "—", "passed": None},
    ]
    return {
        "device_id": device_id,
        "test_name": test_def["name"],
        "test_type": "loadbank_4h",
        "spec_reference": test_def.get("spec_reference"),
        "rated_kw": rated_kw,
        "manufacturer": config.get("manufacturer"),
        "model": config.get("model"),
        "operating_hours": round(hours, 1),
        "loadbank_trace": trace,
        "startup_sequence": startup,
        "passed_overall": all(s["passed"] is not False for s in startup),
        "passed": all(s["passed"] is not False for s in startup),
        "instrument": _instrument_block(test_def),
        "completed_at": run_date.isoformat(),
    }


# ---------------------------------------------------------------------------
# Test type: ats_transfer_sequence (cross-references gen + UPS configs)
# ---------------------------------------------------------------------------


def _gen_build_time(gen_config: dict, run_date: datetime) -> tuple[float, int]:
    p = gen_config["ageing_model"]["params"]
    annual = p.get("annual_runtime_hours_default", 80)
    age = _age_years(gen_config, run_date)
    hours = p.get("operating_hours_at_install_default", 0) + annual * age
    cond = max(0.7, 1.0 - hours / 80_000.0)
    return round(7.5 + (1.0 - cond) * 4.0, 2), int(gen_config["ratings"]["voltage_v"] + 8 * cond)


def _ats_transfer_sequence(device_id, config, test_def, run_date, cross):
    gen_cfg = cross.get("upstream_gen")
    ups_cfgs = cross.get("downstream_ups", [])
    n_ups = len(ups_cfgs)
    accept = test_def["acceptance"]

    if gen_cfg is not None:
        gen_t, gen_v = _gen_build_time(gen_cfg, run_date)
    else:
        gen_t, gen_v = 8.2, 488

    transfer_t = round(gen_t + 4.2, 2)
    confirm_t  = round(transfer_t + 0.7, 2)

    ups_states = []
    all_online = True
    for ups_cfg in ups_cfgs:
        rec = execute_test(device_id=ups_cfg["device_type_slug"], config=ups_cfg,
                           test_def={"name": "ups_battery_transfer",
                                     "type": "ups_battery_transfer",
                                     "parameters": {"waveform_seconds": 60, "sample_period_ms": 100},
                                     "acceptance": {"min_voltage_v": 460.0,
                                                    "max_switchover_ms": 10.0,
                                                    "max_weak_cells": 5},
                                     "instrument": {}},
                           run_date=run_date)
        stayed = rec["passed"]
        all_online = all_online and stayed
        ups_states.append({
            "id": ups_cfg.get("instance", {}).get("device_id", ups_cfg["device_type_slug"]),
            "site": "DC1-Ashburn",
            "min_input_v_during": rec["min_voltage_v"],
            "battery_pct_after": rec["battery_pct"],
            "stayed_online": stayed,
        })

    online_actual   = f"{sum(1 for u in ups_states if u['stayed_online'])} of {n_ups} confirmed"
    online_expected = f"{n_ups} of {n_ups} online_battery → online"

    sequence = [
        {"step": "Pre-test: gen ready_standby", "expected": "ready", "actual": "ready", "passed": True, "t_offset_ms": 0},
        {"step": "Pre-test: ats == source_1", "expected": "source_1", "actual": "source_1", "passed": True, "t_offset_ms": 0},
        {"step": "Pre-test: all downstream UPS online", "expected": f"{n_ups} of {n_ups}", "actual": f"{n_ups} of {n_ups}", "passed": True, "t_offset_ms": 0},
        {"step": "Simulate utility loss", "expected": "command issued", "actual": "command issued", "passed": True, "t_offset_ms": 200},
        {"step": "Gen voltage build", "expected": "≥ rated within 10 s", "actual": f"{gen_v} V @ {gen_t} s", "passed": gen_t <= 10.0, "t_offset_ms": int(gen_t * 1000)},
        {"step": "Gen frequency settle", "expected": "59.5 – 60.5 Hz", "actual": "60.02 Hz", "passed": True, "t_offset_ms": int(gen_t * 1000)},
        {"step": "ATS transfer to source_2", "expected": f"≤ {accept['max_transfer_seconds']:.0f} s", "actual": f"{transfer_t} s", "passed": transfer_t <= accept["max_transfer_seconds"], "t_offset_ms": int(transfer_t * 1000)},
        {"step": "All downstream UPS still online", "expected": online_expected, "actual": online_actual, "passed": all_online, "t_offset_ms": int(confirm_t * 1000)},
        {"step": "Hold on gen", "expected": "5 min stable", "actual": "stable", "passed": True, "t_offset_ms": 313_000},
        {"step": "Return to utility", "expected": "auto-retransfer", "actual": "8.7 s", "passed": True, "t_offset_ms": 320_000},
        {"step": "Gen cooldown", "expected": "5 min idle then stop", "actual": "stopped @ 5:00.4", "passed": True, "t_offset_ms": 620_400},
    ]
    return {
        "device_id": device_id,
        "test_name": test_def["name"],
        "test_type": "ats_transfer",
        "spec_reference": test_def.get("spec_reference"),
        "rated_amps": config["ratings"]["rated_amps"],
        "manufacturer": config.get("manufacturer"),
        "model": config.get("model"),
        "sequence": sequence,
        "downstream_ups": ups_states,
        "overall_passed": all(s["passed"] is True for s in sequence),
        "passed": all(s["passed"] is True for s in sequence),
        "instrument": _instrument_block(test_def),
        "completed_at": run_date.isoformat(),
    }


# ---------------------------------------------------------------------------
# Test type: sel_secondary_injection / sel_primary_injection
#
# Drives a relay test set (e.g., Doble F6150) to inject current at the
# relay's CT secondary side. Verifies pickup ±5% of setting (IEEE
# C37.233 §6.3.1) and timing across a sweep of multiples on the
# coordination-study TCC curve (IEEE C37.112-2018 §5).
#
# `secondary` injects post-CT (typical 5 A scale) — exercises just the
# relay logic. `primary` injects pre-CT (high-current loop), exercising
# the full CT + relay chain. Same control flow, just different physics
# parameters in the Config Context.
# ---------------------------------------------------------------------------


from src.instruments.doble_f6150 import ieee_c37_112_curve_seconds


def _relay_pickup_actual(setpoint_a: float, age_years: float,
                          rng: random.Random) -> float:
    """Model the actual pickup value of a relay element after `age_years`
    of in-service life. Drift is small for solid-state relays — well
    inside the IEEE C37.233 ±5% tolerance for any reasonable age.
    """
    drift_pct = 0.7 * age_years + rng.uniform(-0.4, 0.4)
    return setpoint_a * (1.0 + drift_pct / 100.0)


def _relay_trip_actual(expected_s: float, rng: random.Random) -> float:
    """Per-shot timing variance — protective relays target ±3% one-sigma
    repeatability per IEEE C37.90-2005 §6.4. We model per-injection jitter
    plus a small calibration offset, both well inside the C37.233 ±5%.
    """
    jitter = rng.uniform(-0.018, 0.018)
    return expected_s * (1.0 + jitter)


def _relay_injection_handler(injection_kind: str):
    def _handler(device_id, config, test_def, run_date, cross):
        params = test_def["parameters"]
        accept = test_def.get("acceptance", {})
        elements_cfg = config["ratings"].get("relay_elements", [])
        elements_cfg = {e["code"]: e for e in elements_cfg}
        rng = _seed(device_id, run_date,
                    0x53493 if injection_kind == "secondary" else 0x504A)
        age = _age_years(config, run_date)

        results = []
        for element in params["elements"]:
            code = element["code"]
            curve_kind = element.get("curve_kind", "ieee_very_inverse")
            td = float(element.get("td", 1.0))
            multiples = element.get("multiples", [2.0, 5.0, 10.0])
            cfg_setpoint = elements_cfg.get(code, {}).get("pickup_a")
            setpoint = float(element.get("pickup_a", cfg_setpoint or 1.0))
            tol_pct = float(accept.get("pickup_tolerance_pct", 5.0))
            time_tol_pct = float(accept.get("timing_tolerance_pct", 5.0))
            min_time_tol_ms = int(accept.get("min_timing_tolerance_ms", 50))

            actual_pickup = _relay_pickup_actual(setpoint, age, rng)
            pickup_dev = (actual_pickup - setpoint) / setpoint * 100.0
            pickup_passed = abs(pickup_dev) <= tol_pct

            tcc_points = []
            for m in multiples:
                expected_s = ieee_c37_112_curve_seconds(curve_kind, td, float(m))
                actual_s = _relay_trip_actual(expected_s, rng)
                dev = (actual_s - expected_s) / expected_s * 100.0
                tol_abs = max(time_tol_pct / 100.0 * expected_s,
                              min_time_tol_ms / 1000.0)
                point_passed = abs(actual_s - expected_s) <= tol_abs
                tcc_points.append({
                    "multiple": float(m),
                    "current_a": round(setpoint * float(m), 3),
                    "expected_s": round(expected_s, 4),
                    "actual_s": round(actual_s, 4),
                    "deviation_pct": round(dev, 3),
                    "passed": point_passed,
                })
            timing_passed = all(p["passed"] for p in tcc_points)

            results.append({
                "code": code,
                "function": element.get("function", code),
                "curve_kind": curve_kind,
                "td": td,
                "setpoint_a": round(setpoint, 3),
                "actual_pickup_a": round(actual_pickup, 3),
                "pickup_deviation_pct": round(pickup_dev, 3),
                "pickup_passed": pickup_passed,
                "tcc_points": tcc_points,
                "timing_passed": timing_passed,
                "passed": pickup_passed and timing_passed,
            })

        all_passed = all(r["passed"] for r in results)
        return {
            "device_id": device_id,
            "test_name": test_def["name"],
            "test_type": f"sel_{injection_kind}_injection",
            "spec_reference": test_def.get("spec_reference",
                                            "NETA ATS-17 §7.10 / IEEE C37.233 §6.3"),
            "manufacturer": config.get("manufacturer"),
            "model": config.get("model"),
            "injection_kind": injection_kind,
            "elements_tested": [r["code"] for r in results],
            "results": results,
            "passed": all_passed,
            "instrument": _instrument_block(test_def),
            "completed_at": run_date.isoformat(),
        }
    return _handler


_sel_secondary_injection = _relay_injection_handler("secondary")
_sel_primary_injection = _relay_injection_handler("primary")


# ---------------------------------------------------------------------------
# Test type: breaker_timing
#
# Drives a breaker analyzer (e.g., Megger TM1800) to capture per-pole
# contact timing, simultaneity, motion stroke, and charge-spring time.
# Acceptance per IEC 62271-100 §6.101 + IEEE C37.09:
#   - Pole simultaneity ≤ 2 ms (open) / ≤ 5 ms (close)
#   - Each pole's open/close time within ±10% of nameplate
#   - Spring charge ≤ rated time
# ---------------------------------------------------------------------------


def _breaker_timing(device_id, config, test_def, run_date, cross):
    params = test_def["parameters"]
    accept = test_def.get("acceptance", {})
    rats = config["ratings"]
    rng = _seed(device_id, run_date, 0x42524B)
    age = _age_years(config, run_date)

    # Mechanical-wear model: contact times drift ~0.4% per year at the
    # mean, simultaneity widens ~0.05 ms/yr, spring-charge time drifts
    # +0.02 s/yr — all per OEM service-life data.
    nominal_open_ms = float(rats["nominal_open_time_ms"])
    nominal_close_ms = float(rats["nominal_close_time_ms"])
    nominal_spring_s = float(rats["nominal_spring_charge_s"])
    timing_tol_pct = float(accept.get("timing_tolerance_pct", 10.0))
    open_simul_tol_ms = float(accept.get("max_open_simultaneity_ms", 2.0))
    close_simul_tol_ms = float(accept.get("max_close_simultaneity_ms", 5.0))
    spring_tol_s = float(accept.get("max_spring_charge_s",
                                     nominal_spring_s * 1.10))

    drift_pct = 0.4 * age
    per_pole = {}
    for pole in ("A", "B", "C"):
        wear = rng.uniform(-0.6, 0.9)
        open_ms = nominal_open_ms * (1 + (drift_pct + wear) / 100.0)
        close_ms = nominal_close_ms * (1 + (drift_pct + wear * 0.6) / 100.0)
        per_pole[pole] = {
            "open_ms": round(open_ms, 2),
            "close_ms": round(close_ms, 2),
            "open_passed": abs(open_ms - nominal_open_ms) / nominal_open_ms * 100
                           <= timing_tol_pct,
            "close_passed": abs(close_ms - nominal_close_ms) / nominal_close_ms * 100
                            <= timing_tol_pct,
        }

    open_times = [per_pole[p]["open_ms"] for p in "ABC"]
    close_times = [per_pole[p]["close_ms"] for p in "ABC"]
    open_simul = max(open_times) - min(open_times)
    close_simul = max(close_times) - min(close_times)

    spring_charge_s = nominal_spring_s + 0.02 * age + rng.uniform(-0.3, 0.5)
    stroke_peak = float(rats.get("nominal_stroke_mm", 100.0)) + rng.uniform(-1.5, 1.5)
    stroke_overtravel = 3.0 + rng.uniform(-0.4, 0.4)
    stroke_rebound = 1.0 + 0.05 * age + rng.uniform(-0.2, 0.2)
    trip_coil_a = float(rats.get("nominal_trip_coil_a", 8.0)) + rng.uniform(-0.4, 0.4)
    close_coil_a = float(rats.get("nominal_close_coil_a", 12.0)) + rng.uniform(-0.6, 0.6)

    timing_passed = all(p["open_passed"] and p["close_passed"]
                        for p in per_pole.values())
    simul_passed = (open_simul <= open_simul_tol_ms
                    and close_simul <= close_simul_tol_ms)
    spring_passed = spring_charge_s <= spring_tol_s

    return {
        "device_id": device_id,
        "test_name": test_def["name"],
        "test_type": "breaker_timing",
        "spec_reference": test_def.get("spec_reference",
                                        "IEC 62271-100 §6.101 / IEEE C37.09"),
        "manufacturer": config.get("manufacturer"),
        "model": config.get("model"),
        "rated_amps": rats.get("rated_amps"),
        "voltage_class_v": rats.get("voltage_class_v"),
        "nominal_open_time_ms": nominal_open_ms,
        "nominal_close_time_ms": nominal_close_ms,
        "timing_tolerance_pct": timing_tol_pct,
        "per_pole": per_pole,
        "max_simultaneity_ms": {
            "open": round(open_simul, 3),
            "close": round(close_simul, 3),
        },
        "open_simultaneity_passed": open_simul <= open_simul_tol_ms,
        "close_simultaneity_passed": close_simul <= close_simul_tol_ms,
        "stroke": {
            "peak_mm": round(stroke_peak, 2),
            "overtravel_mm": round(stroke_overtravel, 2),
            "rebound_mm": round(stroke_rebound, 2),
        },
        "spring_charge_s": round(spring_charge_s, 2),
        "spring_charge_passed": spring_passed,
        "coil_peak": {
            "trip_a": round(trip_coil_a, 2),
            "close_a": round(close_coil_a, 2),
        },
        "timing_passed": timing_passed,
        "simultaneity_passed": simul_passed,
        "passed": timing_passed and simul_passed and spring_passed,
        "instrument": _instrument_block(test_def),
        "completed_at": run_date.isoformat(),
    }


# ---------------------------------------------------------------------------
# Manual sign-off renderer
# ---------------------------------------------------------------------------


def render_manual_signoffs(config: dict, run_date: datetime) -> dict:
    out = {}
    for s in config.get("manual_signoffs", []) or []:
        out[s["name"]] = {
            "label": s["label"],
            "spec_reference": s.get("spec_reference"),
            "passed": True,
            "operator": "J. Reyes",
        }
    return out


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


_DISPATCH = {
    "dc_withstand": _dc_withstand,
    "insulation_resistance": _insulation_resistance,
    "joint_resistance_dlro": _joint_resistance_dlro,
    "dissolved_gas_analysis": _dissolved_gas_analysis,
    "transformer_turns_ratio": _transformer_turns_ratio,
    "polarization_index": _polarization_index,
    "transformer_hipot_history": _transformer_hipot_history,
    "ups_battery_transfer": _ups_battery_transfer,
    "generator_loadbank": _generator_loadbank,
    "ats_transfer_sequence": _ats_transfer_sequence,
    "sel_secondary_injection": _sel_secondary_injection,
    "sel_primary_injection": _sel_primary_injection,
    "breaker_timing": _breaker_timing,
}


SUPPORTED_TEST_TYPES = list(_DISPATCH.keys())
