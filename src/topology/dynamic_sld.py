"""Dynamic single-line diagram layout engine.

Today the React SLD has hardcoded x/y positions for every node and
hand-routed wires. That works for a 29-device demo facility but
doesn't scale to a 500 MW datacenter with hundreds of devices and
power-DAG topology that came out of BIM/NetBox.

This module computes a layout — { device_id: {x, y, level} } — from
two inputs:

  1. devices: list of {device_id, device_type_slug, name}
  2. connections: list of (powered_device_id, power_source_device_id)

The algorithm is a layered DAG (Sugiyama-style) cut down to what an
SLD needs:

  1. Topo-sort to assign each device a level (root utilities = 0,
     each level below adds 1).
  2. Within a level, sort by device_type_slug then device_id so the
     output is deterministic.
  3. Lay out each level horizontally, evenly spaced.
  4. Emit straight wires (level N → level N+1). The React renderer
     handles routing details.

This is good enough for facilities up to ~500 nodes. Bigger sites get
broken into per-substation views — same layout engine, narrower
device list.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class SLDNode:
    device_id: str
    device_type_slug: str
    name: str
    x: float
    y: float
    level: int


@dataclass
class SLDWire:
    from_id: str
    to_id: str
    live: bool = True


@dataclass
class SLDLayout:
    nodes: dict[str, SLDNode] = field(default_factory=dict)
    wires: list[SLDWire] = field(default_factory=list)
    width: float = 0
    height: float = 0
    levels: int = 0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_layout(devices: Iterable[dict],
                   connections: Iterable[tuple[str, str]],
                   *,
                   x_step: float = 200.0,
                   y_step: float = 130.0,
                   margin: float = 80.0) -> SLDLayout:
    """Build an SLD layout for the given devices + power-DAG edges.

    `connections` are (child, parent) edges — child is powered by parent.
    Roots (no parent) get level 0 and are placed at the top.
    """
    devs = {d["device_id"]: d for d in devices}
    if not devs:
        return SLDLayout()

    edges = [(c, p) for c, p in connections if c in devs and p in devs]
    parents = defaultdict(list)
    for c, p in edges:
        parents[c].append(p)

    levels = _assign_levels(devs.keys(), parents)
    by_level = defaultdict(list)
    for did, lv in levels.items():
        by_level[lv].append(did)

    # Stable order within a level: by slug then id.
    for lv in by_level:
        by_level[lv].sort(key=lambda d: (devs[d].get("device_type_slug", ""), d))

    nodes: dict[str, SLDNode] = {}
    max_per_level = max(len(v) for v in by_level.values()) if by_level else 1
    width = margin * 2 + max(0, max_per_level - 1) * x_step
    for lv, ids in by_level.items():
        n = len(ids)
        for i, did in enumerate(ids):
            # Center this row horizontally
            x = margin + (i + 0.5) * (width - 2 * margin) / max(1, n)
            y = margin + lv * y_step
            d = devs[did]
            nodes[did] = SLDNode(
                device_id=did,
                device_type_slug=d.get("device_type_slug", ""),
                name=d.get("name", did),
                x=round(x, 1), y=round(y, 1), level=lv,
            )

    wires = [SLDWire(from_id=p, to_id=c) for c, p in edges]
    n_levels = max(by_level.keys()) + 1 if by_level else 0
    height = margin * 2 + max(0, n_levels - 1) * y_step
    return SLDLayout(nodes=nodes, wires=wires,
                     width=round(width, 1), height=round(height, 1),
                     levels=n_levels)


def layout_to_dict(layout: SLDLayout) -> dict:
    """Serialise for the API response. Frontend reads this and renders
    nodes + wires generically without hardcoded positions."""
    return {
        "width": layout.width, "height": layout.height,
        "levels": layout.levels,
        "nodes": [
            {"device_id": n.device_id, "device_type_slug": n.device_type_slug,
             "name": n.name, "x": n.x, "y": n.y, "level": n.level}
            for n in layout.nodes.values()
        ],
        "wires": [
            {"from": w.from_id, "to": w.to_id, "live": w.live}
            for w in layout.wires
        ],
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _assign_levels(device_ids: Iterable[str],
                   parents: dict[str, list[str]]) -> dict[str, int]:
    """Assign a level to each device. Root = 0, child = max(parent) + 1.
    Devices with cyclic dependencies (shouldn't happen in a power graph
    but defensive) get the deepest level encountered. Disconnected
    devices (no parent and no child) get level 0."""
    levels: dict[str, int] = {}
    visiting: set[str] = set()

    def resolve(did: str) -> int:
        if did in levels:
            return levels[did]
        if did in visiting:
            return 0  # cycle break
        visiting.add(did)
        parent_list = parents.get(did, [])
        if not parent_list:
            lv = 0
        else:
            lv = 1 + max((resolve(p) for p in parent_list), default=-1)
        visiting.discard(did)
        levels[did] = lv
        return lv

    for did in device_ids:
        resolve(did)
    return levels
