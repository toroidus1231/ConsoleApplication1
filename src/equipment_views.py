"""Equipment-panel views.

The API endpoint /api/v1/equipment/{kind}/{device_id} delegates here.
Each view function reads the latest run plus historical runs from the
EvidenceStore (which the test engine writes to) and assembles the
panel-shaped record the React HMI expects.

Returns {"no_prior_run": True, ...} when nothing has been recorded for
that device + test. The panel renders a "no run on file" state instead
of inventing data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .evidence_store import EvidenceStore


KIND_TO_TEST_NAME = {
    "hipot": "cable_hipot",
    "cable": "cable_hipot",
}


def _parse_iso(s: str) -> datetime:
    # Accept both "...Z" and offset-naive ISO strings.
    if s.endswith("Z"):
        s = s[:-1]
    return datetime.fromisoformat(s)


def _no_prior_run(device_id: str, kind: str, test_name: str) -> dict:
    return {
        "device_id": device_id,
        "kind": kind,
        "test_name": test_name,
        "no_prior_run": True,
    }


def hipot_panel(device_id: str, store: EvidenceStore) -> dict[str, Any]:
    runs = store.list_runs(device_id, "cable_hipot")
    if not runs:
        return _no_prior_run(device_id, "hipot", "cable_hipot")

    latest = runs[-1]
    latest_at = _parse_iso(latest["completed_at"])
    history = []
    for r in runs[:-1]:
        delta_days = (_parse_iso(r["completed_at"]) - latest_at).days
        history.append({
            "date_offset_days": delta_days,
            "max_leakage_ma": r["peak_leakage_ma"],
            "passed": r["passed"],
        })
    history.sort(key=lambda h: h["date_offset_days"])
    panel = dict(latest)
    panel["history"] = history
    return panel


def transformer_panel(device_id: str, store: EvidenceStore) -> dict[str, Any]:
    runs = store.list_runs(device_id, "transformer_commissioning")
    if not runs:
        return _no_prior_run(device_id, "transformer", "transformer_commissioning")
    return dict(runs[-1])


def generator_panel(device_id: str, store: EvidenceStore) -> dict[str, Any]:
    runs = store.list_runs(device_id, "generator_loadbank")
    if not runs:
        return _no_prior_run(device_id, "generator", "generator_loadbank")
    return dict(runs[-1])


def ups_panel(device_id: str, store: EvidenceStore) -> dict[str, Any]:
    runs = store.list_runs(device_id, "ups_battery_transfer")
    if not runs:
        return _no_prior_run(device_id, "ups", "ups_battery_transfer")

    latest = runs[-1]
    latest_at = _parse_iso(latest["completed_at"])
    transfer_history = []
    for r in runs[:-1]:
        delta_days = (_parse_iso(r["completed_at"]) - latest_at).days
        transfer_history.append({
            "date_offset_days": delta_days,
            "passed": r["passed"],
            "min_voltage_v": r["min_voltage_v"],
            "switchover_ms": r["switchover_ms"],
        })
    transfer_history.sort(key=lambda h: h["date_offset_days"])
    panel = dict(latest)
    panel["transfer_history"] = transfer_history
    return panel


def ats_panel(device_id: str, store: EvidenceStore) -> dict[str, Any]:
    runs = store.list_runs(device_id, "ats_transfer")
    if not runs:
        return _no_prior_run(device_id, "ats", "ats_transfer")
    return dict(runs[-1])


def busway_panel(device_id: str, store: EvidenceStore) -> dict[str, Any]:
    """Aggregate the three instrument-driven busway tests + manual
    sign-offs into one panel record. Manual sign-offs are stored under
    test_name='busway_manual' just like the instrument runs, so the
    panel reads everything via one EvidenceStore interface."""
    megger_runs = store.list_runs(device_id, "busway_megger")
    hipot_runs  = store.list_runs(device_id, "busway_hipot")
    dlro_runs   = store.list_runs(device_id, "busway_dlro")
    manual_runs = store.list_runs(device_id, "busway_manual")
    if not (megger_runs or hipot_runs or dlro_runs):
        return _no_prior_run(device_id, "busway", "busway_acceptance")

    def _trend(runs, key):
        if not runs:
            return []
        latest_at = _parse_iso(runs[-1]["completed_at"])
        out = []
        for r in runs[:-1]:
            delta = (_parse_iso(r["completed_at"]) - latest_at).days
            out.append({"date_offset_days": delta,
                        "value": r.get(key),
                        "passed": r["passed"]})
        out.sort(key=lambda x: x["date_offset_days"])
        return out

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


_KIND_DISPATCH = {
    "hipot": hipot_panel,
    "cable": hipot_panel,
    "transformer": transformer_panel,
    "xfmr": transformer_panel,
    "generator": generator_panel,
    "gen": generator_panel,
    "ups": ups_panel,
    "ats": ats_panel,
    "busway": busway_panel,
}


def build_panel(kind: str, device_id: str, store: EvidenceStore) -> dict[str, Any]:
    fn = _KIND_DISPATCH.get(kind)
    if fn is None:
        raise KeyError(f"no view registered for kind: {kind}")
    return fn(device_id, store)
