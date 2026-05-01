"""Tests for src/equipment_loader.py."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from src.equipment_loader import (
    load_device_type_configs, merge_instance_overrides,
)


def _write_config(tmp_path, slug, **fields):
    cfg = {"device_type_slug": slug, **fields}
    (tmp_path / "equipment").mkdir(exist_ok=True)
    p = tmp_path / "equipment" / f"{slug}.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    return p


def test_load_returns_empty_when_dir_missing(tmp_path):
    out = load_device_type_configs(tmp_path)
    assert out == {}


def test_load_indexes_by_slug(tmp_path):
    _write_config(tmp_path, "vertiv-mtg-4000a",
                  manufacturer="Vertiv", category="busway")
    _write_config(tmp_path, "cat-3516b",
                  manufacturer="Caterpillar", category="generator")
    out = load_device_type_configs(tmp_path)
    assert set(out.keys()) == {"vertiv-mtg-4000a", "cat-3516b"}
    assert out["vertiv-mtg-4000a"]["manufacturer"] == "Vertiv"


def test_load_raises_when_slug_missing(tmp_path):
    (tmp_path / "equipment").mkdir()
    bad = tmp_path / "equipment" / "broken.json"
    bad.write_text(json.dumps({"manufacturer": "X"}))  # no slug
    with pytest.raises(ValueError, match="missing device_type_slug"):
        load_device_type_configs(tmp_path)


def test_merge_returns_deep_copy_when_no_overrides():
    base = {"device_type_slug": "x",
            "ageing_model": {"params": {"a": 1}}}
    merged = merge_instance_overrides(base, None)
    merged["ageing_model"]["params"]["a"] = 99
    assert base["ageing_model"]["params"]["a"] == 1


def test_merge_pushes_overrides_into_ageing_params():
    base = {"device_type_slug": "x",
            "ageing_model": {"params": {"install_year": 2020}}}
    merged = merge_instance_overrides(base, {"install_year": 2018,
                                              "fault_state": "active_arcing"})
    assert merged["ageing_model"]["params"]["install_year"] == 2018
    assert merged["ageing_model"]["params"]["fault_state"] == "active_arcing"


def test_merge_creates_ageing_model_if_missing():
    base = {"device_type_slug": "x"}
    merged = merge_instance_overrides(base, {"k": "v"})
    assert merged["ageing_model"]["params"]["k"] == "v"


def test_merge_pushes_length_m_into_ratings():
    base = {"device_type_slug": "x", "ratings": {"rated_amps": 4000}}
    merged = merge_instance_overrides(base, {"length_m": 42.0})
    assert merged["ratings"]["length_m"] == 42.0
    assert merged["ratings"]["rated_amps"] == 4000  # preserved


def test_merge_records_overrides_in_instance_namespace():
    base = {"device_type_slug": "x"}
    merged = merge_instance_overrides(base, {"fault_state": "arcing",
                                              "install_year": 2018})
    assert merged["instance"] == {"fault_state": "arcing",
                                   "install_year": 2018}
