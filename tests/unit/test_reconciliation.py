"""Tests for Module 14 — Reconciliation Engine.

Pure-function tests over (design, actual, test_outcomes) → punch list.
Exercises the contracts §5.2 severity matrix.
"""

from src.reconciliation import (
    ActualDevice,
    CrossReading,
    DesignDevice,
    TestOutcome,
    check_sensor_drift,
    reconcile,
)


def _design(**overrides) -> DesignDevice:
    base = dict(
        device_id="d1",
        name="cm2000-A3-01",
        site="DC1",
        rack="A3",
        position=10,
        device_type_slug="cm2000",
        expected_firmware=None,
        expected_power_source_id=None,
        expected_lldp_neighbors=None,
    )
    base.update(overrides)
    return DesignDevice(**base)


def _actual(**overrides) -> ActualDevice:
    base = dict(
        device_id="d1",
        name="cm2000-A3-01",
        site="DC1",
        rack="A3",
        position=10,
        device_type_slug="cm2000",
        actual_firmware=None,
        actual_power_source_id=None,
        actual_lldp_neighbors=None,
    )
    base.update(overrides)
    return ActualDevice(**base)


# --- Identity / location -----------------------------------------------------


def test_perfect_match_yields_empty_punch_list():
    items = reconcile([_design()], [_actual()])
    assert items == []


def test_missing_device_is_critical():
    items = reconcile([_design()], [])
    assert len(items) == 1
    assert items[0].severity == "critical"
    assert items[0].category == "identity"
    assert items[0].actual == "NOT FOUND"


def test_wrong_model_is_major():
    items = reconcile([_design()], [_actual(device_type_slug="cm3000")])
    assert any(i.severity == "major" and i.category == "identity" for i in items)


def test_wrong_rack_position_is_major():
    items = reconcile([_design()], [_actual(rack="B5", position=20)])
    assert any(i.severity == "major" and "rack" in i.expected.lower() or "@" in i.expected for i in items)
    # Specifically check the expected/actual form.
    rec = next(i for i in items if i.category == "identity" and "@" in i.expected)
    assert rec.expected == "A3@10"
    assert rec.actual == "B5@20"


def test_unexpected_device_is_info():
    items = reconcile([], [_actual(device_id="d99", name="ghost")])
    assert len(items) == 1
    assert items[0].severity == "info"
    assert items[0].expected == "NOT IN DESIGN"


# --- Firmware ----------------------------------------------------------------


def test_firmware_mismatch_is_minor():
    items = reconcile(
        [_design(expected_firmware="3.2.1")],
        [_actual(actual_firmware="2.0.0")],
    )
    assert any(i.severity == "minor" and i.category == "firmware" for i in items)


def test_firmware_match_no_item():
    items = reconcile(
        [_design(expected_firmware="3.2.1")],
        [_actual(actual_firmware="3.2.1")],
    )
    assert all(i.category != "firmware" for i in items)


def test_firmware_with_no_actual_reading_no_item():
    items = reconcile(
        [_design(expected_firmware="3.2.1")],
        [_actual(actual_firmware=None)],
    )
    assert all(i.category != "firmware" for i in items)


# --- Power -------------------------------------------------------------------


def test_wrong_power_source_is_major():
    items = reconcile(
        [_design(expected_power_source_id="pdu_1")],
        [_actual(actual_power_source_id="pdu_2")],
    )
    assert any(i.severity == "major" and i.category == "power" for i in items)


# --- Network (LLDP) ----------------------------------------------------------


def test_wrong_lldp_neighbors_is_major():
    items = reconcile(
        [_design(expected_lldp_neighbors=["sw1", "sw2"])],
        [_actual(actual_lldp_neighbors=["sw1", "sw9"])],
    )
    assert any(i.severity == "major" and i.category == "network" for i in items)


def test_lldp_neighbors_order_independent():
    items = reconcile(
        [_design(expected_lldp_neighbors=["sw1", "sw2"])],
        [_actual(actual_lldp_neighbors=["sw2", "sw1"])],
    )
    assert all(i.category != "network" for i in items)


# --- Test outcomes -----------------------------------------------------------


def test_failed_safety_test_is_critical():
    items = reconcile(
        [_design()],
        [_actual()],
        test_outcomes=[
            TestOutcome(device_id="d1", test_name="ups_battery_transfer",
                        status="failed", test_id="t1"),
        ],
    )
    fails = [i for i in items if i.category == "test_failure"]
    assert len(fails) == 1
    assert fails[0].severity == "critical"
    assert fails[0].test_id == "t1"


def test_failed_non_safety_test_is_major():
    items = reconcile(
        [_design()], [_actual()],
        test_outcomes=[
            TestOutcome(device_id="d1", test_name="cooling_redundancy",
                        status="failed", test_id="t2", category="cooling"),
        ],
    )
    fails = [i for i in items if i.category == "test_failure"]
    assert fails[0].severity == "major"


def test_passed_test_produces_no_item():
    items = reconcile(
        [_design()], [_actual()],
        test_outcomes=[
            TestOutcome(device_id="d1", test_name="x", status="passed", test_id="t1"),
        ],
    )
    assert all(i.category != "test_failure" for i in items)


def test_aborted_safety_test_is_critical():
    items = reconcile(
        [_design()], [_actual()],
        test_outcomes=[
            TestOutcome(device_id="d1", test_name="breaker_trip",
                        status="aborted", test_id="t3"),
        ],
    )
    fails = [i for i in items if i.category == "test_failure"]
    assert fails[0].severity == "critical"
    assert fails[0].actual == "aborted"


def test_combined_design_and_test_failures_all_emitted():
    items = reconcile(
        [_design(expected_firmware="3.2.1")],
        [_actual(actual_firmware="2.0.0", device_type_slug="cm3000")],
        test_outcomes=[
            TestOutcome(device_id="d1", test_name="ats_transfer",
                        status="failed", test_id="t4"),
        ],
    )
    cats = {i.category for i in items}
    assert cats >= {"identity", "firmware", "test_failure"}


# --- Sensor-drift cross-validation ------------------------------------------


def _cross(primary_v: float, reference_v: float, *, dt_ns: int = 0,
           cert: str = "sha256:ref-cert-1") -> CrossReading:
    return CrossReading(
        physical_link="bus-A1.voltage_ll_avg",
        primary_device_id="cm2000-A1",
        primary_device_name="cm2000-A1",
        primary_reading=primary_v,
        primary_timestamp_ns=1_712_847_600_000_000_000,
        primary_unit="V",
        reference_device_id="fluke-8508a-bench",
        reference_device_name="Fluke 8508A bench DMM",
        reference_reading=reference_v,
        reference_timestamp_ns=1_712_847_600_000_000_000 + dt_ns,
        reference_calibration_cert=cert,
    )


def test_sensor_within_tolerance_no_item():
    # 478.0 vs 478.5 → 0.10% drift, under default 1%.
    items = check_sensor_drift([_cross(478.0, 478.5)])
    assert items == []


def test_minor_drift_emits_minor_severity():
    # 478 vs 489 → ~2.25% drift, between 1% and 5%.
    items = check_sensor_drift([_cross(478.0, 489.0)])
    assert len(items) == 1
    assert items[0].severity == "minor"
    assert items[0].category == "sensor_drift"
    assert "Recalibrate" in items[0].remediation


def test_major_drift_emits_major_severity():
    # 412 vs 478 → ~13.8% drift, well above 5%.
    items = check_sensor_drift([_cross(412.0, 478.0)])
    assert len(items) == 1
    assert items[0].severity == "major"
    assert "13" in items[0].remediation  # diff_pct shown in remediation


def test_calibration_cert_recorded_in_expected_field():
    items = check_sensor_drift([_cross(412.0, 478.0, cert="sha256:cert-XYZ")])
    assert "sha256:cert-XYZ" in items[0].expected
    assert "Fluke 8508A bench DMM" in items[0].expected


def test_zero_reference_skipped_silently():
    # Avoid division by zero when reference reads 0.
    items = check_sensor_drift([_cross(412.0, 0.0)])
    assert items == []


def test_timestamp_skew_beyond_tolerance_skipped():
    # 412 vs 478 with 60s skew — too far apart to compare.
    items = check_sensor_drift([_cross(412.0, 478.0, dt_ns=60_000_000_000)])
    assert items == []


def test_within_skew_tolerance_still_flagged():
    # 1 second skew is within the 5 s default tolerance.
    items = check_sensor_drift([_cross(412.0, 478.0, dt_ns=1_000_000_000)])
    assert len(items) == 1


def test_reconcile_includes_cross_readings():
    items = reconcile(
        [_design()],
        [_actual()],
        cross_readings=[_cross(412.0, 478.0)],
    )
    drift = [i for i in items if i.category == "sensor_drift"]
    assert len(drift) == 1


def test_remediation_names_physical_link_so_operator_knows_what_to_recalibrate():
    items = check_sensor_drift([_cross(412.0, 478.0)])
    assert "bus-A1.voltage_ll_avg" in items[0].remediation
