"""Module 14: Reconciliation Engine.

Compares the DESIGN state (NetBox devices, from BIM import) against the ACTUAL
state (discovered devices + test results) and produces a list[PunchListItem]
(contracts spec §5.2).

The heart of the module is ``reconcile`` — a PURE function over plain dicts that
walks every design device through the §5.2 checks (identity, model, firmware,
location, network, power, tests) and finally flags any discovered device that is
not in the design. ``reconcile_from_netbox`` is a thin async adapter that reads
the design devices from NetBox (paginated, like Module 2) and delegates.

Inputs are modelled as plain dicts defined here, not as new wire types — the only
shared types this module imports are PunchListItem and TestResult. Every check is
robust to missing optional fields (firmware/power/lldp may be absent → that check
is skipped rather than crashing).
"""

from __future__ import annotations

from typing import Iterable

import httpx

from .types import PunchListItem, TestResult


# Test names / device slugs implicating a safety-critical system. A test is
# "safety" (→ critical when failed) when the design device's device_type_slug is
# in this set, OR when the test_name contains one of these tokens (e.g. an
# "ats_transfer" test on any device). Otherwise a failed test is "major"
# (non-safety: cooling/environmental). Documented in the module README via this
# default; callers may override per-call.
SAFETY_TEST_TYPES = frozenset({"ups", "ats", "breaker", "relay", "dc_bus"})

SOURCE = "reconciliation"


def reconcile(
    design_devices: list[dict],
    actual_devices: dict,
    test_results: dict | None = None,
    *,
    safety_test_types: frozenset = SAFETY_TEST_TYPES,
) -> list[PunchListItem]:
    """Reconcile the design against the actual state. PURE.

    For every device in ``design_devices`` the §5.2 checks run in order:
    identity → model → firmware → location → network → power → tests. Then any
    device in ``actual_devices`` with no design counterpart is flagged ``info``.

    Args:
        design_devices: The intended state. Each item is a dict shaped like::

            {
                "device_id", "name", "device_type_slug", "site", "rack",
                "position", "firmware_version", "power_source",
                "lldp_neighbors": {iface: connected_endpoint},
            }

            Only ``device_id``/``name`` are required; every other field is
            optional and its absence simply skips the corresponding check.
        actual_devices: Mapping of ``device_id`` OR ``name`` → discovered dict
            with the same shape as a design device (the keys that were actually
            observed). A device is considered "discovered" when either its
            design ``device_id`` or its design ``name`` is present as a key.
        test_results: Mapping of ``device_id`` → list[TestResult]. Optional.
        safety_test_types: Slugs/tokens that make a failed test safety-critical.

    Returns:
        The punch list. Empty when design and actual agree on every checked
        field and every test passed.
    """
    test_results = test_results or {}
    items: list[PunchListItem] = []

    matched_actual_keys: set = set()

    for design in design_devices:
        device_id = str(design.get("device_id", "") or "")
        name = design.get("name", "") or ""

        actual = _lookup_actual(design, actual_devices)
        if actual is not None:
            matched_actual_keys |= _actual_keys(design, actual_devices)

        # 1. IDENTITY CHECK — was it discovered at all?
        if actual is None:
            items.append(
                _item(
                    design,
                    severity="critical",
                    category="identity",
                    expected=design.get("device_type_slug", "") or "",
                    actual="NOT FOUND",
                    remediation=(
                        f"Device '{name}' from the design was never discovered. "
                        "Verify it is installed, powered, and reachable on the "
                        "network, then re-run discovery."
                    ),
                )
            )
            # No actual state to compare the rest against; still evaluate tests.
            items.extend(
                _test_items(design, test_results.get(device_id, []), safety_test_types)
            )
            continue

        # 2. MODEL CHECK — discovered model vs design model.
        d_slug = design.get("device_type_slug")
        a_slug = actual.get("device_type_slug")
        if d_slug is not None and a_slug is not None and a_slug != d_slug:
            items.append(
                _item(
                    design,
                    severity="major",
                    category="identity",
                    expected=d_slug,
                    actual=a_slug,
                    remediation=(
                        f"Wrong device model: design expects '{d_slug}' but "
                        f"'{a_slug}' was found. Swap the device or correct the "
                        "design record."
                    ),
                )
            )

        # 3. FIRMWARE CHECK — actual firmware vs design firmware.
        d_fw = design.get("firmware_version")
        a_fw = actual.get("firmware_version")
        if d_fw is not None and a_fw is not None and a_fw != d_fw:
            items.append(
                _item(
                    design,
                    severity="minor",
                    category="firmware",
                    expected=str(d_fw),
                    actual=str(a_fw),
                    remediation=(
                        f"Firmware mismatch: expected '{d_fw}', found '{a_fw}'. "
                        "Flash the approved firmware version."
                    ),
                )
            )

        # 4. LOCATION CHECK — actual rack/position vs design rack/position.
        loc_item = _location_item(design, actual)
        if loc_item is not None:
            items.append(loc_item)

        # 5. NETWORK CHECK — LLDP neighbors vs the cable schedule.
        items.extend(_network_items(design, actual))

        # 6. POWER CHECK — connected power source vs design.
        d_power = design.get("power_source")
        a_power = actual.get("power_source")
        if d_power is not None and a_power is not None and a_power != d_power:
            items.append(
                _item(
                    design,
                    severity="major",
                    category="power",
                    expected=str(d_power),
                    actual=str(a_power),
                    remediation=(
                        f"Wrong power source: expected feed '{d_power}', found "
                        f"'{a_power}'. Re-cable to the designed power whip / PDU."
                    ),
                )
            )

        # 7. TEST CHECK — every non-passing TestResult in history.
        items.extend(
            _test_items(design, test_results.get(device_id, []), safety_test_types)
        )

    # Finally: discovered devices that are NOT in the design.
    for key, actual in actual_devices.items():
        if key in matched_actual_keys:
            continue
        # An actual dict may be reachable under both its id and name keys; only
        # emit one item per discovered device.
        if _already_flagged_unexpected(actual, actual_devices, matched_actual_keys, key):
            continue
        items.append(
            _item(
                actual,
                severity="info",
                category="identity",
                expected="NOT IN DESIGN",
                actual=actual.get("device_type_slug", "") or "",
                remediation=(
                    "Discovered a device that is not in the design. Confirm it is "
                    "intended; if so, add it to the design (BIM/NetBox), otherwise "
                    "remove it."
                ),
            )
        )
        matched_actual_keys.add(key)

    return items


def _lookup_actual(design: dict, actual_devices: dict) -> dict | None:
    """Find the discovered counterpart of a design device by id then name."""
    device_id = design.get("device_id")
    name = design.get("name")
    if device_id is not None and str(device_id) in actual_devices:
        return actual_devices[str(device_id)]
    if device_id is not None and device_id in actual_devices:
        return actual_devices[device_id]
    if name is not None and name in actual_devices:
        return actual_devices[name]
    return None


def _actual_keys(design: dict, actual_devices: dict) -> set:
    """All keys in actual_devices that resolve to this design device."""
    keys: set = set()
    device_id = design.get("device_id")
    name = design.get("name")
    for candidate in (device_id, str(device_id) if device_id is not None else None, name):
        if candidate is not None and candidate in actual_devices:
            keys.add(candidate)
    return keys


def _already_flagged_unexpected(
    actual: dict, actual_devices: dict, matched: set, key: str
) -> bool:
    """True if another key already pointed at this same discovered dict."""
    for other_key, other in actual_devices.items():
        if other_key == key:
            continue
        if other is actual and other_key in matched:
            return True
    return False


def _location_item(design: dict, actual: dict) -> PunchListItem | None:
    d_rack = design.get("rack")
    d_pos = design.get("position")
    a_rack = actual.get("rack")
    a_pos = actual.get("position")

    rack_wrong = d_rack is not None and a_rack is not None and a_rack != d_rack
    pos_wrong = d_pos is not None and a_pos is not None and a_pos != d_pos
    if not (rack_wrong or pos_wrong):
        return None

    expected = f"{d_rack if d_rack is not None else '?'}/{d_pos if d_pos is not None else '?'}"
    found = f"{a_rack if a_rack is not None else '?'}/{a_pos if a_pos is not None else '?'}"
    return _item(
        design,
        severity="major",
        category="identity",
        expected=expected,
        actual=found,
        remediation=(
            f"Wrong location: design places this device at {expected} (rack/U) "
            f"but it was found at {found}. Relocate it or correct the design."
        ),
    )


def _network_items(design: dict, actual: dict) -> list[PunchListItem]:
    """Compare each interface's connected endpoint (LLDP vs cable schedule)."""
    expected_map = design.get("lldp_neighbors")
    actual_map = actual.get("lldp_neighbors")
    if expected_map is None or actual_map is None:
        return []

    items: list[PunchListItem] = []
    for iface in sorted(set(expected_map) | set(actual_map)):
        expected_ep = expected_map.get(iface)
        actual_ep = actual_map.get(iface)
        if expected_ep == actual_ep:
            continue
        items.append(
            _item(
                design,
                severity="major",
                category="network",
                expected=f"{iface} -> {expected_ep if expected_ep is not None else 'unconnected'}",
                actual=f"{iface} -> {actual_ep if actual_ep is not None else 'unconnected'}",
                remediation=(
                    f"Wrong network connection on {iface}: cable schedule expects "
                    f"'{expected_ep}', LLDP reports '{actual_ep}'. Re-patch to the "
                    "designed endpoint."
                ),
            )
        )
    return items


def _test_items(
    design: dict,
    results: Iterable[TestResult],
    safety_test_types: frozenset,
) -> list[PunchListItem]:
    """One PunchListItem per non-passed TestResult; severity by safety class."""
    items: list[PunchListItem] = []
    slug = (design.get("device_type_slug") or "").lower()
    for result in results:
        if result.status == "passed":
            continue
        safety = _is_safety_test(slug, result.test_name, safety_test_types)
        severity = "critical" if safety else "major"
        items.append(
            _item(
                design,
                severity=severity,
                category="test_failure",
                expected="passed",
                actual=result.status or "not passed",
                remediation=(
                    f"Test '{result.test_name}' did not pass (status="
                    f"'{result.status}'"
                    + (f"; abort: {result.abort_reason}" if result.abort_triggered else "")
                    + "). Investigate and re-run after remediation."
                ),
                test_id=result.test_id or None,
            )
        )
    return items


def _is_safety_test(slug: str, test_name: str, safety_test_types: frozenset) -> bool:
    """Classify a test as safety-critical.

    Rule: safety when the design device's ``device_type_slug`` is in
    ``safety_test_types`` (e.g. a UPS/ATS/breaker/relay/dc_bus device), OR when
    the ``test_name`` (lower-cased) contains any token in ``safety_test_types``
    (e.g. an "ats_transfer" test regardless of device). Anything else
    (cooling/environmental) is non-safety.
    """
    if slug and slug in safety_test_types:
        return True
    name = (test_name or "").lower()
    return any(token in name for token in safety_test_types)


def _item(
    device: dict,
    *,
    severity: str,
    category: str,
    expected: str,
    actual: str,
    remediation: str,
    test_id: str | None = None,
) -> PunchListItem:
    """Build a PunchListItem, pulling identity fields off ``device``."""
    return PunchListItem(
        severity=severity,
        category=category,
        device_id=str(device.get("device_id", "") or ""),
        device_name=device.get("name", "") or "",
        site=device.get("site", "") or "",
        rack=device.get("rack", "") or "",
        expected=expected,
        actual=actual,
        source=SOURCE,
        remediation=remediation,
        test_id=test_id,
        status="open",
    )


async def reconcile_from_netbox(
    netbox_url: str,
    netbox_token: str,
    *,
    client: httpx.AsyncClient | None = None,
    actual_devices: dict | None = None,
    test_results: dict | None = None,
    page_size: int = 1000,
    safety_test_types: frozenset = SAFETY_TEST_TYPES,
) -> list[PunchListItem]:
    """Read design devices from NetBox and reconcile against the actual state.

    Thin async adapter: fetches every active device from ``/api/dcim/devices/``
    (paginated like Module 2), projects each into the plain design dict that
    ``reconcile`` consumes, then delegates.

    Args:
        netbox_url: Base URL of NetBox.
        netbox_token: NetBox API token.
        client: Optional injected httpx.AsyncClient (for tests).
        actual_devices: Discovered/actual state (see ``reconcile``). Defaults to
            empty, in which case every design device is reported NOT FOUND.
        test_results: Mapping of device_id → list[TestResult]. Optional.
        page_size: Results per page. NetBox default is 50; we use 1000.
        safety_test_types: Passed through to ``reconcile``.
    """
    headers = {"Authorization": f"Token {netbox_token}"}

    if client is None:
        async with httpx.AsyncClient(base_url=netbox_url, timeout=30.0) as c:
            design = await _fetch_design(c, headers, page_size)
    else:
        design = await _fetch_design(client, headers, page_size)

    return reconcile(
        design,
        actual_devices or {},
        test_results,
        safety_test_types=safety_test_types,
    )


async def _fetch_design(
    client: httpx.AsyncClient,
    headers: dict,
    page_size: int,
) -> list[dict]:
    design: list[dict] = []
    url = "/api/dcim/devices/"
    params: dict | None = {"status": "active", "limit": page_size}

    while url:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        for raw in data.get("results", []):
            design.append(_to_design_device(raw))

        # NetBox returns a fully-qualified URL in "next"; params are embedded in
        # it after the first page, so clear them (mirrors netbox_reader).
        url = data.get("next")
        params = None

    return design


def _to_design_device(raw: dict) -> dict:
    """Project a NetBox device record into the plain design dict for reconcile."""
    ctx = raw.get("config_context") or {}
    device_type = raw.get("device_type") or {}
    site = raw.get("site") or {}
    rack = raw.get("rack") or {}

    return {
        "device_id": str(raw.get("id", "")),
        "name": raw.get("name") or "",
        "device_type_slug": device_type.get("slug", "") if isinstance(device_type, dict) else "",
        "site": site.get("name", "") if isinstance(site, dict) else "",
        "rack": rack.get("name", "") if isinstance(rack, dict) else "",
        "position": int(raw.get("position") or 0),
        "firmware_version": ctx.get("firmware_version"),
        "power_source": ctx.get("power_source"),
        "lldp_neighbors": ctx.get("lldp_neighbors"),
    }
