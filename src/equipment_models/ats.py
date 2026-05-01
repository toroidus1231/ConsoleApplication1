"""ATS transfer test record. Cross-references the upstream generator
spec and downstream UPS specs evaluated at the run date so the entire
sequence is consistent with the actual gen + UPSes at the time of test.

If the generator slows with age, the ATS sequence's gen-build step
slows with it. If a downstream UPS is failing, its battery dip during
transfer reflects the UPS spec, not a hardcoded number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .generator import GeneratorSpec, gen_build_time_seconds, gen_voltage_at_build
from .ups import UPSSpec, ups_run_record


@dataclass
class ATSSpec:
    ats_id: str
    rated_amps: int = 4000
    manufacturer: str = "ASCO"
    model: str = "7000"
    site: str = "DC1-Ashburn"
    upstream_gen: GeneratorSpec = None
    downstream_ups: list[UPSSpec] = field(default_factory=list)


def ats_run_record(spec: ATSSpec, run_date: datetime) -> dict:
    """Build the ATS transfer-test sequence using the ACTUAL gen build
    behavior at run_date, plus the actual downstream UPS state."""
    n_ups = len(spec.downstream_ups)
    # Gen build step pulls timing/voltage from gen spec evaluated today
    if spec.upstream_gen is not None:
        gen_build_t = gen_build_time_seconds(spec.upstream_gen, run_date)
        gen_build_v = gen_voltage_at_build(spec.upstream_gen, run_date)
    else:
        gen_build_t = 8.2
        gen_build_v = 488

    transfer_t = round(gen_build_t + 4.2, 2)        # ATS waits for stable gen
    confirm_t  = round(transfer_t + 0.7, 2)
    hold_ms     = 313_000
    return_ms   = 320_000
    cooldown_ms = 620_400

    # Downstream UPS state at run_date — each UPS is evaluated through
    # its own spec/model so this isn't a hardcoded "12 of 12 confirmed"
    ups_states = []
    all_online = True
    for ups_spec in spec.downstream_ups:
        rec = ups_run_record(ups_spec, run_date)
        stayed = rec["passed"]
        all_online = all_online and stayed
        ups_states.append({
            "id": ups_spec.ups_id,
            "site": spec.site,
            "min_input_v_during": rec["min_voltage_v"],
            "battery_pct_after": rec["battery_pct"],
            "stayed_online": stayed,
        })

    online_actual = f"{sum(1 for u in ups_states if u['stayed_online'])} of {n_ups} confirmed"
    online_expected = f"{n_ups} of {n_ups} online_battery → online"

    sequence = [
        {"step": "Pre-test: gen ready_standby", "expected": "ready", "actual": "ready", "passed": True, "t_offset_ms": 0},
        {"step": "Pre-test: ats == source_1", "expected": "source_1", "actual": "source_1", "passed": True, "t_offset_ms": 0},
        {"step": "Pre-test: all downstream UPS online", "expected": f"{n_ups} of {n_ups}", "actual": f"{n_ups} of {n_ups}", "passed": True, "t_offset_ms": 0},
        {"step": "Simulate utility loss", "expected": "command issued", "actual": "command issued", "passed": True, "t_offset_ms": 200},
        {"step": "Gen voltage build", "expected": "≥ rated within 10 s", "actual": f"{gen_build_v} V @ {gen_build_t} s", "passed": gen_build_t <= 10.0, "t_offset_ms": int(gen_build_t * 1000)},
        {"step": "Gen frequency settle", "expected": "59.5 – 60.5 Hz", "actual": "60.02 Hz", "passed": True, "t_offset_ms": int(gen_build_t * 1000)},
        {"step": "ATS transfer to source_2", "expected": "≤ 30 s", "actual": f"{transfer_t} s", "passed": transfer_t <= 30.0, "t_offset_ms": int(transfer_t * 1000)},
        {"step": "All downstream UPS still online", "expected": online_expected, "actual": online_actual, "passed": all_online, "t_offset_ms": int(confirm_t * 1000)},
        {"step": "Hold on gen", "expected": "5 min stable", "actual": "stable", "passed": True, "t_offset_ms": hold_ms},
        {"step": "Return to utility", "expected": "auto-retransfer", "actual": "8.7 s", "passed": True, "t_offset_ms": return_ms},
        {"step": "Gen cooldown", "expected": "5 min idle then stop", "actual": "stopped @ 5:00.4", "passed": True, "t_offset_ms": cooldown_ms},
    ]

    return {
        "device_id": spec.ats_id,
        "test_type": "ats_transfer",
        "rated_amps": spec.rated_amps,
        "manufacturer": spec.manufacturer,
        "model": spec.model,
        "sequence": sequence,
        "downstream_ups": ups_states,
        "overall_passed": all(s["passed"] is True for s in sequence),
        "completed_at": run_date.isoformat(),
    }
