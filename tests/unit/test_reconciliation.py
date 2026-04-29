"""Tests for Module 14 — Reconciliation Engine.

Pure-function tests over (design, actual, test_outcomes) → punch list.
Exercises the contracts §5.2 severity matrix.
"""

from src.reconciliation import (
    ActualDevice,
    DesignDevice,
    TestOutcome,
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
