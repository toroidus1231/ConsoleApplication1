"""Per-device equipment specs in one place.

The same registry feeds:
- EvidenceStore seeding (historical runs are the spec evaluated at past dates)
- live telemetry (current readings are the spec evaluated at `now`)

Production replaces this with NetBox / BIM-driven config; demo populates
it inline in dev_server.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .equipment_models.ats import ATSSpec
from .equipment_models.busway import BuswaySpec
from .equipment_models.cable import CableSpec
from .equipment_models.generator import GeneratorSpec
from .equipment_models.transformer import TransformerSpec
from .equipment_models.ups import UPSSpec


@dataclass
class EquipmentRegistry:
    cables: dict[str, CableSpec] = field(default_factory=dict)
    transformers: dict[str, TransformerSpec] = field(default_factory=dict)
    generators: dict[str, GeneratorSpec] = field(default_factory=dict)
    upses: dict[str, UPSSpec] = field(default_factory=dict)
    ats_units: dict[str, ATSSpec] = field(default_factory=dict)
    busways: dict[str, BuswaySpec] = field(default_factory=dict)
