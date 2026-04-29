"""Module 14: Reconciliation Engine.

Compares the design state (devices NetBox knows about, from BIM import) to
the actual state (what discovery + active tests report). Produces a list of
PunchListItem per contracts spec §5.2 (algorithm) and §3.1 PunchListItem
schema.

Severity matrix from contracts §5.2:

  critical:  device in design not found, safety test failed (UPS/ATS/breaker)
  major:     wrong model, wrong rack, wrong network, wrong power source,
             non-safety test failed
  minor:     firmware mismatch, IP address mismatch, DNS/NTP mismatch
  info:      unexpected device (not in design)

Inputs are passed as plain dicts so this module doesn't import NetBox or
InfluxDB clients — Module 21 (FastAPI) wires up the real data sources.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .types import PunchListItem


SAFETY_CATEGORIES = {"power", "safety"}
SAFETY_TEST_NAMES = {
    "ups_battery_transfer",
    "ats_transfer",
    "breaker_trip",
    "dc_bus_battery_discharge",
    "relay_trip",
}


@dataclass
class DesignDevice:
    """A device the design (BIM/NetBox) expects to exist."""

    device_id: str
    name: str
    site: str
    rack: str
    position: int
    device_type_slug: str
    expected_firmware: str | None = None
    expected_power_source_id: str | None = None
    expected_lldp_neighbors: list[str] | None = None  # interface peer device_ids


@dataclass
class ActualDevice:
    """A device discovered or measured to actually exist."""

    device_id: str
    name: str
    site: str
    rack: str
    position: int
    device_type_slug: str
    actual_firmware: str | None = None
    actual_power_source_id: str | None = None
    actual_lldp_neighbors: list[str] | None = None


@dataclass
class TestOutcome:
    """One row from TestResult history needed by reconciliation."""

    device_id: str
    test_name: str
    status: str  # passed | failed | aborted | restore_failure
    test_id: str
    category: str = ""  # safety | power | cooling | other


@dataclass
class CrossReading:
    """Two readings of the same physical quantity for sensor-drift detection.

    The platform builds one of these whenever a calibrated reference
    instrument (e.g. Fluke 8508A clamped to bus A1) is read at the same
    timestamp as a permanently-installed sensor on the same physical
    point (e.g. CM2000-A1's voltage_ll_avg). A divergence above the
    tolerance flags drift in the permanent sensor, with the reference
    instrument's calibration cert in the audit trail.
    """

    physical_link: str
    primary_device_id: str
    primary_device_name: str
    primary_reading: float
    primary_timestamp_ns: int
    primary_unit: str
    reference_device_id: str
    reference_device_name: str
    reference_reading: float
    reference_timestamp_ns: int
    reference_calibration_cert: str = ""


def reconcile(
    design: list[DesignDevice],
    actual: list[ActualDevice],
    test_outcomes: Iterable[TestOutcome] = (),
    cross_readings: Iterable[CrossReading] = (),
    *,
    sensor_drift_minor_pct: float = 1.0,
    sensor_drift_major_pct: float = 5.0,
    timestamp_skew_ns: int = 5_000_000_000,  # 5 s — see note below
) -> list[PunchListItem]:
    """Run every reconciliation check and return the combined punch list."""
    actual_by_id = {a.device_id: a for a in actual}

    items: list[PunchListItem] = []
    for d in design:
        a = actual_by_id.get(d.device_id)
        if a is None:
            items.append(_missing_device(d))
            continue
        items.extend(_check_design_vs_actual(d, a))

    # Devices found that aren't in the design.
    design_ids = {d.device_id for d in design}
    for a in actual:
        if a.device_id not in design_ids:
            items.append(_unexpected_device(a))

    # Failed tests.
    for outcome in test_outcomes:
        if outcome.status == "passed":
            continue
        items.append(_failed_test(outcome, actual_by_id.get(outcome.device_id)))

    # Sensor-drift cross-validation.
    items.extend(check_sensor_drift(
        cross_readings,
        minor_pct=sensor_drift_minor_pct,
        major_pct=sensor_drift_major_pct,
        timestamp_skew_ns=timestamp_skew_ns,
    ))

    return items


def check_sensor_drift(
    readings: Iterable[CrossReading],
    *,
    minor_pct: float = 1.0,
    major_pct: float = 5.0,
    timestamp_skew_ns: int = 5_000_000_000,
) -> list[PunchListItem]:
    """For every CrossReading where the primary diverges from the reference
    by more than ``minor_pct``, emit a PunchListItem.

    Severity:
      < minor_pct           → no item
      [minor_pct, major_pct) → minor (calibrate or trend)
      >= major_pct          → major (replace or recalibrate immediately)

    Pairs whose timestamps are >timestamp_skew_ns apart are skipped — they
    aren't simultaneous enough to compare. Default 5s covers reasonable
    poll-loop scheduling skew.
    """
    items: list[PunchListItem] = []
    for r in readings:
        if r.reference_reading == 0:
            continue
        if abs(r.primary_timestamp_ns - r.reference_timestamp_ns) > timestamp_skew_ns:
            continue
        diff = r.primary_reading - r.reference_reading
        diff_pct = abs(diff) / abs(r.reference_reading) * 100.0
        if diff_pct < minor_pct:
            continue
        severity = "major" if diff_pct >= major_pct else "minor"
        cert = r.reference_calibration_cert or "uncalibrated"
        items.append(PunchListItem(
            severity=severity,
            category="sensor_drift",
            device_id=r.primary_device_id,
            device_name=r.primary_device_name,
            site="",
            rack="",
            expected=f"{r.reference_reading:.4f} {r.primary_unit} (ref: {r.reference_device_name}, cal cert {cert})",
            actual=f"{r.primary_reading:.4f} {r.primary_unit}",
            source="reconciliation",
            remediation=(
                f"Primary sensor reads {diff_pct:.2f}% off reference instrument. "
                f"Recalibrate or replace {r.primary_device_name}. "
                f"Physical link: {r.physical_link}."
            ),
        ))
    return items


# ----------------------------------------------------------------------
# Per-device checks
# ----------------------------------------------------------------------


def _check_design_vs_actual(
    d: DesignDevice, a: ActualDevice
) -> list[PunchListItem]:
    items: list[PunchListItem] = []

    # Model
    if d.device_type_slug != a.device_type_slug:
        items.append(_pl(
            severity="major", category="identity", source="reconciliation",
            device=d, expected=d.device_type_slug, actual=a.device_type_slug,
            remediation=(
                f"Wrong device model installed in {d.rack}. Expected "
                f"{d.device_type_slug}, found {a.device_type_slug}."
            ),
        ))

    # Location (rack + position)
    if d.rack != a.rack or d.position != a.position:
        items.append(_pl(
            severity="major", category="identity", source="reconciliation",
            device=d,
            expected=f"{d.rack}@{d.position}",
            actual=f"{a.rack}@{a.position}",
            remediation=(
                "Device is not in the rack/position the design specifies. "
                "Move device or update the design."
            ),
        ))

    # Firmware
    if (
        d.expected_firmware is not None
        and a.actual_firmware is not None
        and d.expected_firmware != a.actual_firmware
    ):
        items.append(_pl(
            severity="minor", category="firmware", source="reconciliation",
            device=d,
            expected=d.expected_firmware, actual=a.actual_firmware,
            remediation=f"Update firmware to {d.expected_firmware} or update Config Context.",
        ))

    # Power source
    if (
        d.expected_power_source_id
        and a.actual_power_source_id
        and d.expected_power_source_id != a.actual_power_source_id
    ):
        items.append(_pl(
            severity="major", category="power", source="reconciliation",
            device=d,
            expected=d.expected_power_source_id,
            actual=a.actual_power_source_id,
            remediation="Recable to design power source.",
        ))

    # Network neighbors (LLDP)
    if d.expected_lldp_neighbors and a.actual_lldp_neighbors is not None:
        expected = sorted(d.expected_lldp_neighbors)
        actual_n = sorted(a.actual_lldp_neighbors)
        if expected != actual_n:
            items.append(_pl(
                severity="major", category="network", source="reconciliation",
                device=d,
                expected=",".join(expected),
                actual=",".join(actual_n),
                remediation="Verify cable schedule against discovered LLDP neighbors.",
            ))

    return items


def _missing_device(d: DesignDevice) -> PunchListItem:
    return _pl(
        severity="critical", category="identity", source="reconciliation",
        device=d, expected=d.device_type_slug, actual="NOT FOUND",
        remediation=(
            f"Device {d.name} is in the design but not discovered. "
            "Verify power, network, and Config Context."
        ),
    )


def _unexpected_device(a: ActualDevice) -> PunchListItem:
    return _pl(
        severity="info", category="identity", source="discovery",
        device=a, expected="NOT IN DESIGN", actual=a.device_type_slug,
        remediation=(
            "Device discovered that is not in the design. "
            "Either add it to the design or investigate."
        ),
    )


def _failed_test(outcome: TestOutcome, actual: ActualDevice | None) -> PunchListItem:
    is_safety = (
        outcome.category in SAFETY_CATEGORIES
        or outcome.test_name in SAFETY_TEST_NAMES
    )
    severity = "critical" if is_safety else "major"
    name = actual.name if actual else outcome.device_id
    site = actual.site if actual else ""
    rack = actual.rack if actual else ""
    item = PunchListItem(
        severity=severity,
        category="test_failure",
        device_id=outcome.device_id,
        device_name=name,
        site=site,
        rack=rack,
        expected="passed",
        actual=outcome.status,
        source="active_test",
        test_id=outcome.test_id,
        remediation=f"Investigate failure of {outcome.test_name}.",
    )
    return item


def _pl(
    severity: str,
    category: str,
    source: str,
    device,
    expected: str,
    actual: str,
    remediation: str,
) -> PunchListItem:
    return PunchListItem(
        severity=severity,
        category=category,
        device_id=device.device_id,
        device_name=device.name,
        site=device.site,
        rack=device.rack,
        expected=str(expected),
        actual=str(actual),
        source=source,
        remediation=remediation,
    )
