"""Module 15: Report Generator.

Assembles a commissioning report from a punch list and an attestation chain
summary (contracts spec §4.4 /punchlist/summary shape, §5 attestation). Three
renderers consume one report model:

  * ``build_report_data``  — pure, dependency-free report assembly + counts.
  * ``generate_pdf``       — renders the model to a PDF via reportlab.
  * ``generate_json``      — JSON export of the model.
  * ``generate_certificate`` — the attestation certificate (also served by the
    API /attestation/certificate endpoint).

``build_report_data`` is the easily-tested core and has no reportlab dependency;
reportlab is imported lazily inside ``generate_pdf`` so this module imports even
where reportlab is absent.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass

from .timeutil import iso_now
from .types import PunchListItem


HASH_ALGORITHM = "SHA-256"

# Fixed enumerations get a zero baseline so absent buckets still report 0
# (mirrors the /punchlist/summary contract shape). Categories are open-ended,
# so by_category is built dynamically.
SEVERITIES = ("critical", "major", "minor", "info")
STATUSES = ("open", "acknowledged", "resolved", "deferred")


def build_report_data(
    facility,
    punch_items: list[PunchListItem],
    attestation_summary: dict,
    *,
    generated_at: str | None = None,
) -> dict:
    """Assemble the commissioning report model. Pure; no reportlab dependency.

    Args:
        facility: Facility name/identifier this report covers.
        punch_items: The punch list to summarize and enumerate.
        attestation_summary: Output of ``AttestationEngine.verify_chain`` — a
            dict with ``chain_valid``, ``breaks`` and ``length`` keys.
        generated_at: ISO-8601 timestamp to stamp the report with. Defaults to
            ``src.timeutil.iso_now()``.

    Returns:
        A JSON-serializable report model: facility, generated_at, summary
        (totals + by_severity/by_category/by_status counts), certificate, and
        the list of items.
    """
    by_severity: dict[str, int] = {s: 0 for s in SEVERITIES}
    by_status: dict[str, int] = {s: 0 for s in STATUSES}
    by_category: dict[str, int] = {}

    for item in punch_items:
        by_severity[item.severity] = by_severity.get(item.severity, 0) + 1
        by_status[item.status] = by_status.get(item.status, 0) + 1
        by_category[item.category] = by_category.get(item.category, 0) + 1

    return {
        "facility": facility,
        "generated_at": generated_at or iso_now(),
        "summary": {
            "total": len(punch_items),
            "by_severity": by_severity,
            "by_category": by_category,
            "by_status": by_status,
        },
        "certificate": generate_certificate(facility, attestation_summary),
        "items": [_item_to_dict(item) for item in punch_items],
    }


def generate_certificate(facility, attestation_summary: dict) -> dict:
    """Build the attestation certificate for a facility.

    Shared with the API /attestation/certificate endpoint. ``chain_length`` and
    ``chain_valid`` are taken from an ``AttestationEngine.verify_chain`` result.
    """
    return {
        "facility": facility,
        "chain_length": attestation_summary["length"],
        "chain_valid": attestation_summary["chain_valid"],
        "hash_algorithm": HASH_ALGORITHM,
    }


def generate_json(report_data: dict) -> str:
    """Serialize a report model to indented JSON. ``default=str`` for safety."""
    return json.dumps(report_data, indent=2, default=str)


def generate_pdf(report_data: dict) -> bytes:
    """Render a report model to a PDF and return its bytes.

    reportlab is imported lazily so the module imports without it. Robust to an
    empty punch list — the items section is simply omitted. The returned bytes
    always start with ``b"%PDF"``.
    """
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Spacer, Table, Paragraph

    styles = getSampleStyleSheet()
    facility = report_data.get("facility", "")
    summary = report_data.get("summary", {}) or {}
    certificate = report_data.get("certificate", {}) or {}
    items = report_data.get("items", []) or []

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        title=f"Commissioning Report — {facility}",
    )
    story: list = []

    # --- Title block ---------------------------------------------------------
    story.append(Paragraph("Commissioning Report", styles["Title"]))
    story.append(Paragraph(f"Facility: {_esc(facility)}", styles["Heading2"]))
    story.append(
        Paragraph(f"Generated: {_esc(report_data.get('generated_at', ''))}", styles["Normal"])
    )
    story.append(Spacer(1, 0.25 * inch))

    # --- Summary table (counts by severity) ----------------------------------
    story.append(Paragraph("Summary", styles["Heading2"]))
    story.append(
        Paragraph(f"Total punch list items: {summary.get('total', 0)}", styles["Normal"])
    )
    story.append(Spacer(1, 0.1 * inch))

    by_severity = summary.get("by_severity", {}) or {}
    sev_rows = [["Severity", "Count"]]
    for sev in SEVERITIES:
        sev_rows.append([sev, str(by_severity.get(sev, 0))])
    # Include any non-standard severities that slipped in.
    for sev, count in by_severity.items():
        if sev not in SEVERITIES:
            sev_rows.append([sev, str(count)])

    sev_table = Table(sev_rows, hAlign="LEFT", colWidths=[2.5 * inch, 1.5 * inch])
    sev_table.setStyle(_table_style(colors))
    story.append(sev_table)
    story.append(Spacer(1, 0.3 * inch))

    # --- One section per punch item ------------------------------------------
    story.append(Paragraph("Punch List Items", styles["Heading2"]))
    if not items:
        story.append(Paragraph("No punch list items recorded.", styles["Normal"]))
    for item in items:
        _append_item_section(story, item, styles, colors, inch, Paragraph, Spacer, Table, _table_style)

    story.append(Spacer(1, 0.3 * inch))

    # --- Attestation certificate ---------------------------------------------
    story.append(Paragraph("Attestation Certificate", styles["Heading2"]))
    cert_rows = [
        ["Facility", str(certificate.get("facility", ""))],
        ["Chain length", str(certificate.get("chain_length", ""))],
        ["Chain valid", str(certificate.get("chain_valid", ""))],
        ["Hash algorithm", str(certificate.get("hash_algorithm", ""))],
    ]
    cert_table = Table(cert_rows, hAlign="LEFT", colWidths=[2.0 * inch, 4.0 * inch])
    cert_table.setStyle(_table_style(colors))
    story.append(cert_table)

    doc.build(story)
    return buffer.getvalue()


# ----------------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------------
def _item_to_dict(item: PunchListItem) -> dict:
    """Normalize a punch item (dataclass or dict) to a plain dict."""
    if is_dataclass(item) and not isinstance(item, type):
        return asdict(item)
    if isinstance(item, dict):
        return dict(item)
    return dict(vars(item))


def _esc(value) -> str:
    """Escape text for reportlab Paragraph (XML mini-markup)."""
    text = "" if value is None else str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _table_style(colors):
    from reportlab.platypus import TableStyle

    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
    )


def _append_item_section(
    story, item, styles, colors, inch, Paragraph, Spacer, Table, table_style
):
    """Render a single punch item as a labelled key/value table."""
    item_id = item.get("id", "")
    severity = item.get("severity", "")
    story.append(Spacer(1, 0.12 * inch))
    story.append(
        Paragraph(
            f"[{_esc(severity)}] {_esc(item.get('category', ''))} — {_esc(item_id)}",
            styles["Heading3"],
        )
    )
    rows = [
        ["ID", item.get("id", "")],
        ["Severity", item.get("severity", "")],
        ["Category", item.get("category", "")],
        ["Device", _device_label(item)],
        ["Expected", item.get("expected", "")],
        ["Actual", item.get("actual", "")],
        ["Status", item.get("status", "")],
        ["Remediation", item.get("remediation", "")],
        ["Evidence hash", item.get("evidence_hash", "") or ""],
    ]
    # Wrap long free-text values in Paragraphs so they flow within the cell.
    wrapped = [[label, Paragraph(_esc(value), styles["Normal"])] for label, value in rows]
    table = Table(wrapped, hAlign="LEFT", colWidths=[1.4 * inch, 5.0 * inch])
    table.setStyle(table_style(colors))
    story.append(table)


def _device_label(item: dict) -> str:
    """Human-friendly device identifier: name (id) when both present."""
    name = item.get("device_name", "") or ""
    device_id = item.get("device_id", "") or ""
    if name and device_id:
        return f"{name} ({device_id})"
    return name or device_id
