"""Tests for src/topology/dynamic_sld.py — layered DAG SLD layout."""

from __future__ import annotations

import pytest

from src.topology.dynamic_sld import (
    SLDLayout, SLDNode, SLDWire, compute_layout, layout_to_dict,
    _assign_levels,
)


# ---------------------------------------------------------------------------
# _assign_levels
# ---------------------------------------------------------------------------


def test_levels_root_is_zero():
    levels = _assign_levels(["a"], {})
    assert levels == {"a": 0}


def test_levels_single_chain():
    # b ← a, c ← b
    parents = {"b": ["a"], "c": ["b"]}
    levels = _assign_levels(["a", "b", "c"], parents)
    assert levels == {"a": 0, "b": 1, "c": 2}


def test_levels_diamond():
    # b ← a, c ← a, d ← b, d ← c
    parents = {"b": ["a"], "c": ["a"], "d": ["b", "c"]}
    levels = _assign_levels(["a", "b", "c", "d"], parents)
    assert levels["a"] == 0
    assert levels["b"] == 1
    assert levels["c"] == 1
    assert levels["d"] == 2


def test_levels_disconnected_devices_get_zero():
    levels = _assign_levels(["a", "b", "c"], {"b": ["a"]})
    assert levels["c"] == 0


def test_levels_breaks_cycles():
    # cyclic: a ← b ← a (defensive — power graphs shouldn't cycle)
    parents = {"a": ["b"], "b": ["a"]}
    levels = _assign_levels(["a", "b"], parents)
    # Both should resolve, no infinite recursion
    assert "a" in levels
    assert "b" in levels


# ---------------------------------------------------------------------------
# compute_layout
# ---------------------------------------------------------------------------


def test_compute_layout_empty_devices():
    layout = compute_layout([], [])
    assert layout.nodes == {}
    assert layout.wires == []


def test_compute_layout_assigns_positions():
    devices = [
        {"device_id": "util-A", "device_type_slug": "utility-mv", "name": "Util A"},
        {"device_id": "mv-A", "device_type_slug": "schneider-gma-1200a", "name": "MV A"},
    ]
    connections = [("mv-A", "util-A")]
    layout = compute_layout(devices, connections)
    assert "util-A" in layout.nodes
    assert "mv-A" in layout.nodes
    assert layout.nodes["util-A"].level == 0
    assert layout.nodes["mv-A"].level == 1
    assert layout.nodes["util-A"].y < layout.nodes["mv-A"].y


def test_compute_layout_horizontal_spacing_within_level():
    devices = [
        {"device_id": "util-A", "device_type_slug": "utility-mv", "name": "A"},
        {"device_id": "util-B", "device_type_slug": "utility-mv", "name": "B"},
    ]
    layout = compute_layout(devices, [])
    a, b = layout.nodes["util-A"], layout.nodes["util-B"]
    assert a.level == 0 and b.level == 0
    assert a.y == b.y
    assert a.x != b.x


def test_compute_layout_emits_wires_for_connections():
    devices = [
        {"device_id": "p", "device_type_slug": "x", "name": "p"},
        {"device_id": "c1", "device_type_slug": "y", "name": "c1"},
        {"device_id": "c2", "device_type_slug": "y", "name": "c2"},
    ]
    layout = compute_layout(devices, [("c1", "p"), ("c2", "p")])
    assert len(layout.wires) == 2
    parents_of = {w.to_id: w.from_id for w in layout.wires}
    assert parents_of["c1"] == "p"
    assert parents_of["c2"] == "p"


def test_compute_layout_drops_dangling_edges():
    devices = [{"device_id": "a", "device_type_slug": "x", "name": "a"}]
    layout = compute_layout(devices, [("a", "missing-parent")])
    # Edge has a missing parent → dropped, a treated as root
    assert layout.nodes["a"].level == 0
    assert layout.wires == []


def test_compute_layout_within_level_sorted_deterministically():
    devices = [
        {"device_id": "z", "device_type_slug": "alpha", "name": "z"},
        {"device_id": "a", "device_type_slug": "beta", "name": "a"},
        {"device_id": "m", "device_type_slug": "alpha", "name": "m"},
    ]
    layout1 = compute_layout(devices, [])
    layout2 = compute_layout(list(reversed(devices)), [])
    # Same x-coordinates regardless of input order (sorted by slug, then id)
    for did in ("z", "a", "m"):
        assert layout1.nodes[did].x == layout2.nodes[did].x


def test_compute_layout_500_node_diamond():
    # Stress: 1 utility, 4 MV mains, 100 LV breakers under each main
    devices = [{"device_id": "util", "device_type_slug": "utility", "name": "U"}]
    connections = []
    for mv_idx in range(4):
        mv = f"mv-{mv_idx}"
        devices.append({"device_id": mv, "device_type_slug": "mv-breaker",
                        "name": mv})
        connections.append((mv, "util"))
        for lv_idx in range(100):
            lv = f"lv-{mv_idx}-{lv_idx}"
            devices.append({"device_id": lv, "device_type_slug": "lv-breaker",
                            "name": lv})
            connections.append((lv, mv))
    layout = compute_layout(devices, connections)
    assert layout.levels == 3
    assert len(layout.nodes) == 1 + 4 + 400
    assert len(layout.wires) == 4 + 400


# ---------------------------------------------------------------------------
# layout_to_dict
# ---------------------------------------------------------------------------


def test_layout_to_dict_serialises_nodes_and_wires():
    devices = [
        {"device_id": "p", "device_type_slug": "x", "name": "p"},
        {"device_id": "c", "device_type_slug": "y", "name": "c"},
    ]
    layout = compute_layout(devices, [("c", "p")])
    out = layout_to_dict(layout)
    assert {"width", "height", "levels", "nodes", "wires"}.issubset(out.keys())
    assert len(out["nodes"]) == 2
    assert out["nodes"][0].keys() == {"device_id", "device_type_slug", "name",
                                       "x", "y", "level"}
    assert out["wires"] == [{"from": "p", "to": "c", "live": True}]


def test_layout_to_dict_empty_layout():
    out = layout_to_dict(SLDLayout())
    assert out["nodes"] == [] and out["wires"] == []
    assert out["levels"] == 0
