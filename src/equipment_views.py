"""Equipment-panel views.

The /api/v1/equipment/{kind}/{device_id} endpoint dispatches here.
Dispatch is by `category` field on the device's Config Context.

Each panel function returns the assembled record + the panel.layout
block from the Config Context. The frontend renders the layout
generically — adding a new family is a new <slug>.json with a panel
layout, no React code.

Returns {"no_prior_run": True, ...} when nothing has been recorded.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .evidence_store import EvidenceStore


# Aliases the URL can use → canonical category on the Config Context
URL_KIND_TO_CATEGORY = {
    "hipot": "cable",
    "cable": "cable",
    "transformer": "transformer",
    "xfmr": "transformer",
    "generator": "generator",
    "gen": "generator",
    "gens": "generator",
    "ups": "ups",
    "ats": "ats",
    "busway": "busway",
}


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1]
    return datetime.fromisoformat(s)


def _no_prior_run(device_id: str, category: str, test_name: str) -> dict:
    return {
        "device_id": device_id,
        "kind": category,
        "test_name": test_name,
        "no_prior_run": True,
    }


def _trend(runs: list, key: str) -> list:
    if not runs:
        return []
    latest_at = _parse_iso(runs[-1]["completed_at"])
    out = []
    for r in runs[:-1]:
        delta = (_parse_iso(r["completed_at"]) - latest_at).days
        out.append({"date_offset_days": delta, "value": r.get(key),
                    "passed": r["passed"]})
    out.sort(key=lambda x: x["date_offset_days"])
    return out


# ---------------------------------------------------------------------------
# Per-category panel builders
# ---------------------------------------------------------------------------


def cable_panel(device_id: str, store: EvidenceStore) -> dict:
    runs = store.list_runs(device_id, "cable_hipot")
    if not runs:
        return _no_prior_run(device_id, "cable", "cable_hipot")
    latest = runs[-1]
    history = []
    for r in runs[:-1]:
        delta = (_parse_iso(r["completed_at"]) - _parse_iso(latest["completed_at"])).days
        history.append({"date_offset_days": delta,
                        "max_leakage_ma": r["peak_leakage_ma"],
                        "passed": r["passed"]})
    history.sort(key=lambda h: h["date_offset_days"])
    panel = dict(latest)
    panel["history"] = history
    return panel


def transformer_panel(device_id: str, store: EvidenceStore) -> dict:
    """Aggregate the four transformer tests (DGA, TTR, PI, hipot history)."""
    dga_runs    = store.list_runs(device_id, "dga_initial_sample")
    ttr_runs    = store.list_runs(device_id, "transformer_turns_ratio")
    pi_runs     = store.list_runs(device_id, "polarization_index")
    hipot_runs  = store.list_runs(device_id, "transformer_hipot")
    if not (dga_runs or ttr_runs or pi_runs or hipot_runs):
        return _no_prior_run(device_id, "transformer", "transformer_commissioning")
    dga = dga_runs[-1] if dga_runs else {}
    ttr = ttr_runs[-1] if ttr_runs else {}
    pi  = pi_runs[-1] if pi_runs else {}
    hp  = hipot_runs[-1] if hipot_runs else {}
    return {
        "device_id": device_id,
        "rated_kva": 2500,
        "voltage_class": "13.8 kV / 480 V",
        "vector_group": "Dyn1",
        "fault_state": dga.get("fault_state", "healthy"),
        "current_gases": dga.get("current_gases", {}),
        "dga_history": dga.get("dga_history", []),
        "ttr": ttr.get("ttr", []),
        "polarization_index": pi.get("polarization_index", {"value": 0, "curve": [], "passed": False}),
        "hipot_history": hp.get("hipot_history", []),
        "passed": all(r.get("passed", True) for r in (dga, ttr, pi, hp) if r),
    }


def generator_panel(device_id: str, store: EvidenceStore) -> dict:
    runs = store.list_runs(device_id, "generator_load_bank")
    if not runs:
        return _no_prior_run(device_id, "generator", "generator_loadbank")
    return dict(runs[-1])


def ups_panel(device_id: str, store: EvidenceStore) -> dict:
    runs = store.list_runs(device_id, "ups_battery_transfer")
    if not runs:
        return _no_prior_run(device_id, "ups", "ups_battery_transfer")
    latest = runs[-1]
    transfer_history = []
    for r in runs[:-1]:
        delta = (_parse_iso(r["completed_at"]) - _parse_iso(latest["completed_at"])).days
        transfer_history.append({"date_offset_days": delta,
                                 "passed": r["passed"],
                                 "min_voltage_v": r["min_voltage_v"],
                                 "switchover_ms": r["switchover_ms"]})
    transfer_history.sort(key=lambda h: h["date_offset_days"])
    panel = dict(latest)
    panel["transfer_history"] = transfer_history
    return panel


def ats_panel(device_id: str, store: EvidenceStore) -> dict:
    runs = store.list_runs(device_id, "ats_transfer")
    if not runs:
        return _no_prior_run(device_id, "ats", "ats_transfer")
    return dict(runs[-1])


def busway_panel(device_id: str, store: EvidenceStore) -> dict:
    megger_runs = store.list_runs(device_id, "busway_megger")
    hipot_runs  = store.list_runs(device_id, "busway_hipot")
    dlro_runs   = store.list_runs(device_id, "busway_dlro")
    manual_runs = store.list_runs(device_id, "busway_manual")
    if not (megger_runs or hipot_runs or dlro_runs):
        return _no_prior_run(device_id, "busway", "busway_acceptance")

    megger = megger_runs[-1] if megger_runs else None
    hipot  = hipot_runs[-1]  if hipot_runs  else None
    dlro   = dlro_runs[-1]   if dlro_runs   else None
    manual = manual_runs[-1] if manual_runs else None
    signoffs = (manual or {}).get("signoffs", {}) if manual else {}

    instrument_passed = all(r["passed"] for r in (megger, hipot, dlro) if r)
    manual_passed = all(s["passed"] for s in signoffs.values()) if signoffs else False
    auto_count = sum(1 for r in (megger, hipot, dlro) if r)
    manual_count = len(signoffs)
    total = auto_count + manual_count
    return {
        "device_id": device_id,
        "kind": "busway",
        "megger": megger,
        "megger_history": _trend(megger_runs, "min_megohm"),
        "hipot": hipot,
        "hipot_history": _trend(hipot_runs, "peak_leakage_ma"),
        "dlro": dlro,
        "dlro_history": _trend(dlro_runs, "max_joint_uohm"),
        "manual_signoffs": signoffs,
        "passed": instrument_passed and manual_passed,
        "automation_summary": {
            "automated_steps": auto_count,
            "manual_steps": manual_count,
            "automated_pct": round(auto_count / total * 100, 1) if total else 0.0,
        },
    }


def relay_panel(device_id: str, store: EvidenceStore) -> dict:
    """Aggregate the two protective-relay tests (secondary + primary
    injection). Either or both may be absent if a particular bay only
    requires one. Returns the latest of each plus a combined verdict.
    """
    sec_runs  = store.list_runs(device_id, "sel_secondary_injection")
    prim_runs = store.list_runs(device_id, "sel_primary_injection")
    if not (sec_runs or prim_runs):
        return _no_prior_run(device_id, "relay", "sel_secondary_injection")
    latest_sec  = sec_runs[-1]  if sec_runs  else None
    latest_prim = prim_runs[-1] if prim_runs else None
    primary = latest_sec or latest_prim
    panel = dict(primary)
    panel["secondary_injection"] = latest_sec
    panel["primary_injection"]   = latest_prim
    if latest_sec and latest_prim:
        panel["passed"] = bool(latest_sec.get("passed")) and bool(latest_prim.get("passed"))
    return panel


_CATEGORY_DISPATCH = {
    "cable": cable_panel,
    "transformer": transformer_panel,
    "generator": generator_panel,
    "ups": ups_panel,
    "ats": ats_panel,
    "busway": busway_panel,
    "relay": relay_panel,
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_panel(kind: str, device_id: str, store: EvidenceStore,
                config: dict | None = None) -> dict:
    """Resolve URL kind → category → category aggregator. Attach the
    panel.layout block from the device's Config Context for the
    frontend's generic renderer."""
    category = URL_KIND_TO_CATEGORY.get(kind)
    if category is None:
        raise KeyError(f"unknown URL kind: {kind!r}")
    fn = _CATEGORY_DISPATCH.get(category)
    if fn is None:
        raise KeyError(f"no panel registered for category: {category!r}")
    record = fn(device_id, store)
    if config is not None and "panel" in config:
        record["panel_layout"] = config["panel"]
        record["category"] = config["category"]
        record["device_type_slug"] = config["device_type_slug"]
        record["manufacturer"] = config.get("manufacturer", record.get("manufacturer"))
        record["model"] = config.get("model", record.get("model"))
    return record


def build_panel_by_id(device_id: str, store: EvidenceStore,
                      effective_configs: dict[str, dict]) -> dict:
    """For the new generic /api/v1/equipment/{device_id} route — looks up
    the device's Config Context and dispatches by its category."""
    cfg = effective_configs.get(device_id)
    if cfg is None:
        raise KeyError(f"unknown device: {device_id!r}")
    category = cfg["category"]
    fn = _CATEGORY_DISPATCH.get(category)
    if fn is None:
        raise KeyError(f"no panel for category: {category!r}")
    record = fn(device_id, store)
    record["category"] = category
    record["device_type_slug"] = cfg["device_type_slug"]
    record["manufacturer"] = cfg.get("manufacturer", record.get("manufacturer"))
    record["model"] = cfg.get("model", record.get("model"))
    if "panel" in cfg:
        record["panel_layout"] = cfg["panel"]
    return record
