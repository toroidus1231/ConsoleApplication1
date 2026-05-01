"""Tests for src/test_executor.py — the generic test record builder.

Covers every dispatch type: dc_withstand, insulation_resistance,
joint_resistance_dlro, dissolved_gas_analysis, transformer_turns_ratio,
polarization_index, transformer_hipot_history, ups_battery_transfer,
generator_loadbank, ats_transfer_sequence.

The tests verify shape + invariants (every required key present, ageing
math monotonic, acceptance evaluators correct) rather than exact
numeric values which depend on the rng seed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import pytest

from src.test_executor import execute_test, render_manual_signoffs


def _today(): return datetime(2026, 5, 1)


# ---------------------------------------------------------------------------
# dc_withstand
# ---------------------------------------------------------------------------


def test_dc_withstand_fixed_kv():
    cfg = {
        "category": "busway",
        "ageing_model": {"params": {"initial_megohm": 8000.0,
                                     "aging_per_year": 0.025,
                                     "install_year": 2022}},
        "ratings": {"rated_amps": 4000, "voltage_class_v": 600},
    }
    test_def = {
        "name": "busway_hipot", "type": "dc_withstand",
        "instrument": {"vendor": "Vitrek", "model": "95X"},
        "parameters": {"target_kv": 2.5, "ramp_seconds": 60,
                       "hold_seconds": 60, "test_kind": "fixed_kv"},
        "acceptance": {"max_leakage_ma": 1.0},
    }
    rec = execute_test(device_id="mtg-feed-A", config=cfg,
                       test_def=test_def, run_date=_today())
    assert rec["target_kv"] == 2.5
    assert rec["passed"] is True
    assert rec["peak_leakage_ma"] < 1.0
    assert len(rec["trace"]) == 60 + 60 + 1
    assert rec["instrument"]["model"] == "95X"


def test_dc_withstand_ieee_400_uses_test_factor():
    cfg = {
        "category": "cable",
        "ageing_model": {"params": {"initial_megohm": 220.0,
                                     "aging_per_year": 0.045,
                                     "install_year": 2017}},
        "ratings": {"rated_kv": 13.8},
    }
    test_def = {
        "name": "cable_hipot", "type": "dc_withstand",
        "instrument": {},
        "parameters": {"test_factor": 0.8, "ramp_seconds": 60,
                       "hold_seconds": 600,
                       "test_kind": "ieee_400_maintenance"},
        "acceptance": {"max_leakage_ma": 0.5},
    }
    rec = execute_test(device_id="mv-main-A", config=cfg,
                       test_def=test_def, run_date=_today())
    assert rec["target_kv"] == 11.04  # 13.8 * 0.8
    assert rec["passed"] is True


def test_dc_withstand_ages_monotonically():
    cfg = {
        "category": "cable",
        "ageing_model": {"params": {"initial_megohm": 220.0,
                                     "aging_per_year": 0.05,
                                     "install_year": 2017}},
        "ratings": {"rated_kv": 13.8},
    }
    test_def = {
        "name": "cable_hipot", "type": "dc_withstand",
        "instrument": {},
        "parameters": {"test_factor": 0.8, "ramp_seconds": 60,
                       "hold_seconds": 60,
                       "test_kind": "ieee_400_maintenance"},
        "acceptance": {"max_leakage_ma": 0.5},
    }
    young = execute_test(device_id="d", config=cfg, test_def=test_def,
                         run_date=_today() - timedelta(days=540))
    old = execute_test(device_id="d", config=cfg, test_def=test_def,
                       run_date=_today())
    assert old["peak_leakage_ma"] > young["peak_leakage_ma"]
    assert old["insulation_megohm"] < young["insulation_megohm"]


# ---------------------------------------------------------------------------
# insulation_resistance
# ---------------------------------------------------------------------------


def test_insulation_resistance_six_phase_pairs():
    cfg = {
        "category": "busway",
        "ageing_model": {"params": {"initial_megohm": 8000.0,
                                     "aging_per_year": 0.025,
                                     "install_year": 2022}},
        "ratings": {"rated_amps": 4000, "voltage_class_v": 600},
    }
    test_def = {
        "name": "busway_megger", "type": "insulation_resistance",
        "instrument": {"vendor": "Megger", "model": "MIT525"},
        "parameters": {"test_voltage_v": 1000, "applied_seconds": 60,
                       "phase_pairs": [["A","B"],["B","C"],["A","C"],
                                        ["A","G"],["B","G"],["C","G"]]},
        "acceptance": {"min_megohm": 100.0},
    }
    rec = execute_test(device_id="mtg-feed-A", config=cfg,
                       test_def=test_def, run_date=_today())
    assert len(rec["readings"]) == 6
    assert all(r["passed"] for r in rec["readings"])
    assert rec["min_megohm"] > 100.0


def test_insulation_resistance_phase_to_ground_lower():
    cfg = {
        "category": "busway",
        "ageing_model": {"params": {"initial_megohm": 8000.0,
                                     "aging_per_year": 0.025,
                                     "install_year": 2022}},
        "ratings": {"rated_amps": 4000, "voltage_class_v": 600},
    }
    test_def = {
        "name": "m", "type": "insulation_resistance",
        "instrument": {},
        "parameters": {"test_voltage_v": 1000, "applied_seconds": 60,
                       "phase_pairs": [["A","B"],["A","G"]]},
        "acceptance": {"min_megohm": 100.0},
    }
    rec = execute_test(device_id="d", config=cfg, test_def=test_def,
                       run_date=_today())
    ab = next(r for r in rec["readings"] if r["from"] == "A" and r["to"] == "B")
    ag = next(r for r in rec["readings"] if r["from"] == "A" and r["to"] == "G")
    assert ag["megohm"] < ab["megohm"]  # phase-to-ground bias 0.85


# ---------------------------------------------------------------------------
# joint_resistance_dlro
# ---------------------------------------------------------------------------


def test_dlro_emits_correct_joint_count():
    cfg = {
        "category": "busway",
        "ageing_model": {"params": {"initial_joint_uohm": 18.0,
                                     "joint_aging_per_year": 0.015,
                                     "install_year": 2022}},
        "ratings": {"rated_amps": 4000, "voltage_class_v": 600,
                     "length_m": 30.0},
    }
    test_def = {
        "name": "busway_dlro", "type": "joint_resistance_dlro",
        "instrument": {"vendor": "Megger", "model": "DLRO10X"},
        "parameters": {"test_current_a": 10.0, "joints_per_meter": 0.33,
                       "end_terminations": 2, "phases": ["A", "B", "C"]},
        "acceptance": {"max_uohm_per_joint": 30.0},
    }
    rec = execute_test(device_id="mtg-feed-A", config=cfg,
                       test_def=test_def, run_date=_today())
    # Expected: int(30 * 0.33) + 2 = 9 + 2 = 11
    assert rec["joint_count"] == 11
    assert all(j["passed"] for j in rec["joints"])
    for j in rec["joints"]:
        assert set(j["uohm_per_phase"].keys()) == {"A", "B", "C"}


def test_dlro_joints_age_monotonically():
    cfg = {
        "category": "busway",
        "ageing_model": {"params": {"initial_joint_uohm": 18.0,
                                     "joint_aging_per_year": 0.05,
                                     "install_year": 2018}},
        "ratings": {"rated_amps": 4000, "voltage_class_v": 600,
                     "length_m": 30.0},
    }
    test_def = {
        "name": "j", "type": "joint_resistance_dlro",
        "instrument": {},
        "parameters": {"test_current_a": 10.0, "joints_per_meter": 0.33,
                       "end_terminations": 2, "phases": ["A","B","C"]},
        "acceptance": {"max_uohm_per_joint": 50.0},
    }
    young = execute_test(device_id="d", config=cfg, test_def=test_def,
                         run_date=datetime(2019, 1, 1))
    old = execute_test(device_id="d", config=cfg, test_def=test_def,
                       run_date=datetime(2026, 1, 1))
    assert old["max_joint_uohm"] > young["max_joint_uohm"]


# ---------------------------------------------------------------------------
# dissolved_gas_analysis
# ---------------------------------------------------------------------------


def test_dga_healthy_transformer_low_c2h2():
    cfg = {
        "category": "transformer",
        "device_type_slug": "oil-xfmr-2500kva",
        "ageing_model": {"params": {"install_year": 2018,
                                     "fault_state": "healthy"}},
        "ratings": {"rated_kva": 2500, "primary_kv": 13.8,
                     "secondary_v": 480.0},
    }
    test_def = {"name": "dga", "type": "dissolved_gas_analysis",
                "instrument": {},
                "parameters": {"history_weeks": 14},
                "acceptance": {"max_c2h2_ppm": 1.0}}
    rec = execute_test(device_id="xfmr-A2", config=cfg, test_def=test_def,
                       run_date=_today())
    assert rec["fault_state"] == "healthy"
    assert rec["passed"] is True
    assert rec["current_gases"]["c2h2"] < 1.0


def test_dga_active_arcing_lifts_c2h2():
    today = _today()
    cfg = {
        "category": "transformer",
        "device_type_slug": "oil-xfmr-2500kva",
        "ageing_model": {"params": {
            "install_year": 2018,
            "fault_state": "active_arcing",
            "fault_severity": 1.1,
            "fault_onset": (today - timedelta(days=110)).isoformat(),
        }},
        "ratings": {"rated_kva": 2500, "primary_kv": 13.8,
                     "secondary_v": 480.0},
    }
    test_def = {"name": "dga", "type": "dissolved_gas_analysis",
                "instrument": {},
                "parameters": {"history_weeks": 14},
                "acceptance": {"max_c2h2_ppm": 1.0}}
    rec = execute_test(device_id="xfmr-A1", config=cfg, test_def=test_def,
                       run_date=today)
    assert rec["fault_state"] == "active_arcing"
    assert rec["current_gases"]["c2h2"] > 1.0
    assert rec["passed"] is False


def test_dga_history_has_n_weekly_samples():
    cfg = {
        "category": "transformer",
        "device_type_slug": "oil-xfmr-2500kva",
        "ageing_model": {"params": {"install_year": 2018,
                                     "fault_state": "healthy"}},
        "ratings": {"rated_kva": 2500, "primary_kv": 13.8,
                     "secondary_v": 480.0},
    }
    test_def = {"name": "dga", "type": "dissolved_gas_analysis",
                "instrument": {},
                "parameters": {"history_weeks": 10},
                "acceptance": {}}
    rec = execute_test(device_id="d", config=cfg, test_def=test_def,
                       run_date=_today())
    assert len(rec["dga_history"]) == 10


# ---------------------------------------------------------------------------
# transformer_turns_ratio + polarization_index + transformer_hipot_history
# ---------------------------------------------------------------------------


def test_ttr_per_tap_per_phase():
    cfg = {
        "category": "transformer",
        "device_type_slug": "oil-xfmr-2500kva",
        "ageing_model": {"params": {"install_year": 2018,
                                     "fault_state": "healthy"}},
        "ratings": {"primary_kv": 13.8, "secondary_v": 480.0},
    }
    test_def = {"name": "ttr", "type": "transformer_turns_ratio",
                "instrument": {},
                "parameters": {"taps": [-2, -1, 0, 1, 2],
                                "phases": ["A", "B", "C"]},
                "acceptance": {"max_deviation_pct": 0.5}}
    rec = execute_test(device_id="d", config=cfg, test_def=test_def,
                       run_date=_today())
    assert len(rec["ttr"]) == 15
    assert all(r["passed"] for r in rec["ttr"])


def test_pi_healthy_transformer_passes_ieee_43():
    cfg = {
        "category": "transformer",
        "device_type_slug": "oil-xfmr-2500kva",
        "ageing_model": {"params": {"install_year": 2018,
                                     "fault_state": "healthy",
                                     "initial_pi_megohm": 1100.0}},
        "ratings": {"primary_kv": 13.8, "secondary_v": 480.0},
    }
    test_def = {"name": "pi", "type": "polarization_index",
                "instrument": {},
                "parameters": {"test_voltage_v": 5000,
                                "duration_minutes": 10},
                "acceptance": {"min_pi": 2.0}}
    rec = execute_test(device_id="d", config=cfg, test_def=test_def,
                       run_date=_today())
    assert rec["polarization_index"]["value"] >= 2.0
    assert rec["passed"] is True


def test_pi_arcing_transformer_fails_ieee_43():
    today = _today()
    cfg = {
        "category": "transformer",
        "device_type_slug": "oil-xfmr-2500kva",
        "ageing_model": {"params": {
            "install_year": 2018,
            "fault_state": "active_arcing",
            "fault_severity": 1.1,
            "fault_onset": (today - timedelta(days=110)).isoformat(),
            "initial_pi_megohm": 1100.0,
        }},
        "ratings": {"primary_kv": 13.8, "secondary_v": 480.0},
    }
    test_def = {"name": "pi", "type": "polarization_index",
                "instrument": {},
                "parameters": {"test_voltage_v": 5000,
                                "duration_minutes": 10},
                "acceptance": {"min_pi": 2.0}}
    rec = execute_test(device_id="d", config=cfg, test_def=test_def,
                       run_date=today)
    assert rec["passed"] is False
    assert rec["polarization_index"]["value"] < 2.0


def test_transformer_hipot_history_returns_4_runs():
    cfg = {
        "category": "transformer",
        "device_type_slug": "oil-xfmr-2500kva",
        "ageing_model": {"params": {"install_year": 2018,
                                     "fault_state": "healthy"}},
        "ratings": {"primary_kv": 13.8, "secondary_v": 480.0},
    }
    test_def = {"name": "hp", "type": "transformer_hipot_history",
                "instrument": {},
                "parameters": {"duration_seconds": 60},
                "acceptance": {"max_leakage_ma": 0.5}}
    rec = execute_test(device_id="d", config=cfg, test_def=test_def,
                       run_date=_today())
    assert len(rec["hipot_history"]) == 4
    assert all(r["passed"] for r in rec["hipot_history"])


# ---------------------------------------------------------------------------
# ups_battery_transfer + generator_loadbank
# ---------------------------------------------------------------------------


def test_ups_battery_transfer_with_stable_weak_cells():
    cfg = {
        "category": "ups",
        "ageing_model": {"params": {
            "install_year": 2022, "install_month": 4,
            "weak_cell_ids": [17, 142, 199],
            "cell_aging_per_year": 0.005,
            "weak_cell_aging_per_year": 0.015,
            "dip_v_per_year": 1.0, "dip_v_initial": 14.0,
            "switchover_ms_per_year": 0.18, "switchover_ms_initial": 8.0,
        }},
        "ratings": {"rated_kw": 750, "cells_count": 240,
                     "cell_nominal_v": 3.65, "cell_weak_threshold_v": 3.55,
                     "nominal_v": 480},
    }
    test_def = {"name": "ups", "type": "ups_battery_transfer",
                "instrument": {},
                "parameters": {},
                "acceptance": {"min_voltage_v": 460.0,
                                "max_switchover_ms": 10.0,
                                "max_weak_cells": 5}}
    rec1 = execute_test(device_id="ups-A", config=cfg, test_def=test_def,
                        run_date=_today())
    rec2 = execute_test(device_id="ups-A", config=cfg, test_def=test_def,
                        run_date=_today() + timedelta(days=90))
    weak_ids_1 = {c["id"] for c in rec1["weak_cells"]}
    weak_ids_2 = {c["id"] for c in rec2["weak_cells"]}
    assert weak_ids_1 >= {17, 142, 199}
    assert weak_ids_2 >= {17, 142, 199}  # SAME cells across runs


def test_ups_battery_transfer_dip_widens_with_age():
    base_cfg = {
        "category": "ups",
        "ageing_model": {"params": {
            "install_year": 2022, "install_month": 4,
            "weak_cell_ids": [1], "cell_aging_per_year": 0.005,
            "weak_cell_aging_per_year": 0.015,
            "dip_v_per_year": 1.0, "dip_v_initial": 14.0,
            "switchover_ms_per_year": 0.18, "switchover_ms_initial": 8.0,
        }},
        "ratings": {"rated_kw": 750, "cells_count": 240,
                     "cell_nominal_v": 3.65, "cell_weak_threshold_v": 3.55,
                     "nominal_v": 480},
    }
    test_def = {"name": "u", "type": "ups_battery_transfer",
                "instrument": {}, "parameters": {},
                "acceptance": {"min_voltage_v": 460.0,
                                "max_switchover_ms": 10.0,
                                "max_weak_cells": 5}}
    young = execute_test(device_id="ups-A", config=base_cfg,
                         test_def=test_def, run_date=datetime(2022, 12, 1))
    old = execute_test(device_id="ups-A", config=base_cfg,
                       test_def=test_def, run_date=datetime(2026, 12, 1))
    assert old["switchover_ms"] > young["switchover_ms"]
    assert old["min_voltage_v"] < young["min_voltage_v"]


def test_generator_loadbank_4_steps():
    cfg = {
        "category": "generator",
        "ageing_model": {"params": {
            "install_year": 2020,
            "annual_runtime_hours_default": 85,
            "operating_hours_at_install_default": 0,
        }},
        "ratings": {"rated_kw": 1500, "voltage_v": 480.0,
                     "nominal_freq_hz": 60.0, "nominal_rpm": 1800},
    }
    test_def = {"name": "lb", "type": "generator_loadbank",
                "instrument": {},
                "parameters": {"steps": [0.25, 0.5, 0.75, 1.0],
                                "step_duration_minutes": 60},
                "acceptance": {"min_voltage_v": 470.0,
                                "max_voltage_v": 490.0,
                                "min_freq_hz": 59.5, "max_freq_hz": 60.5,
                                "min_kw_at_full": 1450,
                                "max_ready_signal_seconds": 15.0,
                                "max_ramp_seconds": 12.0}}
    rec = execute_test(device_id="gen-1", config=cfg, test_def=test_def,
                       run_date=_today())
    # 4 steps × 60 min × 60 s = 14400 s, decimated to ~480 samples
    assert len(rec["loadbank_trace"]) > 100
    assert len(rec["startup_sequence"]) == 8
    assert rec["passed_overall"] is True


# ---------------------------------------------------------------------------
# ats_transfer_sequence + cross-references
# ---------------------------------------------------------------------------


def test_ats_transfer_pulls_gen_build_time_from_cross_ref():
    gen_cfg = {
        "category": "generator",
        "ageing_model": {"params": {
            "install_year": 2020,
            "annual_runtime_hours_default": 85,
            "operating_hours_at_install_default": 0,
        }},
        "ratings": {"rated_kw": 1500, "voltage_v": 480.0,
                     "nominal_freq_hz": 60.0, "nominal_rpm": 1800},
    }
    ups_cfg = {
        "device_type_slug": "apc-symmetra-mw",
        "category": "ups",
        "ageing_model": {"params": {
            "install_year": 2022, "install_month": 4,
            "weak_cell_ids": [17, 142, 199],
            "cell_aging_per_year": 0.005,
            "weak_cell_aging_per_year": 0.015,
            "dip_v_per_year": 1.0, "dip_v_initial": 14.0,
            "switchover_ms_per_year": 0.18, "switchover_ms_initial": 8.0,
        }},
        "ratings": {"rated_kw": 750, "cells_count": 240,
                     "cell_nominal_v": 3.65, "cell_weak_threshold_v": 3.55,
                     "nominal_v": 480},
    }
    ats_cfg = {"category": "ats", "ratings": {"rated_amps": 4000}}
    test_def = {"name": "ats", "type": "ats_transfer_sequence",
                "instrument": {},
                "parameters": {"transfer_acceptance_seconds": 30},
                "acceptance": {"max_transfer_seconds": 30.0,
                                "min_voltage_during_v": 460.0}}
    rec = execute_test(device_id="ats-1", config=ats_cfg,
                       test_def=test_def, run_date=_today(),
                       cross_refs={"upstream_gen": gen_cfg,
                                    "downstream_ups": [ups_cfg]})
    gen_v_step = next(s for s in rec["sequence"] if s["step"] == "Gen voltage build")
    assert "@ " in gen_v_step["actual"]  # contains build time
    assert rec["overall_passed"] is True
    assert len(rec["downstream_ups"]) == 1


# ---------------------------------------------------------------------------
# Manual sign-offs + dispatch errors
# ---------------------------------------------------------------------------


def test_render_manual_signoffs():
    cfg = {"manual_signoffs": [
        {"name": "visual", "label": "Visual inspection",
         "spec_reference": "IEC 61439-6 §10.2"},
        {"name": "torque", "label": "Bolt torque @ 60 ft-lb",
         "spec_reference": "Vertiv MTG joint kit"},
    ]}
    out = render_manual_signoffs(cfg, _today())
    assert "visual" in out
    assert "torque" in out
    assert out["visual"]["passed"] is True


def test_unsupported_test_type_raises():
    with pytest.raises(KeyError, match="no executor"):
        execute_test(device_id="d", config={}, run_date=_today(),
                     test_def={"name": "x", "type": "bogus_test_type",
                               "parameters": {}, "acceptance": {},
                               "instrument": {}})
