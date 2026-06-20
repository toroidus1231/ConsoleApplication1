"""Tests for Module 14 — Reconciliation Engine."""

import httpx

from src.reconciliation import reconcile, reconcile_from_netbox
from src.types import PunchListItem, TestResult


def _design_device(
    device_id="1",
    name="ups-a3",
    slug="ups5000",
    site="DC1",
    rack="A3",
    position=10,
    firmware="2.1.0",
    power_source="PDU-A1",
    lldp=None,
) -> dict:
    return {
        "device_id": device_id,
        "name": name,
        "device_type_slug": slug,
        "site": site,
        "rack": rack,
        "position": position,
        "firmware_version": firmware,
        "power_source": power_source,
        "lldp_neighbors": lldp if lldp is not None else {"eth0": "switch-01:ge-0/0/1"},
    }


def _actual_from(design: dict, **overrides) -> dict:
    actual = dict(design)
    actual.update(overrides)
    return actual


def _by_category(items):
    out = {}
    for it in items:
        out.setdefault(it.category, []).append(it)
    return out


# --------------------------------------------------------------------------- #
# Pure reconcile()
# --------------------------------------------------------------------------- #


def test_clean_case_is_empty():
    design = _design_device()
    actual = {design["device_id"]: _actual_from(design)}
    result = TestResult(
        test_id="t1", device_id="1", test_name="ups_runtime", status="passed"
    )

    items = reconcile([design], actual, {"1": [result]})

    assert items == []


def test_missing_device_is_critical_identity():
    design = _design_device()
    items = reconcile([design], {})

    assert len(items) == 1
    item = items[0]
    assert item.severity == "critical"
    assert item.category == "identity"
    assert item.expected == "ups5000"
    assert item.actual == "NOT FOUND"
    assert item.device_id == "1"
    assert item.device_name == "ups-a3"
    assert item.site == "DC1"
    assert item.rack == "A3"
    assert item.source == "reconciliation"
    assert item.status == "open"
    assert item.remediation  # non-empty helpful string


def test_wrong_model_is_major_identity():
    design = _design_device()
    actual = {"1": _actual_from(design, device_type_slug="ups9000")}

    items = reconcile([design], actual)

    assert len(items) == 1
    assert items[0].severity == "major"
    assert items[0].category == "identity"
    assert items[0].expected == "ups5000"
    assert items[0].actual == "ups9000"


def test_wrong_rack_position_is_major_identity():
    design = _design_device()
    actual = {"1": _actual_from(design, rack="B7", position=22)}

    items = reconcile([design], actual)

    assert len(items) == 1
    assert items[0].severity == "major"
    assert items[0].category == "identity"
    assert "A3/10" in items[0].expected
    assert "B7/22" in items[0].actual


def test_firmware_mismatch_is_minor_firmware():
    design = _design_device()
    actual = {"1": _actual_from(design, firmware_version="2.0.5")}

    items = reconcile([design], actual)

    assert len(items) == 1
    assert items[0].severity == "minor"
    assert items[0].category == "firmware"
    assert items[0].expected == "2.1.0"
    assert items[0].actual == "2.0.5"


def test_wrong_power_source_is_major_power():
    design = _design_device()
    actual = {"1": _actual_from(design, power_source="PDU-B2")}

    items = reconcile([design], actual)

    assert len(items) == 1
    assert items[0].severity == "major"
    assert items[0].category == "power"
    assert items[0].expected == "PDU-A1"
    assert items[0].actual == "PDU-B2"


def test_wrong_lldp_neighbor_is_major_network():
    design = _design_device()
    actual = {"1": _actual_from(design, lldp_neighbors={"eth0": "switch-99:ge-0/0/9"})}

    items = reconcile([design], actual)

    assert len(items) == 1
    assert items[0].severity == "major"
    assert items[0].category == "network"
    assert "switch-01:ge-0/0/1" in items[0].expected
    assert "switch-99:ge-0/0/9" in items[0].actual


def test_failed_safety_test_is_critical():
    # Safety classified via device_type_slug "ups" token presence is NOT enough;
    # use a slug in the safety set OR a safety test name. Here the slug "ups5000"
    # is not literally in the set, so drive it via the test name.
    design = _design_device(slug="ups5000")
    actual = {"1": _actual_from(design)}
    failed = TestResult(
        test_id="t1", device_id="1", test_name="ups_battery_transfer", status="failed"
    )

    items = reconcile([design], actual, {"1": [failed]})

    test_items = [i for i in items if i.category == "test_failure"]
    assert len(test_items) == 1
    assert test_items[0].severity == "critical"
    assert test_items[0].test_id == "t1"


def test_failed_safety_test_via_device_slug_is_critical():
    # When the device_type_slug itself is a safety type, any failed test on it is
    # critical even if the test name is neutral.
    design = _design_device(slug="ats")
    actual = {"1": _actual_from(design)}
    failed = TestResult(
        test_id="t9", device_id="1", test_name="transfer_time", status="failed"
    )

    items = reconcile([design], actual, {"1": [failed]})

    test_items = [i for i in items if i.category == "test_failure"]
    assert len(test_items) == 1
    assert test_items[0].severity == "critical"


def test_failed_cooling_test_is_major():
    design = _design_device(slug="crah-unit")
    actual = {"1": _actual_from(design)}
    failed = TestResult(
        test_id="t2", device_id="1", test_name="cooling_capacity", status="failed"
    )

    items = reconcile([design], actual, {"1": [failed]})

    test_items = [i for i in items if i.category == "test_failure"]
    assert len(test_items) == 1
    assert test_items[0].severity == "major"
    assert test_items[0].category == "test_failure"


def test_unexpected_discovered_device_is_info():
    design = _design_device(device_id="1", name="ups-a3")
    actual = {
        "1": _actual_from(design),
        "rogue-1": {
            "device_id": "rogue-1",
            "name": "rogue-1",
            "device_type_slug": "raspberry-pi",
            "site": "DC1",
            "rack": "Z9",
        },
    }

    items = reconcile([design], actual)

    info_items = [i for i in items if i.severity == "info"]
    assert len(info_items) == 1
    assert info_items[0].category == "identity"
    assert info_items[0].expected == "NOT IN DESIGN"
    assert info_items[0].actual == "raspberry-pi"
    assert info_items[0].device_id == "rogue-1"


def test_all_deviations_caught_together():
    # One design device per deviation kind, all in a single reconcile pass.
    missing = _design_device(device_id="1", name="missing-ups")
    wrong_model = _design_device(device_id="2", name="model-dev", slug="cm2000")
    wrong_loc = _design_device(device_id="3", name="loc-dev")
    fw = _design_device(device_id="4", name="fw-dev")
    power = _design_device(device_id="5", name="power-dev")
    network = _design_device(device_id="6", name="net-dev")
    safety = _design_device(device_id="7", name="ats-dev", slug="ats")
    cooling = _design_device(device_id="8", name="crah-dev", slug="crah")

    design = [missing, wrong_model, wrong_loc, fw, power, network, safety, cooling]

    actual = {
        # "1" missing on purpose
        "2": _actual_from(wrong_model, device_type_slug="ex4300"),
        "3": _actual_from(wrong_loc, rack="B1", position=4),
        "4": _actual_from(fw, firmware_version="0.9.0"),
        "5": _actual_from(power, power_source="PDU-Z9"),
        "6": _actual_from(network, lldp_neighbors={"eth0": "wrong-switch:xe-0/0/0"}),
        "7": _actual_from(safety),
        "8": _actual_from(cooling),
        "rogue": {"device_id": "rogue", "name": "rogue", "device_type_slug": "nuc"},
    }
    tests = {
        "7": [TestResult(test_id="s1", device_id="7", test_name="transfer", status="failed")],
        "8": [TestResult(test_id="c1", device_id="8", test_name="cooling_capacity", status="failed")],
    }

    items = reconcile(design, actual, tests)

    def find(device_id, category):
        return [i for i in items if i.device_id == device_id and i.category == category]

    # missing -> critical identity
    m = find("1", "identity")
    assert len(m) == 1 and m[0].severity == "critical" and m[0].actual == "NOT FOUND"
    # wrong model -> major identity
    assert find("2", "identity")[0].severity == "major"
    # wrong location -> major identity
    assert find("3", "identity")[0].severity == "major"
    # firmware -> minor firmware
    assert find("4", "firmware")[0].severity == "minor"
    # power -> major power
    assert find("5", "power")[0].severity == "major"
    # network -> major network
    assert find("6", "network")[0].severity == "major"
    # safety test failure -> critical
    assert find("7", "test_failure")[0].severity == "critical"
    # cooling test failure -> major
    assert find("8", "test_failure")[0].severity == "major"
    # unexpected -> info
    rogue = [i for i in items if i.device_id == "rogue"]
    assert len(rogue) == 1 and rogue[0].severity == "info"

    # Every item is well-formed.
    for it in items:
        assert isinstance(it, PunchListItem)
        assert it.source == "reconciliation"
        assert it.status == "open"
        assert it.remediation


def test_missing_device_still_evaluates_tests():
    design = _design_device(device_id="1", name="ups-a3", slug="ups")
    failed = TestResult(test_id="t1", device_id="1", test_name="runtime", status="failed")

    items = reconcile([design], {}, {"1": [failed]})

    cats = _by_category(items)
    assert "identity" in cats and cats["identity"][0].severity == "critical"
    assert "test_failure" in cats and cats["test_failure"][0].severity == "critical"


def test_robust_to_missing_optional_fields():
    # Design and actual carry only the bare minimum — no firmware/power/lldp/etc.
    design = {"device_id": "1", "name": "bare"}
    actual = {"1": {"device_id": "1", "name": "bare"}}

    items = reconcile([design], actual)

    assert items == []


def test_matches_actual_by_name_when_no_id_key():
    design = _design_device(device_id="1", name="ups-a3")
    # actual keyed by NAME, not id.
    actual = {"ups-a3": _actual_from(design, firmware_version="9.9.9")}

    items = reconcile([design], actual)

    assert len(items) == 1
    assert items[0].category == "firmware"


def test_passed_tests_produce_nothing():
    design = _design_device(slug="ats")
    actual = {"1": _actual_from(design)}
    passed = TestResult(test_id="t1", device_id="1", test_name="transfer", status="passed")

    items = reconcile([design], actual, {"1": [passed]})

    assert items == []


# --------------------------------------------------------------------------- #
# Async NetBox-backed reconcile_from_netbox()
# --------------------------------------------------------------------------- #


def _netbox_record(
    dev_id, name, slug, site="DC1", rack="A3", position=10, ctx=None
) -> dict:
    return {
        "id": dev_id,
        "name": name,
        "device_type": {"slug": slug},
        "site": {"name": site},
        "rack": {"name": rack},
        "position": position,
        "config_context": ctx or {},
    }


async def test_reconcile_from_netbox_flags_missing_device():
    record = _netbox_record(1, "ups-a3", "ups5000")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Token tok"
        assert request.url.params.get("status") == "active"
        return httpx.Response(200, json={"results": [record], "next": None})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        items = await reconcile_from_netbox(
            "http://nb", "tok", client=c, actual_devices={}
        )

    assert len(items) == 1
    assert items[0].severity == "critical"
    assert items[0].category == "identity"
    assert items[0].actual == "NOT FOUND"
    assert items[0].device_name == "ups-a3"


async def test_reconcile_from_netbox_clean_match():
    record = _netbox_record(
        1, "ups-a3", "ups5000", ctx={"firmware_version": "2.1.0"}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [record], "next": None})

    actual = {
        "1": {
            "device_id": "1",
            "name": "ups-a3",
            "device_type_slug": "ups5000",
            "site": "DC1",
            "rack": "A3",
            "position": 10,
            "firmware_version": "2.1.0",
        }
    }

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        items = await reconcile_from_netbox(
            "http://nb", "tok", client=c, actual_devices=actual
        )

    assert items == []


async def test_reconcile_from_netbox_handles_pagination_and_firmware():
    page1 = {
        "results": [
            _netbox_record(1, "a", "ups5000", ctx={"firmware_version": "2.1.0"})
        ],
        "next": "http://nb/api/dcim/devices/?limit=1000&offset=1000",
    }
    page2 = {
        "results": [
            _netbox_record(2, "b", "ups5000", ctx={"firmware_version": "2.1.0"})
        ],
        "next": None,
    }
    pages = iter([page1, page2])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(pages))

    actual = {
        "1": {"device_id": "1", "name": "a", "device_type_slug": "ups5000", "firmware_version": "2.1.0"},
        "2": {"device_id": "2", "name": "b", "device_type_slug": "ups5000", "firmware_version": "1.0.0"},
    }

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        items = await reconcile_from_netbox(
            "http://nb", "tok", client=c, actual_devices=actual
        )

    # device 2 has a firmware mismatch, device 1 is clean.
    assert len(items) == 1
    assert items[0].device_id == "2"
    assert items[0].category == "firmware"
    assert items[0].severity == "minor"
