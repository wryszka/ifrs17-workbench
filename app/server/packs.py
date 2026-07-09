"""Sign-off certificate PDF — built from the as-at evidence snapshot at sign-off time.

Pure fpdf2 + stdlib. The certificate is the auditor's anchor: the key figures, the engine
runs that produced them, the input Delta table versions, the approved assumption versions
and the curve dates — plus a SHA-256 of the evidence JSON. Reproduce any number by
time-travelling to the pinned versions and re-running the same engine.
"""
import hashlib
import json

from fpdf import FPDF


def _m(v):
    try:
        return f"EUR {float(v):,.2f}"
    except (TypeError, ValueError):
        return "-"


class _Cert(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(30, 41, 59)
        self.cell(0, 5, "BRICKSURANCE SE - IFRS 17 CLOSE SIGN-OFF CERTIFICATE", align="L")
        self.set_font("Helvetica", "", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 5, "Synthetic demo - not a real insurer", align="R")
        self.ln(8)
        self.set_draw_color(37, 99, 235)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(148, 163, 184)
        self.cell(0, 5, f"Generated from gov_signoff_certificates evidence - page {self.page_no()}", align="C")


def _title(pdf, t):
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B", 10.5)
    pdf.set_text_color(37, 99, 235)
    pdf.cell(0, 7, t.upper())
    pdf.ln(8)


def _kv(pdf, k, v):
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(70, 5.5, str(k)[:52])
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(15, 23, 42)
    pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin - 70, 5.5, str(v if v not in (None, "") else "-")[:300])


def build_certificate(cert_id: str, period: str, signed_by: str, evidence: dict) -> tuple[bytes, str]:
    """Returns (pdf_bytes, sha256_of_evidence)."""
    blob = json.dumps(evidence, sort_keys=True, default=str)
    sha = hashlib.sha256(blob.encode()).hexdigest()

    pdf = _Cert()
    pdf.set_auto_page_break(True, margin=16)
    pdf.add_page()

    _title(pdf, f"Certificate {cert_id} - close period {period}")
    _kv(pdf, "Signed by", signed_by)
    _kv(pdf, "Signed at", evidence.get("signed_at"))
    _kv(pdf, "Evidence SHA-256", sha)
    pdf.ln(2)

    _title(pdf, "Key figures as signed")
    for k, v in (evidence.get("key_figures") or {}).items():
        _kv(pdf, k.replace("_", " "), _m(v))
    pdf.ln(2)

    _title(pdf, "Approvals")
    for a in (evidence.get("approvals") or []):
        _kv(pdf, a.get("workstream", "-"), f"{a.get('decision', '-')} by {a.get('approver', '-')} at {a.get('approved_at', '-')}")
    pdf.ln(2)

    _title(pdf, "Engine runs and pinned inputs (reproduce via time travel)")
    for r in (evidence.get("runs") or [])[:10]:
        _kv(pdf, f"{r.get('engine')} ({r.get('run_id', '')[:28]})",
            f"inputs {str(r.get('input_versions', ''))[:120]} | assumptions {str(r.get('assumption_versions', ''))[:80]}")
    pdf.ln(2)

    _title(pdf, "Assumption versions in force")
    for a in (evidence.get("assumptions") or []):
        _kv(pdf, f"{a.get('assumption_id')} v{a.get('version')}",
            f"approved by {a.get('approved_by')} on {a.get('approved_at')}")

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(100, 116, 139)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin, 4.6,
                   "Reproducibility: every figure above was produced by a governed engine run whose input "
                   "Delta table versions, assumption versions and curve dates are pinned in this evidence. "
                   "An auditor reproduces any number by reading the pinned versions (VERSION AS OF) and "
                   "re-running the same engine - the audit trail is a join, not a project.")
    return bytes(pdf.output()), sha
