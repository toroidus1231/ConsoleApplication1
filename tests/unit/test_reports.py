"""Tests for Module 15 — Report Generator.

Covers the pure report-assembly core (``build_report_data`` counts +
certificate), the reportlab PDF renderer (valid %PDF bytes, including the empty
punch-list case), the JSON export round-trip, and the standalone certificate
builder. Mostly plain sync tests; ``asyncio_mode="auto"`` is set globally.
"""

import json

from src.reports import (
    build_report_data,
    generate_certificate,
    generate_json,
    generate_pdf,
)
from src.types import PunchListItem


FACILITY = "DC1"

ATTESTATION_SUMMARY = {"chain_valid": True, "breaks": [], "length": 42}


def _sample_items() -> list[PunchListItem]:
    """A spread of severities, categories and statuses for count assertions."""
    return [
        PunchListItem(
            id="p-1",
            severity="critical",
            category="electrical",
            device_id="dev-1",
            device_name="PDU-A",
            expected="480.0 V",
            actual="0.0 V",
            remediation="Inspect breaker B12.",
            evidence_hash="abc123",
            status="open",
        ),
        PunchListItem(
            id="p-2",
            severity="major",
            category="electrical",
            device_id="dev-2",
            device_name="PDU-B",
            expected="closed",
            actual="open",
            status="acknowledged",
        ),
        PunchListItem(
            id="p-3",
            severity="minor",
            category="thermal",
            device_id="dev-3",
            device_name="CRAC-1",
            expected="22.0 C",
            actual="27.5 C",
            status="resolved",
        ),
        PunchListItem(
            id="p-4",
            severity="info",
            category="network",
            device_id="dev-4",
            device_name="SW-1",
            expected="up",
            actual="up",
            status="deferred",
        ),
        PunchListItem(
            id="p-5",
            severity="critical",
            category="thermal",
            device_id="dev-5",
            device_name="CRAC-2",
            expected="22.0 C",
            actual="40.0 C",
            status="open",
        ),
    ]


# --- build_report_data -------------------------------------------------------


def test_build_report_data_top_level_shape():
    data = build_report_data(FACILITY, _sample_items(), ATTESTATION_SUMMARY)
    assert set(data.keys()) == {
        "facility",
        "generated_at",
        "summary",
        "certificate",
        "items",
    }
    assert data["facility"] == FACILITY
    assert isinstance(data["generated_at"], str) and data["generated_at"]
    assert len(data["items"]) == 5


def test_build_report_data_generated_at_override():
    data = build_report_data(
        FACILITY, _sample_items(), ATTESTATION_SUMMARY, generated_at="2026-06-20T00:00:00Z"
    )
    assert data["generated_at"] == "2026-06-20T00:00:00Z"


def test_build_report_data_totals_and_counts_exact():
    data = build_report_data(FACILITY, _sample_items(), ATTESTATION_SUMMARY)
    summary = data["summary"]

    assert summary["total"] == 5
    # by_severity covers the full fixed enum, zero-filled.
    assert summary["by_severity"] == {
        "critical": 2,
        "major": 1,
        "minor": 1,
        "info": 1,
    }
    # by_status covers the full fixed enum, zero-filled.
    assert summary["by_status"] == {
        "open": 2,
        "acknowledged": 1,
        "resolved": 1,
        "deferred": 1,
    }
    # by_category is dynamic.
    assert summary["by_category"] == {
        "electrical": 2,
        "thermal": 2,
        "network": 1,
    }


def test_build_report_data_certificate_fields():
    data = build_report_data(FACILITY, _sample_items(), ATTESTATION_SUMMARY)
    cert = data["certificate"]
    assert cert["facility"] == FACILITY
    assert cert["chain_length"] == 42
    assert cert["chain_valid"] is True
    assert cert["hash_algorithm"] == "SHA-256"


def test_build_report_data_empty_punch_list():
    data = build_report_data(FACILITY, [], ATTESTATION_SUMMARY)
    summary = data["summary"]
    assert summary["total"] == 0
    assert summary["by_severity"] == {"critical": 0, "major": 0, "minor": 0, "info": 0}
    assert summary["by_status"] == {
        "open": 0,
        "acknowledged": 0,
        "resolved": 0,
        "deferred": 0,
    }
    assert summary["by_category"] == {}
    assert data["items"] == []


def test_build_report_data_items_carry_fields():
    data = build_report_data(FACILITY, _sample_items(), ATTESTATION_SUMMARY)
    first = data["items"][0]
    assert first["id"] == "p-1"
    assert first["severity"] == "critical"
    assert first["evidence_hash"] == "abc123"
    assert first["remediation"] == "Inspect breaker B12."


# --- generate_pdf ------------------------------------------------------------


def test_generate_pdf_returns_pdf_bytes():
    data = build_report_data(FACILITY, _sample_items(), ATTESTATION_SUMMARY)
    pdf = generate_pdf(data)
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


def test_generate_pdf_handles_empty_punch_list():
    data = build_report_data(FACILITY, [], ATTESTATION_SUMMARY)
    pdf = generate_pdf(data)
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


# --- generate_json -----------------------------------------------------------


def test_generate_json_round_trips():
    data = build_report_data(FACILITY, _sample_items(), ATTESTATION_SUMMARY)
    text = generate_json(data)
    assert isinstance(text, str)

    loaded = json.loads(text)
    assert set(loaded.keys()) == {
        "facility",
        "generated_at",
        "summary",
        "certificate",
        "items",
    }
    assert loaded["summary"]["total"] == 5
    assert loaded["certificate"]["chain_length"] == 42
    assert len(loaded["items"]) == 5


# --- generate_certificate ----------------------------------------------------


def test_generate_certificate_fields():
    cert = generate_certificate(FACILITY, ATTESTATION_SUMMARY)
    assert cert["facility"] == FACILITY
    assert cert["chain_length"] == 42
    assert cert["chain_valid"] is True
    assert cert["hash_algorithm"] == "SHA-256"


def test_generate_certificate_reflects_invalid_chain():
    cert = generate_certificate("DC2", {"chain_valid": False, "breaks": [3], "length": 7})
    assert cert["facility"] == "DC2"
    assert cert["chain_length"] == 7
    assert cert["chain_valid"] is False
    assert cert["hash_algorithm"] == "SHA-256"
