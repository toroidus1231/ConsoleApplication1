"""Tests for src/equipment_views.py — panel aggregators per category."""

from __future__ import annotations

import pytest

from src.evidence_store import InMemoryEvidenceStore
from src.equipment_views import (
    URL_KIND_TO_CATEGORY, build_panel, build_panel_by_id,
    cable_panel, busway_panel, transformer_panel,
    generator_panel, ups_panel, ats_panel,
)


def _store_with_runs(runs_by_test: dict[str, list[dict]],
                     device_id: str = "d") -> InMemoryEvidenceStore:
    s = InMemoryEvidenceStore()
    for test_name, runs in runs_by_test.items():
        for r in runs:
            s.put(device_id, test_name, r)
    return s


# ---------------------------------------------------------------------------
# URL kind → category dispatch
# ---------------------------------------------------------------------------


def test_url_kind_aliases_resolve_to_canonical_category():
    assert URL_KIND_TO_CATEGORY["xfmr"] == "transformer"
    assert URL_KIND_TO_CATEGORY["transformer"] == "transformer"
    assert URL_KIND_TO_CATEGORY["hipot"] == "cable"
    assert URL_KIND_TO_CATEGORY["cable"] == "cable"
    assert URL_KIND_TO_CATEGORY["gens"] == "generator"
    assert URL_KIND_TO_CATEGORY["gen"] == "generator"
    assert URL_KIND_TO_CATEGORY["generator"] == "generator"


def test_build_panel_unknown_kind_raises():
    s = InMemoryEvidenceStore()
    with pytest.raises(KeyError, match="unknown URL kind"):
        build_panel("nope", "d", s)


# ---------------------------------------------------------------------------
# cable_panel
# ---------------------------------------------------------------------------


def test_cable_panel_no_prior_run():
    out = cable_panel("nope", InMemoryEvidenceStore())
    assert out["no_prior_run"] is True


def test_cable_panel_includes_history():
    s = _store_with_runs({"cable_hipot": [
        {"completed_at": "2026-01-01T00:00:00", "peak_leakage_ma": 0.076,
         "passed": True, "trace": [], "target_kv": 11.04},
        {"completed_at": "2026-04-01T00:00:00", "peak_leakage_ma": 0.082,
         "passed": True, "trace": [], "target_kv": 11.04},
    ]}, device_id="cab")
    out = cable_panel("cab", s)
    assert out["peak_leakage_ma"] == 0.082
    assert len(out["history"]) == 1
    assert out["history"][0]["max_leakage_ma"] == 0.076


# ---------------------------------------------------------------------------
# transformer_panel
# ---------------------------------------------------------------------------


def test_transformer_panel_aggregates_four_test_types():
    s = _store_with_runs({
        "dga_initial_sample": [{
            "completed_at": "2026-05-01", "passed": False,
            "fault_state": "active_arcing",
            "current_gases": {"c2h2": 3.4},
            "dga_history": [{"days_ago": 7, "c2h2": 3.0}],
        }],
        "transformer_turns_ratio": [{
            "completed_at": "2026-05-01", "passed": True,
            "ttr": [{"tap": 0, "phase": "A", "passed": True}],
        }],
        "polarization_index": [{
            "completed_at": "2026-05-01", "passed": False,
            "polarization_index": {"value": 1.4, "passed": False, "curve": []},
        }],
        "transformer_hipot": [{
            "completed_at": "2026-05-01", "passed": True,
            "hipot_history": [{"date_offset_days": -90, "passed": True,
                                "leakage_ma": 0.2}],
        }],
    }, device_id="xfmr-A1")
    out = transformer_panel("xfmr-A1", s)
    assert out["fault_state"] == "active_arcing"
    assert out["current_gases"]["c2h2"] == 3.4
    assert len(out["ttr"]) == 1
    assert out["polarization_index"]["value"] == 1.4
    assert len(out["hipot_history"]) == 1
    assert out["passed"] is False  # arcing fails


def test_transformer_panel_no_runs_returns_no_prior():
    out = transformer_panel("nope", InMemoryEvidenceStore())
    assert out["no_prior_run"] is True


# ---------------------------------------------------------------------------
# generator_panel + ups_panel + ats_panel
# ---------------------------------------------------------------------------


def test_generator_panel_returns_latest_only():
    s = _store_with_runs({"generator_load_bank": [
        {"completed_at": "2026-01-01", "passed": True, "rated_kw": 1500},
        {"completed_at": "2026-04-01", "passed": True, "rated_kw": 1500,
         "passed_overall": True},
    ]}, device_id="gen-1")
    out = generator_panel("gen-1", s)
    assert out["completed_at"] == "2026-04-01"


def test_ups_panel_includes_transfer_history():
    s = _store_with_runs({"ups_battery_transfer": [
        {"completed_at": "2026-01-01", "passed": True,
         "min_voltage_v": 462.0, "switchover_ms": 8.5},
        {"completed_at": "2026-04-01", "passed": True,
         "min_voltage_v": 461.0, "switchover_ms": 8.7},
    ]}, device_id="ups-A")
    out = ups_panel("ups-A", s)
    assert out["min_voltage_v"] == 461.0  # latest
    assert len(out["transfer_history"]) == 1
    assert out["transfer_history"][0]["min_voltage_v"] == 462.0


def test_ats_panel_returns_latest_only():
    s = _store_with_runs({"ats_transfer": [
        {"completed_at": "2026-04-01", "overall_passed": True,
         "passed": True, "sequence": []},
    ]}, device_id="ats-1")
    out = ats_panel("ats-1", s)
    assert out["overall_passed"] is True


# ---------------------------------------------------------------------------
# busway_panel
# ---------------------------------------------------------------------------


def test_busway_panel_aggregates_three_tests_plus_signoffs():
    s = InMemoryEvidenceStore()
    s.put("mtg-A", "busway_megger",
          {"completed_at": "2026-04-01", "passed": True, "min_megohm": 5800})
    s.put("mtg-A", "busway_hipot",
          {"completed_at": "2026-04-01", "passed": True, "peak_leakage_ma": 0.0008})
    s.put("mtg-A", "busway_dlro",
          {"completed_at": "2026-04-01", "passed": True, "max_joint_uohm": 19.5})
    s.put("mtg-A", "busway_manual", {
        "completed_at": "2026-04-01", "passed": True,
        "signoffs": {"visual": {"label": "Visual", "passed": True,
                                 "operator": "j.reyes"}},
    })
    out = busway_panel("mtg-A", s)
    assert out["megger"]["min_megohm"] == 5800
    assert out["hipot"]["peak_leakage_ma"] == 0.0008
    assert out["dlro"]["max_joint_uohm"] == 19.5
    assert "visual" in out["manual_signoffs"]
    assert out["passed"] is True
    assert out["automation_summary"]["automated_steps"] == 3
    assert out["automation_summary"]["manual_steps"] == 1


def test_busway_panel_no_prior_run_when_empty():
    out = busway_panel("none", InMemoryEvidenceStore())
    assert out["no_prior_run"] is True


def test_busway_panel_failed_when_megger_below_acceptance():
    s = InMemoryEvidenceStore()
    s.put("mtg-A", "busway_megger",
          {"completed_at": "2026-04-01", "passed": False, "min_megohm": 50})
    s.put("mtg-A", "busway_hipot",
          {"completed_at": "2026-04-01", "passed": True, "peak_leakage_ma": 0.0008})
    s.put("mtg-A", "busway_dlro",
          {"completed_at": "2026-04-01", "passed": True, "max_joint_uohm": 19.5})
    s.put("mtg-A", "busway_manual", {"completed_at": "2026-04-01", "passed": True,
                                       "signoffs": {"visual": {"label": "Visual",
                                                                "passed": True,
                                                                "operator": "j"}}})
    out = busway_panel("mtg-A", s)
    assert out["passed"] is False


# ---------------------------------------------------------------------------
# build_panel_by_id (generic /equipment/:id endpoint)
# ---------------------------------------------------------------------------


def test_build_panel_by_id_attaches_panel_layout_from_config():
    s = _store_with_runs({"cable_hipot": [
        {"completed_at": "2026-04-01", "passed": True,
         "peak_leakage_ma": 0.08, "trace": [], "target_kv": 11.04},
    ]}, device_id="mv-main-A")
    cfg = {
        "device_type_slug": "cable-15kv-xlpe",
        "category": "cable",
        "manufacturer": "Generic",
        "model": "15 kV XLPE",
        "panel": {"header": {"title_template": "Cable · {device_id}"}},
    }
    out = build_panel_by_id("mv-main-A", s, {"mv-main-A": cfg})
    assert out["panel_layout"]["header"]["title_template"] == "Cable · {device_id}"
    assert out["category"] == "cable"
    assert out["device_type_slug"] == "cable-15kv-xlpe"


def test_build_panel_by_id_unknown_device_raises():
    with pytest.raises(KeyError, match="unknown device"):
        build_panel_by_id("nope", InMemoryEvidenceStore(), {})


def test_build_panel_attaches_panel_layout_when_config_supplied():
    s = _store_with_runs({"cable_hipot": [
        {"completed_at": "2026-04-01", "passed": True,
         "peak_leakage_ma": 0.08, "trace": [], "target_kv": 11.04},
    ]}, device_id="mv-main-A")
    cfg = {"device_type_slug": "cable-15kv-xlpe", "category": "cable",
           "panel": {"tiles": [{"label": "X"}]}}
    out = build_panel("hipot", "mv-main-A", s, config=cfg)
    assert out["panel_layout"]["tiles"][0]["label"] == "X"
