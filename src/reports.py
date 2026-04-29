"""Module 15: Report Generator.

Produces a commissioning report from a punch list + attestation chain summary.
Three output formats per spec:

  - PDF: human-readable summary, severity-grouped, with attestation footer
  - JSON: machine-readable export of every PunchListItem
  - Attestation certificate: small JSON declaring chain length, root hash,
    last hash, hash algorithm

Uses ReportLab for PDF generation (no LaTeX, no external services).

The PDF generator returns bytes so callers can write to disk, stream over
HTTP, or upload to MinIO without going through a tempfile.
"""

from __future__ import annotations

import io
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Sequence

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .types import PunchListItem


SEVERITY_ORDER = ["critical", "major", "minor", "info"]
SEVERITY_COLORS = {
    "critical": colors.HexColor("#b00020"),
    "major": colors.HexColor("#f57c00"),
    "minor": colors.HexColor("#1976d2"),
    "info": colors.HexColor("#616161"),
}


@dataclass
class AttestationSummary:
    facility: str
    chain_length: int
    first_hash: str
    last_hash: str
    chain_valid: bool
    hash_algorithm: str = "SHA-256"


def punchlist_to_json(items: Sequence[PunchListItem]) -> str:
    """Serialize a punch list as pretty-printed JSON."""
    return json.dumps([asdict(i) for i in items], indent=2, default=str)


def attestation_certificate(summary: AttestationSummary) -> dict:
    """Stand-alone certificate the auditor can verify independently."""
    return {
        "facility": summary.facility,
        "chain_length": summary.chain_length,
        "first_hash": summary.first_hash,
        "last_hash": summary.last_hash,
        "chain_valid": summary.chain_valid,
        "hash_algorithm": summary.hash_algorithm,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def generate_pdf(
    items: Sequence[PunchListItem],
    attestation: AttestationSummary,
    *,
    title: str = "Commissioning Report",
    subtitle: str | None = None,
) -> bytes:
    """Render a punch list + attestation summary as a PDF and return bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER, leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        title=title,
    )
    styles = getSampleStyleSheet()
    flow = []

    flow.append(Paragraph(title, styles["Title"]))
    if subtitle:
        flow.append(Paragraph(subtitle, styles["Heading2"]))
    flow.append(Paragraph(
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        styles["Normal"],
    ))
    flow.append(Spacer(1, 0.2 * inch))

    flow.append(Paragraph("Summary", styles["Heading1"]))
    counts = _severity_counts(items)
    summary_rows = [["Severity", "Count"]] + [
        [s, str(counts.get(s, 0))] for s in SEVERITY_ORDER
    ]
    summary_table = Table(summary_rows, hAlign="LEFT")
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#222")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    flow.append(summary_table)
    flow.append(Spacer(1, 0.2 * inch))

    # Per-severity sections.
    for severity in SEVERITY_ORDER:
        section_items = [i for i in items if i.severity == severity]
        if not section_items:
            continue
        flow.append(Paragraph(severity.upper(), styles["Heading2"]))
        rows = [["Device", "Category", "Expected", "Actual", "Remediation"]]
        for item in section_items:
            rows.append([
                _wrap(item.device_name or item.device_id, styles),
                _wrap(item.category, styles),
                _wrap(item.expected, styles),
                _wrap(item.actual, styles),
                _wrap(item.remediation, styles),
            ])
        t = Table(rows, repeatRows=1, colWidths=[1.4 * inch, 0.9 * inch, 1.4 * inch, 1.4 * inch, 2.5 * inch])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), SEVERITY_COLORS[severity]),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        flow.append(t)
        flow.append(Spacer(1, 0.15 * inch))

    # Attestation footer.
    flow.append(Spacer(1, 0.2 * inch))
    flow.append(Paragraph("Attestation", styles["Heading1"]))
    cert = attestation_certificate(attestation)
    cert_rows = [[k, str(v)] for k, v in cert.items()]
    cert_table = Table(cert_rows, hAlign="LEFT", colWidths=[1.5 * inch, 5 * inch])
    cert_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
    ]))
    flow.append(cert_table)

    doc.build(flow)
    return buf.getvalue()


def _severity_counts(items: Sequence[PunchListItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.severity] = counts.get(item.severity, 0) + 1
    return counts


def _wrap(text: str, styles) -> Paragraph:
    """Wrap long strings so PDF table cells don't overflow."""
    return Paragraph(str(text or ""), styles["BodyText"])
