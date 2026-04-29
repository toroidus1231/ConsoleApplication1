"""Per-equipment commissioning records: DGA gas history, generator load-
bank traces, UPS battery transfer waveforms, hipot test traces, ATS
transfer sequences. The UI renders these in equipment-specific consoles.

All data is deterministic — seeded by device_id — so screenshots and
tests are stable.
"""

from __future__ import annotations

import math
import random
from typing import Iterable


def transformer_record(xfmr_id: str, c2h2_ppm_now: float) -> dict:
    """DGA history (90-day trend), TTR, polarization index, hipot history."""
    rng = random.Random(hash(xfmr_id))

    # 90-day DGA gas trend, sample every 7 days.
    history = []
    base_h2  = rng.uniform(20, 50)
    base_ch4 = rng.uniform(8, 22)
    base_c2h6 = rng.uniform(4, 14)
    base_c2h4 = rng.uniform(0.5, 3.5)
    # Drift the C2H2 per device — XFMR-A1 trends up to today's 3.4
    today_c2h2 = c2h2_ppm_now
    start_c2h2 = max(0.05, today_c2h2 * 0.15)
    for d in range(13, -1, -1):
        days_ago = d * 7
        frac = (13 - d) / 13
        history.append({
            "days_ago": days_ago,
            "h2":   round(base_h2  * (1 + 0.01 * d) + rng.uniform(-2, 2), 1),
            "ch4":  round(base_ch4 * (1 + 0.005 * d) + rng.uniform(-1, 1), 2),
            "c2h6": round(base_c2h6 + rng.uniform(-0.5, 0.5), 2),
            "c2h4": round(base_c2h4 + rng.uniform(-0.3, 0.3), 2),
            "c2h2": round(start_c2h2 + (today_c2h2 - start_c2h2) * frac + rng.uniform(-0.05, 0.05), 2),
            "co":   round(rng.uniform(180, 260), 0),
            "co2":  round(rng.uniform(1800, 2400), 0),
        })

    # TTR (Transformer Turns Ratio): 6 winding pairs, 13.8/0.48 = 28.75 nominal
    nominal_ratio = 13800 / 480
    ttr = []
    for tap in (-2, -1, 0, 1, 2):
        for phase in ("A", "B", "C"):
            measured = nominal_ratio * (1 + tap * 0.025) + rng.uniform(-0.05, 0.05)
            expected = nominal_ratio * (1 + tap * 0.025)
            err_pct = (measured - expected) / expected * 100
            ttr.append({
                "tap": tap, "phase": phase,
                "expected": round(expected, 4), "measured": round(measured, 4),
                "deviation_pct": round(err_pct, 3),
                "passed": abs(err_pct) <= 0.5,
            })

    # Polarization index: R every minute for 10 min, PI = R10 / R1 (>2.0 = good)
    pi_curve = []
    r0 = rng.uniform(800, 1500)
    for t in range(0, 11):
        r = r0 * (1 + 0.18 * (1 - math.exp(-t / 3.5)))
        pi_curve.append({"minute": t, "resistance_mohm": round(r, 1)})
    pi_value = pi_curve[-1]["resistance_mohm"] / pi_curve[1]["resistance_mohm"]

    # Hipot history (last 4 commissioning runs at 80% rated DC)
    hipot = []
    for i in range(4):
        leak = 0.12 + i * 0.07 + rng.uniform(-0.01, 0.01)
        hipot.append({
            "date_offset_days": -90 + i * 30,
            "voltage_kv": 11.04, "leakage_ma": round(leak, 3),
            "duration_seconds": 60, "passed": leak < 0.5,
        })

    return {
        "device_id": xfmr_id,
        "rated_kva": 2500,
        "voltage_class": "13.8 kV / 480 V",
        "vector_group": "Dyn1",
        "dga_history": history,
        "ttr": ttr,
        "polarization_index": {
            "value": round(pi_value, 2),
            "curve": pi_curve,
            "passed": pi_value >= 2.0,
        },
        "hipot_history": hipot,
    }


def generator_record(gen_id: str) -> dict:
    """Load-bank test result + startup sequence checklist."""
    rng = random.Random(hash(gen_id))

    # Load-bank: ramp to 25%, 50%, 75%, 100% rated, hold each step. 4-hour test.
    rated_kw = 1500
    steps = [0.25, 0.50, 0.75, 1.00]
    trace = []
    t = 0.0
    rpm = 0
    for i, target in enumerate(steps):
        for s in range(60 * 60):  # 1h per step
            if rpm < 1800:
                rpm = min(1800, rpm + 600)
            kw = rated_kw * target * (1 + math.sin(t / 11) * 0.012) + rng.uniform(-3, 3)
            oil_t = 92 + (target * 16) + math.sin(t / 19) * 1.5
            cool_t = 78 + (target * 12) + math.sin(t / 23) * 1.0
            v = 480 + math.sin(t / 13) * 1.5 + rng.uniform(-0.5, 0.5)
            f = 60.0 + math.sin(t / 17) * 0.04
            if s % 30 == 0:  # one sample every 30 s
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

    # Startup checklist (typical for a CAT 3516B)
    startup = [
        {"step": "Pre-lube oil pump", "expected": "≥20 psi within 5 s", "actual": "23 psi @ 4.2 s", "passed": True},
        {"step": "Crank engagement", "expected": "≤300 ms", "actual": "240 ms", "passed": True},
        {"step": "Ignition", "expected": "Combustion within 8 s of crank", "actual": "5.4 s", "passed": True},
        {"step": "Ramp to rated RPM", "expected": "1800 RPM within 12 s", "actual": "10.1 s", "passed": True},
        {"step": "AVR / governor settle", "expected": "V ±2%, f ±0.2 Hz, 5 s", "actual": "stable @ 6.8 s", "passed": True},
        {"step": "Ready signal", "expected": "Asserted within 15 s of crank", "actual": "12.4 s", "passed": True},
        {"step": "Close to bus", "expected": "On 'transfer requested'", "actual": "manual hold (commissioning mode)", "passed": True},
        {"step": "Cooldown after stop", "expected": "5 min idle", "actual": "—", "passed": None},
    ]

    return {
        "device_id": gen_id,
        "rated_kw": rated_kw,
        "manufacturer": "Caterpillar",
        "model": "3516B",
        "loadbank_trace": trace,
        "startup_sequence": startup,
        "passed_overall": all(s["passed"] is not False for s in startup),
    }


def ups_record(ups_id: str) -> dict:
    """UPS battery transfer test waveform + cell health."""
    rng = random.Random(hash(ups_id))

    # Battery transfer waveform: 60s at 100ms granularity
    waveform = []
    for i in range(600):
        ms = i * 100
        if ms < 5:
            v = 480 + rng.uniform(-0.5, 0.5)
        elif ms < 10:
            v = 460 + rng.uniform(-1, 1)
        elif ms < 50:
            v = 460 + 15 * (1 - math.exp(-(ms - 10) / 12))
        else:
            v = 475 + math.sin(ms / 1500) * 1.5 + rng.uniform(-0.2, 0.2)
        waveform.append({
            "t_ms": ms,
            "output_voltage": round(v, 2),
            "battery_pct": round(96 - ms / 60_000 * 4, 2),
            "load_pct": round(62 + math.sin(ms / 2000) * 4, 1),
        })

    # 240 lithium cells, with a few outliers
    cells = []
    for i in range(240):
        v = 3.65 + rng.uniform(-0.04, 0.04)
        if i in {17, 142, 199}:
            v = rng.uniform(3.45, 3.52)  # weak cells
        cells.append({"id": i + 1, "voltage": round(v, 3), "ok": v >= 3.55})

    transfer_history = [
        {"date_offset_days": -180, "passed": True,  "min_voltage_v": 461.2, "switchover_ms": 8.2},
        {"date_offset_days":  -90, "passed": True,  "min_voltage_v": 460.8, "switchover_ms": 8.5},
        {"date_offset_days":  -30, "passed": True,  "min_voltage_v": 459.4, "switchover_ms": 8.7},
        {"date_offset_days":   -1, "passed": True,  "min_voltage_v": 460.0, "switchover_ms": 8.6},
    ]

    return {
        "device_id": ups_id,
        "rated_kw": 750,
        "manufacturer": "APC",
        "model": "Symmetra MW",
        "transfer_waveform_60s": waveform,
        "cells_count": 240,
        "weak_cells": [c for c in cells if not c["ok"]],
        "all_cells": cells,
        "transfer_history": transfer_history,
    }


def cable_hipot_record(cable_id: str = "cable-mv-main-A-to-xfmr-A1") -> dict:
    """Hipot test trace: voltage ramp + leakage current vs time."""
    rng = random.Random(hash(cable_id))
    target_kv = 11.04   # 80% of 13.8 kV rated
    ramp_seconds = 60.0
    hold_seconds = 600.0  # 10 min hold for MV cable

    trace = []
    leak_trip = 0.5  # mA
    insulation_megohm = 800.0
    failed = False
    fail_reason = None
    for t in range(0, int(ramp_seconds + hold_seconds) + 1):
        if t < ramp_seconds:
            v = target_kv * (t / ramp_seconds)
        else:
            v = target_kv
        # Resistive leakage. I = V / R in amps. With V in kV and R in MΩ:
        # I_µA = V_kV / R_MΩ × 1000 → I_mA = V_kV / R_MΩ.
        # 11.04 kV / 800 MΩ ≈ 0.014 mA at full voltage — well under 0.5 mA.
        leak = (v / insulation_megohm) * 1.0 + rng.uniform(-0.0005, 0.0005)
        # Trend leakage up slightly during the hold (real cables show this)
        if t > ramp_seconds:
            leak += (t - ramp_seconds) / hold_seconds * 0.005
        # No fault trip — passed test
        trace.append({
            "t_seconds": t,
            "voltage_kv": round(v, 4),
            "leakage_ma": round(leak, 4),
            "phase": "DC+",
        })

    return {
        "cable_id": cable_id,
        "test_type": "DC Hipot",
        "target_kv": target_kv,
        "ramp_seconds": ramp_seconds,
        "hold_seconds": hold_seconds,
        "leakage_trip_threshold_ma": leak_trip,
        "instrument": {"vendor": "Vitrek", "model": "95X",
                       "serial": "SN-12345", "cal_cert": "sha256:vitrek-2026-q2"},
        "trace": trace,
        "passed": True if not failed else False,
        "fail_reason": fail_reason,
        "history": [
            {"date_offset_days": -180, "max_leakage_ma": 0.31, "passed": True},
            {"date_offset_days":  -90, "max_leakage_ma": 0.37, "passed": True},
            {"date_offset_days":   -1, "max_leakage_ma": 0.42, "passed": True},
        ],
    }


def ats_record(ats_id: str) -> dict:
    """ATS transfer test sequence-of-operations.

    Per spec §4.3: must verify EVERY downstream UPS stays online during
    the simulated utility loss; one unhealthy UPS = outage."""
    return {
        "device_id": ats_id,
        "rated_amps": 4000,
        "manufacturer": "ASCO",
        "model": "7000",
        "sequence": [
            {"step": "Pre-test: gen ready_standby", "expected": "ready", "actual": "ready", "passed": True, "t_offset_ms": 0},
            {"step": "Pre-test: ats == source_1", "expected": "source_1", "actual": "source_1", "passed": True, "t_offset_ms": 0},
            {"step": "Pre-test: all downstream UPS online", "expected": "12 of 12", "actual": "12 of 12", "passed": True, "t_offset_ms": 0},
            {"step": "Simulate utility loss", "expected": "command issued", "actual": "command issued", "passed": True, "t_offset_ms": 200},
            {"step": "Gen voltage build", "expected": "≥ rated within 10 s", "actual": "488 V @ 8.2 s", "passed": True, "t_offset_ms": 8_200},
            {"step": "Gen frequency settle", "expected": "59.5 – 60.5 Hz", "actual": "60.02 Hz", "passed": True, "t_offset_ms": 8_200},
            {"step": "ATS transfer to source_2", "expected": "≤ 30 s", "actual": "12.4 s", "passed": True, "t_offset_ms": 12_400},
            {"step": "All downstream UPS still online", "expected": "12 of 12 online_battery → online", "actual": "12 of 12 confirmed", "passed": True, "t_offset_ms": 13_100},
            {"step": "Hold on gen", "expected": "5 min stable", "actual": "stable", "passed": True, "t_offset_ms": 313_000},
            {"step": "Return to utility", "expected": "auto-retransfer", "actual": "8.7 s", "passed": True, "t_offset_ms": 320_000},
            {"step": "Gen cooldown", "expected": "5 min idle then stop", "actual": "stopped @ 5:00.4", "passed": True, "t_offset_ms": 620_400},
        ],
        "downstream_ups": [
            {"id": f"ups-{side}", "site": "DC1-Ashburn",
             "min_input_v_during": 472, "battery_pct_after": 95.4,
             "stayed_online": True}
            for side in ("A", "B")
        ],
        "overall_passed": True,
    }
