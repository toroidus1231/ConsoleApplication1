"""One-shot util: inject panel.layout into the remaining Config Context
JSON files. Keeps layouts as Python dicts so they're readable here, then
serialises into config/equipment/<slug>.json. Run once; deletes itself
mentally afterwards."""

from __future__ import annotations

import json
from pathlib import Path

CFG_DIR = Path(__file__).resolve().parent.parent / "config" / "equipment"


# Common busway panel — same layout for MTG 4000A, MTG 3200A, iMPB 1250A
BUSWAY_PANEL = {
    "header": {
        "title_template": "Busway · {device_id}",
        "crumb_template": "{manufacturer} {model} · NETA ATS-17 §7.4 · IEC 61439-6 acceptance",
        "status_field": "passed",
        "status_labels": {"true": "ACCEPTED", "false": "DEFICIENCY"},
    },
    "tiles": [
        {"label": "Manufacturer / Model", "value_template": "{manufacturer} {model}"},
        {"label": "Rated current", "value_field": "megger.rated_amps", "format": "{:.0f} A"},
        {"label": "Voltage class", "value_field": "megger.voltage_class_v", "format": "{:.0f} V"},
        {"label": "Automated steps",
         "value_template": "{automation_summary.automated_steps} of {automation_summary.automated_steps}+{automation_summary.manual_steps}"},
        {"label": "Automation",
         "value_field": "automation_summary.automated_pct", "format": "{:.0f} %",
         "color": "var(--accent)"},
        {"label": "Overall",
         "value_template": "{passed}",
         "color_field": "passed",
         "color_labels": {"true": "var(--pass)", "false": "var(--fail)"}},
    ],
    "cards": [
        {
            "kind": "table",
            "title": "Insulation resistance · Megger MIT525 · 1000 VDC × 60 s",
            "data_path": "megger.readings",
            "columns": [
                {"label": "Pair", "field": "from", "mono": True},
                {"label": "→",     "field": "to",   "mono": True},
                {"label": "Resistance (MΩ)", "field": "megohm", "format": "{:.0f} MΩ", "mono": True},
                {"label": "Status", "kind": "badge", "field": "passed",
                 "labels": {"true": "pass", "false": "fail"}},
            ],
        },
        {
            "kind": "line_chart",
            "title": "DC withstand · Vitrek 95X · 2.5 kV × 60 s",
            "height": 240,
            "data_path": "hipot.trace",
            "decimate": 4,
            "x": {"field": "t_seconds", "tick_suffix": "s"},
            "y_axes": [
                {"id": "v", "label": "kV",
                 "domain_max_field": "hipot.target_kv", "domain_max_factor": 1.1},
                {"id": "i", "label": "mA", "orientation": "right",
                 "domain_max_field": "hipot.leakage_trip_ma", "domain_max_factor": 1.2},
            ],
            "lines": [
                {"y_axis_id": "v", "field": "voltage_kv", "name": "V", "stroke": "#58a6ff"},
                {"y_axis_id": "i", "field": "leakage_ma", "name": "I", "stroke": "#f0a05c"},
            ],
            "reference_lines": [
                {"y_axis_id": "i", "y_value_field": "hipot.leakage_trip_ma",
                 "stroke": "var(--fail)", "stroke_dasharray": "4 4", "label": "trip"},
            ],
        },
        {
            "kind": "bar_chart",
            "title": "Joint resistance · Megger DLRO10X · 10 A DC",
            "height": 220,
            "data_path": "dlro.joints",
            "x": {"field": "joint"},
            "y": {"field": "max_uohm", "label": "µΩ"},
            "acceptance_field": "dlro.joint_acceptance_uohm",
            "bar_default_color": "#58a6ff",
        },
        {
            "kind": "signoff_table",
            "title": "Operator sign-offs · IEC 61439-6 §10.2 / NETA §7.4",
            "data_path": "manual_signoffs",
        },
    ],
}


# UPS panel (APC Symmetra MW)
UPS_PANEL = {
    "header": {
        "title_template": "UPS · {device_id}",
        "crumb_template": "{manufacturer} {model} · {rated_kw} kW · IEC 62040-3 Class 3",
        "status_field": "passed",
        "status_labels": {"true": "ONLINE", "false": "FAULT"},
    },
    "tiles": [
        {"label": "Input V",   "value_template": "476.9 V"},
        {"label": "Output V",  "value_template": "480.0 V"},
        {"label": "Battery",   "value_field": "battery_pct",  "format": "{:.1f} %",
         "color": "var(--pass)"},
        {"label": "Runtime",   "value_field": "runtime_min", "format": "{:.1f} min"},
        {"label": "Switchover","value_field": "switchover_ms", "format": "{:.1f} ms"},
        {"label": "Weak cells","value_template": "{weak_cells.length}",
         "color_field": "passed",
         "color_labels": {"true": "var(--pass)", "false": "var(--fail)"}},
    ],
    "cards": [
        {
            "kind": "line_chart",
            "title": "Battery transfer waveform · last commissioning test (IEEE 1184)",
            "height": 280,
            "data_path": "transfer_waveform_60s",
            "decimate": 6,
            "x": {"field": "t_ms", "tick_suffix": "ms"},
            "y_axes": [{"id": "v", "label": "V_out", "domain_min": 450,
                        "domain_max_factor": 1, "domain_max_field": "min_voltage_v",
                        "domain_min_value": 450}],
            "lines": [{"y_axis_id": "v", "field": "output_voltage", "stroke": "#58a6ff",
                       "name": "V_out"}],
        },
        {
            "kind": "bar_chart",
            "title": "Battery cells · 240 cells",
            "height": 220,
            "data_path": "all_cells",
            "x": {"field": "id"},
            "y": {"field": "voltage", "label": "V"},
            "bar_default_color": "#58a6ff",
            "bar_color_logic": [
                {"if_field": "ok", "if_value": False, "color": "var(--fail)"},
            ],
        },
        {
            "kind": "table",
            "title": "Transfer history",
            "data_path": "transfer_history",
            "columns": [
                {"label": "Days ago",   "field": "date_offset_days", "mono": True},
                {"label": "Min V",      "field": "min_voltage_v",    "format": "{:.1f} V",  "mono": True},
                {"label": "Switchover", "field": "switchover_ms",    "format": "{:.1f} ms", "mono": True},
                {"label": "Status",     "kind": "badge", "field": "passed",
                 "labels": {"true": "passed", "false": "failed"}},
            ],
        },
    ],
}


# ATS panel (ASCO 7000)
ATS_PANEL = {
    "header": {
        "title_template": "ATS · {device_id}",
        "crumb_template": "{manufacturer} {model} · {rated_amps} A · NFPA 110 Type 10",
        "status_field": "overall_passed",
        "status_labels": {"true": "TRANSFER TEST PASSED", "false": "DEFICIENCY"},
    },
    "tiles": [],
    "cards": [
        {
            "kind": "table",
            "title": "Sequence of operations · last transfer test",
            "data_path": "sequence",
            "columns": [
                {"label": "Step",     "field": "step"},
                {"label": "Expected", "field": "expected"},
                {"label": "Actual",   "field": "actual"},
                {"label": "Status",   "kind": "badge", "field": "passed",
                 "labels": {"true": "passed", "false": "failed"}},
            ],
        },
        {
            "kind": "table",
            "title": "Downstream UPS verification (spec §4.3)",
            "data_path": "downstream_ups",
            "columns": [
                {"label": "UPS",     "field": "id", "mono": True},
                {"label": "Site",    "field": "site"},
                {"label": "Min V during", "field": "min_input_v_during", "format": "{:.0f} V", "mono": True},
                {"label": "Battery after", "field": "battery_pct_after", "format": "{:.1f} %", "mono": True},
                {"label": "Stayed online", "kind": "badge", "field": "stayed_online",
                 "labels": {"true": "yes", "false": "no"}},
            ],
        },
        {
            "kind": "ats_pipeline",
            "title": "Transfer timeline",
            "data_path": "sequence",
        },
    ],
}


# Transformer panel
XFMR_PANEL = {
    "header": {
        "title_template": "Transformer · {device_id}",
        "crumb_template": "{rated_kva} kVA · {voltage_class} · {vector_group}",
        "status_field": "passed",
        "status_labels": {"true": "ACCEPTED", "false": "ACTIVE FAULT"},
    },
    "tiles": [
        {"label": "C₂H₂",  "value_field": "current_gases.c2h2", "format": "{:.2f} ppm",
         "color_field": "fault_state",
         "color_labels": {"healthy": "var(--pass)",
                          "active_arcing": "var(--fail)",
                          "partial_discharge": "var(--major)",
                          "overheating": "var(--major)"}},
        {"label": "H₂",    "value_field": "current_gases.h2",   "format": "{:.1f} ppm"},
        {"label": "CH₄",   "value_field": "current_gases.ch4",  "format": "{:.1f} ppm"},
        {"label": "C₂H₄",  "value_field": "current_gases.c2h4", "format": "{:.1f} ppm"},
        {"label": "CO₂",   "value_field": "current_gases.co2",  "format": "{:.0f} ppm"},
        {"label": "PI",    "value_field": "polarization_index.value", "format": "{:.2f}",
         "color_field": "polarization_index.passed",
         "color_labels": {"true": "var(--pass)", "false": "var(--fail)"}},
    ],
    "cards": [
        {
            "kind": "duval",
            "title": "Duval triangle (DGA fault diagnosis · IEC 60599)",
            "data_path": "current_gases",
        },
        {
            "kind": "line_chart",
            "title": "DGA gas analysis · 14-week trend (IEEE C57.104)",
            "height": 260,
            "data_path": "dga_history",
            "x": {"field": "days_ago"},
            "y_axes": [{"id": "ppm", "label": "ppm"}],
            "lines": [
                {"y_axis_id": "ppm", "field": "h2",   "stroke": "#58a6ff", "name": "H₂"},
                {"y_axis_id": "ppm", "field": "ch4",  "stroke": "#7ee787", "name": "CH₄"},
                {"y_axis_id": "ppm", "field": "c2h4", "stroke": "#f0a05c", "name": "C₂H₄"},
                {"y_axis_id": "ppm", "field": "c2h2", "stroke": "#f85149", "name": "C₂H₂"},
            ],
        },
        {
            "kind": "table",
            "title": "Transformer turns ratio (IEEE C57.12.90)",
            "data_path": "ttr",
            "columns": [
                {"label": "Tap",       "field": "tap",      "mono": True},
                {"label": "Phase",     "field": "phase",    "mono": True},
                {"label": "Expected",  "field": "expected", "format": "{:.4f}", "mono": True},
                {"label": "Measured",  "field": "measured", "format": "{:.4f}", "mono": True},
                {"label": "Deviation", "field": "deviation_pct", "format": "{:.3f} %", "mono": True},
                {"label": "Status",    "kind": "badge", "field": "passed",
                 "labels": {"true": "passed", "false": "failed"}},
            ],
        },
        {
            "kind": "pi_curve",
            "data_path": "polarization_index",
        },
        {
            "kind": "table",
            "title": "Hipot history (NETA §7.2)",
            "data_path": "hipot_history",
            "columns": [
                {"label": "Days ago", "field": "date_offset_days", "mono": True},
                {"label": "Voltage",  "field": "voltage_kv", "format": "{:.2f} kV", "mono": True},
                {"label": "Leakage",  "field": "leakage_ma", "format": "{:.3f} mA", "mono": True},
                {"label": "Status",   "kind": "badge", "field": "passed",
                 "labels": {"true": "passed", "false": "failed"}},
            ],
        },
    ],
}


# Generator panel
GEN_PANEL = {
    "header": {
        "title_template": "Generator · {device_id}",
        "crumb_template": "{manufacturer} {model} · {rated_kw} kW · NFPA 110 Type 10",
        "status_field": "passed_overall",
        "status_labels": {"true": "READY", "false": "FAULT"},
    },
    "tiles": [
        {"label": "Rated power",      "value_field": "rated_kw", "format": "{:.0f} kW"},
        {"label": "Operating hours",  "value_field": "operating_hours", "format": "{:.0f} h"},
        {"label": "Manufacturer",     "value_field": "manufacturer"},
        {"label": "Model",            "value_field": "model"},
    ],
    "cards": [
        {
            "kind": "line_chart",
            "title": "Load-bank trace · 4-hour graduated test (NFPA 110 §8.4.2)",
            "height": 320,
            "data_path": "loadbank_trace",
            "decimate": 4,
            "x": {"field": "t_seconds", "tick_suffix": "s"},
            "y_axes": [
                {"id": "kw",   "label": "kW",
                 "domain_max_field": "rated_kw", "domain_max_factor": 1.1},
                {"id": "temp", "label": "°C", "orientation": "right",
                 "domain_max_field": "rated_kw", "domain_max_factor": 0.1},
            ],
            "lines": [
                {"y_axis_id": "kw",   "field": "kw",            "stroke": "#58a6ff", "name": "Real Power kW"},
                {"y_axis_id": "temp", "field": "oil_temp_c",    "stroke": "#f0a05c", "name": "Oil °C"},
                {"y_axis_id": "temp", "field": "coolant_temp_c","stroke": "#7ee787", "name": "Coolant °C"},
            ],
        },
        {
            "kind": "table",
            "title": "Black-start sequence · last automatic test (NFPA 110)",
            "data_path": "startup_sequence",
            "columns": [
                {"label": "Step",     "field": "step"},
                {"label": "Expected", "field": "expected"},
                {"label": "Actual",   "field": "actual",   "mono": True},
                {"label": "Status",   "kind": "badge", "field": "passed",
                 "labels": {"true": "passed", "false": "failed"}},
            ],
        },
    ],
}


# Apply
PANELS = {
    "vertiv-mtg-4000a.json":   BUSWAY_PANEL,
    "vertiv-mtg-3200a.json":   BUSWAY_PANEL,
    "vertiv-impb-1250a.json":  BUSWAY_PANEL,
    "apc-symmetra-mw.json":    UPS_PANEL,
    "asco-7000.json":          ATS_PANEL,
    "oil-xfmr-2500kva.json":   XFMR_PANEL,
    "cat-3516b.json":          GEN_PANEL,
}

for filename, panel in PANELS.items():
    path = CFG_DIR / filename
    with open(path) as f:
        cfg = json.load(f)
    cfg["panel"] = panel
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"  wrote panel layout to {filename}")
