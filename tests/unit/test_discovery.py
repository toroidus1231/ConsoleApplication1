"""Tests for Module 11 — Discovery Scanner.

Probes are injected as fakes returning canned identities per IP, so no test
touches the network. The httpx parts mirror test_netbox_reader.py's
MockTransport pattern.
"""

import httpx

from src.discovery import DiscoveryResult, classify, expand_subnets, scan


# NetBox-shaped device types. Includes both the nested-manufacturer form and the
# flattened-string form to exercise classify's dict-or-str handling.
DEVICE_TYPES = [
    {"slug": "cm2000", "manufacturer": {"name": "Schneider"}, "model": "CM2000"},
    {"slug": "ex4300", "manufacturer": {"name": "Juniper"}, "model": "EX4300"},
    {"slug": "poweredge-r640", "manufacturer": "Dell", "model": "PowerEdge R640"},
]


def _probe_returning(identities_by_ip: dict[str, str | None]):
    """Build a probe coroutine that returns a canned identity keyed by IP."""

    async def probe(ip: str) -> str | None:
        return identities_by_ip.get(ip)

    return probe


# --------------------------------------------------------------------------- #
# classify (pure)
# --------------------------------------------------------------------------- #
def test_classify_substring_match_case_insensitive():
    # sysDescr-style string containing the model somewhere in the middle.
    identity = "Linux switch-01 Juniper EX4300-48T running JunOS"
    assert classify(identity, DEVICE_TYPES) == "ex4300"


def test_classify_exact_match_preferred_over_substring():
    # "Dell" is a substring of this identity, but an exact device-type label
    # also exists; the exact match must win regardless of list order.
    device_types = [
        {"slug": "dell-generic", "manufacturer": "Dell", "model": "Server"},
        {"slug": "poweredge-r640", "manufacturer": "Dell", "model": "PowerEdge R640"},
    ]
    identity = "Dell PowerEdge R640"
    assert classify(identity, device_types) == "poweredge-r640"


def test_classify_flattened_manufacturer_string():
    assert classify("Dell PowerEdge R640 BMC", DEVICE_TYPES) == "poweredge-r640"


def test_classify_no_match_returns_none():
    assert classify("Cisco Catalyst 9300", DEVICE_TYPES) is None
    assert classify("", DEVICE_TYPES) is None


# --------------------------------------------------------------------------- #
# expand_subnets (pure)
# --------------------------------------------------------------------------- #
def test_expand_subnets_slash30_host_count():
    # A /30 has exactly 2 usable hosts.
    ips = expand_subnets(["192.168.1.0/30"])
    assert ips == ["192.168.1.1", "192.168.1.2"]


def test_expand_subnets_bare_ip_and_dedupe():
    ips = expand_subnets(["10.0.0.5", "10.0.0.5", "10.0.0.6"])
    assert ips == ["10.0.0.5", "10.0.0.6"]


# --------------------------------------------------------------------------- #
# scan with injected fakes
# --------------------------------------------------------------------------- #
async def test_scan_mixed_classified_and_unknown():
    # .1 -> snmp identity that classifies as ex4300
    # .2 -> redfish identity that classifies as poweredge-r640
    # .3 -> snmp identity that matches nothing -> "unknown"
    snmp = _probe_returning(
        {
            "192.168.1.1": "Juniper EX4300 switch",
            "192.168.1.3": "Some Unknown Box 9000",
        }
    )
    redfish = _probe_returning({"192.168.1.2": "Dell PowerEdge R640"})
    modbus = _probe_returning({})  # nothing responds on modbus
    bacnet = _probe_returning({})

    result = await scan(
        ["192.168.1.0/29"],
        probes={
            "snmp": snmp,
            "modbus_tcp": modbus,
            "bacnet_ip": bacnet,
            "redfish": redfish,
        },
        device_types=DEVICE_TYPES,
        timeout=0.5,
    )

    assert isinstance(result, DiscoveryResult)
    assert result.scan_id  # uuid present
    assert result.devices_found == 3
    assert result.devices_classified == 2
    assert result.devices_unmatched == 1
    assert result.errors == []

    by_ip = {(d["ip"], d["protocol"]): d for d in result.devices}
    assert by_ip[("192.168.1.1", "snmp")]["device_type_slug"] == "ex4300"
    assert by_ip[("192.168.1.1", "snmp")]["classified"] is True
    assert by_ip[("192.168.1.2", "redfish")]["device_type_slug"] == "poweredge-r640"

    unknown = by_ip[("192.168.1.3", "snmp")]
    assert unknown["device_type_slug"] == "unknown"
    assert unknown["classified"] is False
    assert unknown["identity"] == "Some Unknown Box 9000"


async def test_scan_single_ip_responds_on_two_protocols():
    # 10.0.0.1 answers on BOTH snmp and redfish -> two independent records.
    snmp = _probe_returning({"10.0.0.1": "Juniper EX4300"})
    redfish = _probe_returning({"10.0.0.1": "Dell PowerEdge R640"})
    none_probe = _probe_returning({})

    result = await scan(
        ["10.0.0.1/32"],
        probes={
            "snmp": snmp,
            "modbus_tcp": none_probe,
            "bacnet_ip": none_probe,
            "redfish": redfish,
        },
        device_types=DEVICE_TYPES,
        timeout=0.5,
    )

    assert result.devices_found == 2
    assert result.devices_classified == 2
    protocols = sorted(d["protocol"] for d in result.devices)
    assert protocols == ["redfish", "snmp"]
    assert all(d["ip"] == "10.0.0.1" for d in result.devices)


async def test_scan_probe_raising_is_captured_in_errors():
    async def boom(ip: str) -> str | None:
        raise RuntimeError("connection reset")

    ok = _probe_returning({"10.0.0.1": "Juniper EX4300"})
    none_probe = _probe_returning({})

    result = await scan(
        ["10.0.0.1/32"],
        probes={
            "snmp": ok,
            "modbus_tcp": boom,
            "bacnet_ip": none_probe,
            "redfish": none_probe,
        },
        device_types=DEVICE_TYPES,
        timeout=0.5,
    )

    # Scan still completes and the good probe still produced a record.
    assert result.devices_found == 1
    assert result.devices[0]["device_type_slug"] == "ex4300"
    assert len(result.errors) == 1
    err = result.errors[0]
    assert err["ip"] == "10.0.0.1"
    assert err["protocol"] == "modbus_tcp"
    assert "RuntimeError" in err["error"]


# --------------------------------------------------------------------------- #
# device-type sourcing
# --------------------------------------------------------------------------- #
async def test_scan_with_device_types_provided_makes_no_http_call():
    # If a netbox_client were consulted it would raise; passing device_types
    # directly must skip HTTP entirely.
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP should not be called when device_types is provided")

    snmp = _probe_returning({"10.0.0.1": "Juniper EX4300"})
    none_probe = _probe_returning({})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        result = await scan(
            ["10.0.0.1/32"],
            probes={
                "snmp": snmp,
                "modbus_tcp": none_probe,
                "bacnet_ip": none_probe,
                "redfish": none_probe,
            },
            device_types=DEVICE_TYPES,
            netbox_client=c,
            timeout=0.5,
        )

    assert result.devices_found == 1


async def test_scan_fetches_device_types_via_mock_transport():
    page1 = {
        "results": [
            {"slug": "cm2000", "manufacturer": {"name": "Schneider"}, "model": "CM2000"}
        ],
        "next": "http://nb/api/dcim/device-types/?limit=1000&offset=1000",
    }
    page2 = {
        "results": [
            {"slug": "ex4300", "manufacturer": {"name": "Juniper"}, "model": "EX4300"}
        ],
        "next": None,
    }
    pages = iter([page1, page2])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Token tok"
        assert request.url.path == "/api/dcim/device-types/"
        return httpx.Response(200, json=next(pages))

    snmp = _probe_returning({"10.0.0.1": "Juniper EX4300 switch"})
    none_probe = _probe_returning({})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        result = await scan(
            ["10.0.0.1/32"],
            probes={
                "snmp": snmp,
                "modbus_tcp": none_probe,
                "bacnet_ip": none_probe,
                "redfish": none_probe,
            },
            netbox_token="tok",
            netbox_client=c,
            timeout=0.5,
        )

    # Device types came from NetBox (both pages), and classification used them.
    assert result.devices_found == 1
    assert result.devices_classified == 1
    assert result.devices[0]["device_type_slug"] == "ex4300"
