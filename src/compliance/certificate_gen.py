"""Commissioning certificate generator.

Produces a signed PDF certificate per device or per energization
boundary, suitable for handover to the customer's compliance file.
The cert includes:

  - Facility, device IDs, energization date.
  - Every test executed (name, date, status, peak value).
  - Reference to the attestation chain entry (last_hash + sequence).
  - Signing engineer + role.
  - Insurance / regulatory tags (NEC article numbers, NFPA refs).

The PDF is rendered via src/reports.py. The signature is a detached
HMAC-SHA256 over the canonical-JSON of the cert payload, stored in a
sidecar block on the PDF metadata. Verifiers can recompute the HMAC
to confirm the cert hasn't been tampered with.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class CommissioningCertificate:
    facility: str
    devices: list[str]
    energization_date: datetime
    tests_completed: list[dict]
    attestation_last_hash: str
    attestation_chain_length: int
    signed_by: str
    signed_by_role: str
    nfpa_refs: list[str] = field(default_factory=list)
    nec_refs: list[str] = field(default_factory=list)
    issued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cert_id: str = ""

    def canonical_json(self) -> bytes:
        payload = {
            "facility": self.facility,
            "devices": sorted(self.devices),
            "energization_date": self.energization_date.isoformat(),
            "tests_completed": sorted(
                self.tests_completed,
                key=lambda t: (t.get("device_id", ""), t.get("test_name", "")),
            ),
            "attestation_last_hash": self.attestation_last_hash,
            "attestation_chain_length": self.attestation_chain_length,
            "signed_by": self.signed_by,
            "signed_by_role": self.signed_by_role,
            "nfpa_refs": sorted(self.nfpa_refs),
            "nec_refs": sorted(self.nec_refs),
            "issued_at": self.issued_at.isoformat(),
            "cert_id": self.cert_id,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def sign(self, secret: bytes) -> str:
        return hmac.new(secret, self.canonical_json(),
                        hashlib.sha256).hexdigest()


def verify_certificate(cert: CommissioningCertificate, signature_hex: str,
                       secret: bytes) -> bool:
    expected = cert.sign(secret)
    return hmac.compare_digest(expected, signature_hex)


def render_certificate_pdf(cert: CommissioningCertificate,
                           signature_hex: str) -> bytes:
    """Render a one-page commissioning certificate. Body text + summary
    table + the signature in monospace at the bottom."""
    from io import BytesIO
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )
    from reportlab.lib import colors

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        title=f"Commissioning Certificate {cert.cert_id}",
    )
    styles = getSampleStyleSheet()
    flow = []
    flow.append(Paragraph("Commissioning Certificate", styles["Title"]))
    flow.append(Paragraph(f"Facility: <b>{cert.facility}</b>", styles["Normal"]))
    flow.append(Paragraph(
        f"Energization date: {cert.energization_date.isoformat()}",
        styles["Normal"]))
    flow.append(Paragraph(f"Cert ID: {cert.cert_id}", styles["Normal"]))
    flow.append(Spacer(1, 0.2 * inch))

    flow.append(Paragraph("Devices covered", styles["Heading2"]))
    flow.append(Paragraph(", ".join(sorted(cert.devices)), styles["Code"]))
    flow.append(Spacer(1, 0.2 * inch))

    flow.append(Paragraph("Tests completed", styles["Heading2"]))
    if cert.tests_completed:
        rows = [["Device", "Test", "Status", "Completed"]]
        for t in cert.tests_completed:
            rows.append([t.get("device_id", ""),
                         t.get("test_name", t.get("test_type", "")),
                         t.get("status", "passed" if t.get("passed") else "failed"),
                         t.get("completed_at", "")])
        tbl = Table(rows, hAlign="LEFT")
        tbl.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ]))
        flow.append(tbl)
    flow.append(Spacer(1, 0.2 * inch))

    flow.append(Paragraph("Attestation chain", styles["Heading2"]))
    flow.append(Paragraph(f"Length: {cert.attestation_chain_length}",
                          styles["Normal"]))
    flow.append(Paragraph(f"Last hash: <font face='Courier'>"
                          f"{cert.attestation_last_hash}</font>",
                          styles["Normal"]))
    flow.append(Spacer(1, 0.2 * inch))

    if cert.nfpa_refs or cert.nec_refs:
        flow.append(Paragraph("Regulatory references", styles["Heading2"]))
        if cert.nfpa_refs:
            flow.append(Paragraph(f"NFPA: {', '.join(cert.nfpa_refs)}",
                                  styles["Normal"]))
        if cert.nec_refs:
            flow.append(Paragraph(f"NEC: {', '.join(cert.nec_refs)}",
                                  styles["Normal"]))
        flow.append(Spacer(1, 0.2 * inch))

    flow.append(Paragraph(
        f"Signed by: <b>{cert.signed_by}</b> ({cert.signed_by_role}) "
        f"on {cert.issued_at.isoformat()}", styles["Normal"]))
    flow.append(Spacer(1, 0.1 * inch))
    flow.append(Paragraph(
        f"<font face='Courier' size='8'>HMAC-SHA256: {signature_hex}</font>",
        styles["Normal"]))

    doc.build(flow)
    return buf.getvalue()
