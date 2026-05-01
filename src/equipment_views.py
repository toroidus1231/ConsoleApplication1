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


def hipot_panel(device_id: str, store: EvidenceStore) -> dict[str, Any]:
    runs = store.list_runs(device_id, "cable_hipot")
    if not runs:
        return {
            "device_id": device_id,
            "kind": "hipot",
            "no_prior_run": True,
            "test_name": "cable_hipot",
        }

    latest = runs[-1]
    latest_at = _parse_iso(latest["completed_at"])
    history = []
    for r in runs[:-1]:
        run_at = _parse_iso(r["completed_at"])
        delta_days = (run_at - latest_at).days  # negative for older runs
        history.append({
            "date_offset_days": delta_days,
            "max_leakage_ma": r["peak_leakage_ma"],
            "passed": r["passed"],
        })
    history.sort(key=lambda h: h["date_offset_days"])

    panel = dict(latest)
    panel["history"] = history
    return panel


def build_panel(kind: str, device_id: str, store: EvidenceStore) -> dict[str, Any]:
    if kind in ("hipot", "cable"):
        return hipot_panel(device_id, store)
    raise KeyError(f"no view registered for kind: {kind}")
