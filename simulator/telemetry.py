"""Live telemetry generator for the digital twin.

Models a steady-state energized switchgear lineup with realistic per-second
electrical readings: bus voltages near nominal with small modulation,
phase currents tracking load, frequency drifting around 60Hz, breaker
positions per the test status, relay trip targets, and gen/UPS state.

The dev_server polls live_telemetry() once per second and exposes the
result via /api/v1/telemetry — the SLD page renders it directly.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field


@dataclass
class BreakerState:
    position: str  # closed | open | tripped | racked_out
    rating_amps: int
    spring_charged: bool = True
    contact_wear_pct: int = 0
    ops_count: int = 0


@dataclass
class BusReadings:
    voltage_ll_v: float       # line-line nominal
    current_a: float          # avg phase
    real_power_kw: float
    reactive_power_kvar: float
    power_factor: float
    frequency_hz: float
    thd_voltage_pct: float


@dataclass
class RelayState:
    """SEL-751-style relay: pickup status per element, last trip cause,
    last event report timestamp."""
    elements: dict[str, dict]  # "50": {pickup, trip, threshold_a, actual_a}, ...
    last_trip_cause: str | None = None
    last_trip_at: str | None = None
    sync_check_ok: bool = True


@dataclass
class XfmrReadings:
    winding_temp_c: float
    oil_temp_c: float
    h2_ppm: float
    ch4_ppm: float
    c2h2_ppm: float        # acetylene — >2 = active arcing per spec §3.5
    moisture_ppm: float
    pd_magnitude_pc: float


@dataclass
class GenReadings:
    voltage_ll_v: float
    frequency_hz: float
    rpm: float
    oil_pressure_psi: float
    coolant_temp_c: float
    fuel_level_pct: float
    runtime_hours: float
    state: str  # standby | starting | running | paralleled | cooldown


@dataclass
class UpsReadings:
    input_voltage_v: float
    output_voltage_v: float
    battery_pct: float
    runtime_minutes: float
    load_pct: float
    state: str  # online | battery | bypass | fault


@dataclass
class SoeEvent:
    """Sequence-of-events row."""
    timestamp: str
    severity: str   # info | warn | alarm | critical
    device_id: str
    text: str


@dataclass
class FacilityTelemetry:
    timestamp: str
    breakers: dict[str, BreakerState] = field(default_factory=dict)
    buses: dict[str, BusReadings] = field(default_factory=dict)
    relays: dict[str, RelayState] = field(default_factory=dict)
    xfmrs: dict[str, XfmrReadings] = field(default_factory=dict)
    gens: dict[str, GenReadings] = field(default_factory=dict)
    upses: dict[str, UpsReadings] = field(default_factory=dict)
    soe: list[SoeEvent] = field(default_factory=list)


# A small alarm log — appended to over time by the dev_server.
_GLOBAL_SOE: list[SoeEvent] = []
_LAST_SOE_PUSH = 0.0


def push_soe(severity: str, device_id: str, text: str) -> None:
    _GLOBAL_SOE.append(SoeEvent(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        severity=severity, device_id=device_id, text=text,
    ))
    if len(_GLOBAL_SOE) > 200:
        del _GLOBAL_SOE[:50]


def _osc(t: float, period: float, amp: float, base: float) -> float:
    return base + amp * math.sin(2 * math.pi * t / period)


def live_telemetry(devices, runs, now_t: float | None = None,
                   effective_configs: dict | None = None) -> FacilityTelemetry:
    """Generate one snapshot of live telemetry for the current twin state.

    `effective_configs` is a {device_id: Config Context} map produced by
    the equipment loader. When supplied, transformer gas readings are
    derived through test_executor._gas_levels using the device's
    Config Context so live tile values agree with the EvidenceStore's
    most recent DGA sample (same spec evaluated at the same date)."""
    t = now_t if now_t is not None else time.time()
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    snap = FacilityTelemetry(timestamp=iso)

    by_id = {d.device_id: d for d in devices}

    # Breakers: position per latest test status; small daily wear/ops.
    # mv-main, mv-tie, mtz-inc, mtz-fdr-* are breakers.
    for d in devices:
        if d.device_type_slug in {
            "schneider-gma-1200a", "schneider-mtz-4000a", "schneider-mtz-1200a",
        }:
            if d.test_status == "failed":
                position = "tripped"
            elif d.test_status == "aborted":
                position = "open"
            elif d.test_status == "pending":
                position = "racked_out"
            else:
                position = "closed"
            rating = {
                "schneider-gma-1200a": 1200,
                "schneider-mtz-4000a": 4000,
                "schneider-mtz-1200a": 1200,
            }[d.device_type_slug]
            wear = (hash(d.device_id) % 6)
            ops = (hash(d.device_id) % 200) + 4
            snap.breakers[d.device_id] = BreakerState(
                position=position, rating_amps=rating,
                spring_charged=position == "closed",
                contact_wear_pct=wear, ops_count=ops,
            )

    # Bus readings: MV at 13.8kV, LV at 480V. Slight modulation.
    snap.buses["mv-bus-A"] = BusReadings(
        voltage_ll_v=_osc(t, 30, 35, 13_800),
        current_a=_osc(t, 11, 25, 380),
        real_power_kw=_osc(t, 11, 800, 8_900),
        reactive_power_kvar=_osc(t, 17, 300, 2_100),
        power_factor=_osc(t, 13, 0.02, 0.97),
        frequency_hz=_osc(t, 7, 0.04, 60.0),
        thd_voltage_pct=_osc(t, 23, 0.4, 2.3),
    )
    snap.buses["mv-bus-B"] = BusReadings(
        voltage_ll_v=_osc(t, 28, 30, 13_790),
        current_a=_osc(t, 13, 22, 360),
        real_power_kw=_osc(t, 13, 700, 8_300),
        reactive_power_kvar=_osc(t, 19, 280, 1_900),
        power_factor=_osc(t, 11, 0.02, 0.96),
        frequency_hz=_osc(t, 7, 0.04, 60.0),
        thd_voltage_pct=_osc(t, 21, 0.3, 2.1),
    )
    for side in ("A", "B"):
        for n in (1, 2):
            bus_id = f"lv-bus-{side}{n}"
            snap.buses[bus_id] = BusReadings(
                voltage_ll_v=_osc(t + hash(bus_id) % 5, 17, 4, 480),
                current_a=_osc(t + hash(bus_id) % 7, 11, 80, 1850),
                real_power_kw=_osc(t + hash(bus_id) % 11, 13, 80, 1350),
                reactive_power_kvar=_osc(t + hash(bus_id) % 9, 19, 35, 290),
                power_factor=_osc(t, 13, 0.015, 0.972),
                frequency_hz=_osc(t, 7, 0.04, 60.0),
                thd_voltage_pct=_osc(t, 23, 0.5, 3.1),
            )

    # SEL-751 relays: pickup elements 50, 51, 50N, 51N, 27 (undervolt), 81 (frequency)
    for d in devices:
        if d.device_type_slug != "sel-751":
            continue
        bus_id = "mv-bus-A" if "main-A" in d.device_id else "mv-bus-B"
        if "lv" in d.device_id.lower() or "inc" in d.device_id.lower():
            bus_id = f"lv-bus-{d.device_id[-2:].upper().replace('-', '')}"
        bus = snap.buses.get(bus_id) or snap.buses["mv-bus-A"]
        elems = {
            "50":   {"label": "Phase IOC",   "threshold_a": 800, "actual_a": bus.current_a,
                     "pickup": False, "trip": False},
            "51":   {"label": "Phase TOC",   "threshold_a": 480, "actual_a": bus.current_a,
                     "pickup": bus.current_a > 480, "trip": False},
            "50N":  {"label": "Ground IOC",  "threshold_a": 80,  "actual_a": 12.4,
                     "pickup": False, "trip": False},
            "51N":  {"label": "Ground TOC",  "threshold_a": 50,  "actual_a": 12.4,
                     "pickup": False, "trip": False},
            "27":   {"label": "Undervoltage","threshold_v": 0.85 * 13_800, "actual_v": bus.voltage_ll_v,
                     "pickup": False, "trip": False},
            "81":   {"label": "Frequency",   "threshold_hz_lo": 59.5, "threshold_hz_hi": 60.5,
                     "actual_hz": bus.frequency_hz, "pickup": False, "trip": False},
        }
        last_trip_cause = None
        last_trip_at = None
        if d.test_status == "failed":
            # Show 51 picked up + tripped on the failed device.
            elems["51"]["pickup"] = True
            elems["51"]["trip"] = True
            last_trip_cause = "51 PHASE TOC"
            last_trip_at = d.last_test_at
        snap.relays[d.device_id] = RelayState(
            elements=elems, last_trip_cause=last_trip_cause,
            last_trip_at=last_trip_at, sync_check_ok=True,
        )

    # Transformers — gases pulled from the per-XFMR Config Context evaluated
    # at `today` so live tile values agree with the EvidenceStore's most
    # recent DGA sample for the same transformer.
    from datetime import datetime as _dt
    cfg_map = effective_configs or {}
    for d in devices:
        if not d.device_id.startswith("xfmr-"):
            continue
        cfg = cfg_map.get(d.device_id)
        if cfg is not None and cfg.get("category") == "transformer":
            from src.test_executor import _gas_levels
            gases = _gas_levels(cfg, _dt.utcnow())
            c2h2, h2, ch4 = gases["c2h2"], gases["h2"], gases["ch4"]
        else:
            c2h2 = _osc(t + hash(d.device_id), 51, 0.2, 0.8)
            h2 = _osc(t, 47, 8, 35)
            ch4 = _osc(t, 53, 4, 18)
        snap.xfmrs[d.device_id] = XfmrReadings(
            winding_temp_c=_osc(t + hash(d.device_id) % 9, 31, 4, 68),
            oil_temp_c=_osc(t + hash(d.device_id) % 5, 41, 3, 52),
            h2_ppm=h2,
            ch4_ppm=ch4,
            c2h2_ppm=c2h2,
            moisture_ppm=_osc(t, 67, 1.5, 8.4),
            pd_magnitude_pc=_osc(t + hash(d.device_id), 31, 12, 26),
        )

    # Generators
    for d in devices:
        if d.device_type_slug != "cat-3516b":
            continue
        # gen-1 standby, gen-2 standby (both pre-test)
        snap.gens[d.device_id] = GenReadings(
            voltage_ll_v=0.0,
            frequency_hz=0.0,
            rpm=0.0,
            oil_pressure_psi=_osc(t, 17, 1, 4),
            coolant_temp_c=_osc(t, 31, 1, 24),
            fuel_level_pct=_osc(t, 600, 0.3, 87.4),
            runtime_hours=124.5 + (hash(d.device_id) % 50),
            state="standby",
        )

    # UPSes
    for d in devices:
        if d.device_type_slug != "apc-symmetra-mw":
            continue
        side = d.device_id[-1]
        bus = snap.buses[f"lv-bus-{side}1"]
        snap.upses[d.device_id] = UpsReadings(
            input_voltage_v=bus.voltage_ll_v,
            output_voltage_v=_osc(t + hash(d.device_id), 17, 1.5, 480),
            battery_pct=_osc(t, 1800, 0.5, 96.3),
            runtime_minutes=_osc(t, 1800, 1, 17),
            load_pct=_osc(t + hash(d.device_id), 19, 4, 62),
            state="online",
        )

    snap.soe = list(_GLOBAL_SOE[-30:])
    return snap


def telemetry_to_dict(snap: FacilityTelemetry) -> dict:
    """JSON-serializable form for the API."""
    return {
        "timestamp": snap.timestamp,
        "breakers": {k: asdict(v) for k, v in snap.breakers.items()},
        "buses": {k: asdict(v) for k, v in snap.buses.items()},
        "relays": {k: asdict(v) for k, v in snap.relays.items()},
        "xfmrs": {k: asdict(v) for k, v in snap.xfmrs.items()},
        "gens": {k: asdict(v) for k, v in snap.gens.items()},
        "upses": {k: asdict(v) for k, v in snap.upses.items()},
        "soe": [asdict(e) for e in snap.soe],
    }
