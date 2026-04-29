"""Tests for Module 15 — Report Generator."""

import json

from src.reports import (
    AttestationSummary,
    attestation_certificate,
    generate_pdf,
    punchlist_to_json,
)
from src.types import PunchListItem


def _items() -> list[PunchListItem]:
    return [
        PunchListItem(
            severity="critical", category="identity",
            device_id="d1", device_name="cm2000-A3",
            site="DC1", rack="A3",
            expected="cm2000", actual="NOT FOUND",
            source="reconciliation",
            remediation="Verify power and network.",
        ),
        PunchListItem(
            severity="major", category="firmware",
            device_id="d2", device_name="ex4300-N1",
            site="DC1", rack="N1",
            expected="3.2.1", actual="2.0.0",
            source="reconciliation",
            remediation="Update firmware.",
        ),
        PunchListItem(
            severity="minor", category="firmware",
            device_id="d3", device_name="opt100-T1",
            site="DC1", rack="T1",
            expected="2.5.0", actual="2.4.9",
            source="reconciliation",
            remediation="Update firmware.",
        ),
    ]


def _summary() -> AttestationSummary:
    return AttestationSummary(
        facility="DC1-Ashburn",
        chain_length=12345,
        first_hash="0" * 64,
        last_hash="abcd" * 16,
        chain_valid=True,
    )


# --- JSON export -------------------------------------------------------------


def test_punchlist_to_json_round_trips():
    items = _items()
    s = punchlist_to_json(items)
    parsed = json.loads(s)
    assert len(parsed) == 3
    assert parsed[0]["severity"] == "critical"
    assert parsed[1]["severity"] == "major"
    assert all("device_id" in row for row in parsed)


def test_punchlist_to_json_pretty_printed():
    s = punchlist_to_json(_items())
    assert "\n" in s and "  " in s  # indented


# --- Certificate -------------------------------------------------------------


def test_attestation_certificate_has_required_fields():
    cert = attestation_certificate(_summary())
    for key in ("facility", "chain_length", "first_hash", "last_hash",
                "chain_valid", "hash_algorithm", "generated_at"):
        assert key in cert
    assert cert["hash_algorithm"] == "SHA-256"


def test_attestation_certificate_serializes_as_json():
    cert = attestation_certificate(_summary())
    encoded = json.dumps(cert)
    assert "DC1-Ashburn" in encoded


# --- PDF generation ----------------------------------------------------------


def test_generate_pdf_returns_pdf_bytes():
    pdf = generate_pdf(_items(), _summary())
    assert pdf.startswith(b"%PDF-")
    # ReportLab outputs at least a few KB for any non-trivial doc.
    assert len(pdf) > 1500


def test_generate_pdf_with_empty_punch_list():
    pdf = generate_pdf([], _summary())
    assert pdf.startswith(b"%PDF-")


def test_generate_pdf_includes_facility_name_and_severity_sections():
    """ReportLab compresses text streams, so we extract with pdfplumber."""
    import io
    import pdfplumber

    pdf = generate_pdf(_items(), _summary())
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        text = "\n".join((page.extract_text() or "") for page in doc.pages)

    assert "DC1-Ashburn" in text
    assert "CRITICAL" in text
    assert "MAJOR" in text
    assert "MINOR" in text
    assert "cm2000-A3" in text
    assert "Update firmware" in text


def test_generate_pdf_omits_severity_section_when_empty():
    import io
    import pdfplumber
    only_critical = [_items()[0]]
    pdf = generate_pdf(only_critical, _summary())
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        text = "\n".join((page.extract_text() or "") for page in doc.pages)
    assert "CRITICAL" in text
    # MAJOR/MINOR/INFO sections should not appear since their counts are 0.
    assert "MAJOR" not in text
    assert "MINOR" not in text


def test_generate_pdf_with_invalid_chain_renders():
    invalid = AttestationSummary(
        facility="DC1", chain_length=10,
        first_hash="0" * 64, last_hash="x" * 64,
        chain_valid=False,
    )
    pdf = generate_pdf(_items(), invalid)
    assert pdf.startswith(b"%PDF-")
