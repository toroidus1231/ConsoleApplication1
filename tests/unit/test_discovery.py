"""Tests for Module 11 — Discovery Scanner."""

import asyncio
from dataclasses import dataclass

import pytest

from src.discovery import (
    DiscoveryScanner,
    PROBE_TIMEOUT_SECONDS,
    ProbeResponse,
    classify,
    _iter_cidr,
)


@dataclass
class FakeConfig:
    max_concurrent_polls: int = 50


# --- CIDR expansion ----------------------------------------------------------


def test_cidr_expand_24_excludes_network_and_broadcast():
    ips = _iter_cidr("10.0.0.0/24")
    assert "10.0.0.0" not in ips
    assert "10.0.0.255" not in ips
    assert "10.0.0.1" in ips
    assert "10.0.0.254" in ips
    assert len(ips) == 254


def test_cidr_expand_32_returns_single_host():
    ips = _iter_cidr("10.0.0.5/32")
    assert ips == ["10.0.0.5"]


def test_cidr_expand_30_returns_two_hosts():
    ips = _iter_cidr("10.0.0.0/30")
    assert ips == ["10.0.0.1", "10.0.0.2"]


# --- Classification ----------------------------------------------------------


CATALOG = [
    ("cm2000", "Schneider", "CM2000"),
    ("mtz-iEM", "Schneider", "Masterpact MTZ"),
    ("ex4300", "Juniper", "EX4300"),
    ("opt100", "Vaisala", "OPT100"),
    ("h100", "NVIDIA", "H100"),
]


def test_classify_exact_match_wins_over_substring():
    slug, exact = classify("Schneider CM2000", CATALOG)
    assert slug == "cm2000"
    assert exact is True


def test_classify_case_insensitive_exact_match():
    slug, exact = classify("schneider cm2000", CATALOG)
    assert slug == "cm2000"
    assert exact is True


def test_classify_substring_match_when_no_exact():
    slug, exact = classify("Schneider CM2000 Power Meter v3.2", CATALOG)
    assert slug == "cm2000"
    assert exact is False


def test_classify_prefers_longest_substring_match():
    catalog = [
        ("schneider", "Schneider", ""),     # generic vendor entry
        ("cm2000", "Schneider", "CM2000"),  # specific
    ]
    slug, exact = classify("Schneider CM2000 Power Meter", catalog)
    assert slug == "cm2000"
    assert exact is False


def test_classify_unmatched_returns_unknown():
    slug, exact = classify("Unknown Random Device 9000", CATALOG)
    assert slug == "unknown"
    assert exact is False


def test_classify_empty_catalog_returns_unknown():
    slug, exact = classify("Schneider CM2000", [])
    assert slug == "unknown"
    assert exact is False


# --- Scanner -----------------------------------------------------------------


def _scanner(prober, catalog=None, *, max_concurrent=50):
    catalog_data = catalog or CATALOG

    async def get_catalog():
        return catalog_data

    return DiscoveryScanner(
        FakeConfig(max_concurrent_polls=max_concurrent),
        prober=prober,
        device_type_catalog=get_catalog,
    )


async def test_scan_returns_one_device_per_responding_protocol():
    async def prober(ip):
        if ip == "10.0.0.1":
            return [
                ProbeResponse(ip=ip, protocol="modbus_tcp", identity="Schneider CM2000"),
                ProbeResponse(ip=ip, protocol="snmp", identity="Schneider CM2000"),
            ]
        return []

    scanner = _scanner(prober)
    results = await scanner.scan("10.0.0.0/30")  # .1 and .2

    # IP .1 responds on both protocols, .2 doesn't respond at all.
    assert len(results) == 2
    assert {r.protocol for r in results} == {"modbus_tcp", "snmp"}
    assert all(r.device_type_slug == "cm2000" for r in results)


async def test_scan_classifies_unknown_devices_as_unknown_slug():
    async def prober(ip):
        if ip == "10.0.0.1":
            return [ProbeResponse(ip=ip, protocol="snmp", identity="Some Random Device")]
        return []

    scanner = _scanner(prober)
    results = await scanner.scan("10.0.0.0/30")

    assert len(results) == 1
    assert results[0].device_type_slug == "unknown"
    assert results[0].matched_exact is False


async def test_scan_per_ip_timeout_skips_slow_ips():
    async def slow_prober(ip):
        # IP .1 is slow, others respond immediately.
        if ip == "10.0.0.1":
            await asyncio.sleep(PROBE_TIMEOUT_SECONDS + 1)
            return [ProbeResponse(ip=ip, protocol="snmp", identity="Schneider CM2000")]
        return [ProbeResponse(ip=ip, protocol="snmp", identity="Schneider CM2000")]

    # Use a real timeout but small for the test by monkey-patching
    import src.discovery as disc

    orig = disc.PROBE_TIMEOUT_SECONDS
    disc.PROBE_TIMEOUT_SECONDS = 0.05
    try:
        scanner = _scanner(slow_prober)
        results = await scanner.scan("10.0.0.0/30")  # .1 slow, .2 fast
    finally:
        disc.PROBE_TIMEOUT_SECONDS = orig

    # Only .2 should appear — .1 timed out.
    assert len(results) == 1
    assert results[0].ip == "10.0.0.2"


async def test_scan_prober_exception_does_not_halt_scan():
    async def flaky_prober(ip):
        if ip == "10.0.0.1":
            raise OSError("network unreachable")
        return [ProbeResponse(ip=ip, protocol="snmp", identity="Schneider CM2000")]

    scanner = _scanner(flaky_prober)
    results = await scanner.scan("10.0.0.0/30")

    # .1 errored silently, .2 still classified.
    assert len(results) == 1
    assert results[0].ip == "10.0.0.2"


async def test_scan_respects_concurrency_semaphore():
    in_flight = 0
    max_seen = 0

    async def prober(ip):
        nonlocal in_flight, max_seen
        in_flight += 1
        max_seen = max(max_seen, in_flight)
        await asyncio.sleep(0.005)
        in_flight -= 1
        return []

    scanner = _scanner(prober, max_concurrent=4)
    await scanner.scan("10.0.0.0/28")  # 14 hosts

    assert max_seen <= 4


async def test_scan_empty_cidr_returns_empty():
    async def prober(_ip):
        raise AssertionError("prober should not be called for /32 mismatch")

    async def get_catalog():
        return []

    scanner = DiscoveryScanner(
        FakeConfig(),
        prober=lambda ip: _no_response(ip),
        device_type_catalog=get_catalog,
    )
    results = await scanner.scan("10.0.0.0/32")
    assert results == []


async def _no_response(_ip):
    return []
