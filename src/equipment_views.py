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


_KIND_DISPATCH = {
    "hipot": hipot_panel,
    "cable": hipot_panel,
    "transformer": transformer_panel,
    "xfmr": transformer_panel,
    "generator": generator_panel,
    "gen": generator_panel,
    "ups": ups_panel,
    "ats": ats_panel,
}


def build_panel(kind: str, device_id: str, store: EvidenceStore) -> dict[str, Any]:
    fn = _KIND_DISPATCH.get(kind)
    if fn is None:
        raise KeyError(f"no view registered for kind: {kind}")
    return fn(device_id, store)
