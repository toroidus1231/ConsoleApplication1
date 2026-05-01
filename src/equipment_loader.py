"""Walk config/equipment/*.json and stuff each Config Context into a
NetBox-shaped dict keyed by device_type_slug.

In production, the same data flows through src/config_loader.py into
NetBox. For the demo we read straight from disk so we can stand the
platform up without a NetBox instance running.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_device_type_configs(config_dir: str | Path) -> dict[str, dict]:
    """Load every *.json under config_dir/equipment/ and return a dict
    mapping device_type_slug → ConfigContext."""
    base = Path(config_dir) / "equipment"
    out: dict[str, dict] = {}
    if not base.exists():
        return out
    for path in sorted(base.glob("*.json")):
        with open(path) as f:
            cfg = json.load(f)
        slug = cfg.get("device_type_slug")
        if not slug:
            raise ValueError(f"{path}: missing device_type_slug")
        out[slug] = cfg
    return out


def merge_instance_overrides(type_config: dict, overrides: dict | None) -> dict:
    """Merge per-device-instance overrides into a copy of the type-level
    Config Context. Recognised keys at the top level (install_year,
    install_month, length_m, fault_state, fault_severity, fault_onset,
    weak_cell_ids, initial_megohm, aging_per_year, …) override the
    matching key in the spec or in `ageing_model.params`.
    """
    cfg = json.loads(json.dumps(type_config))  # deep copy
    if not overrides:
        return cfg

    ageing = cfg.setdefault("ageing_model", {}).setdefault("params", {})
    ratings = cfg.setdefault("ratings", {})

    for k, v in overrides.items():
        # Push known instance keys into ageing_model.params; everything
        # else goes onto a top-level "instance" namespace.
        ageing[k] = v
        if k in {"length_m"}:
            ratings[k] = v
    cfg["instance"] = dict(overrides)
    return cfg
