"""PDF generation for customer-facing Quote / Contract / Invoice / Receipt.

Layout matches the BMDW reference receipt — centered logo, big company name,
italic section headers, simple bordered tables, no marketing colors.

WeasyPrint native libs (cairo, pango) only needed at PDF-render time; the
import is lazy so the rest of the app boots fine without it.

Logos embed via base64 data URIs (works locally + on Render — `file://` URIs
are unreliable across WeasyPrint versions and container filesystems).
"""

import base64
import mimetypes
from datetime import date
from pathlib import Path
from typing import Optional, Tuple

from server.schemas import CostBucket, Quote
from tools.outputs.contract_drafter import draft_contract_text
from tools.storage.paths import data_dir


def _pdf_dir() -> Path:
    p = data_dir() / "pdfs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ensure_dir(quote_id: str) -> Path:
    out = _pdf_dir() / quote_id
    out.mkdir(parents=True, exist_ok=True)
    return out


def is_configured() -> bool:
    """Return True if WeasyPrint is importable on this machine."""
    try:
        import weasyprint  # noqa: F401
        return True
    except Exception:
        return False


# ---- Logo loader -------------------------------------------------------

def _logo_data_uri(company: dict) -> str:
    """Find the logo and return it as a base64 data URI (works in WeasyPrint
    everywhere). Empty string if no logo found.

    Search order:
    1. <persistent data dir>/branding/logo.png  (uploaded via Settings page)
    2. <project root>/<company.logo_path>       (committed to git)
    """
    candidates = []
    candidates.append(data_dir() / "branding" / "logo.png")
    candidates.append(data_dir() / "branding" / "logo.jpg")
    candidates.append(data_dir() / "branding" / "logo.jpeg")
    logo_rel = company.get("logo_path", "config/branding/logo.png")
    project_root = Path(__file__).resolve().parents[2]
    candidates.append((project_root / logo_rel).resolve())

    for path in candidates:
        if not path.exists():
            continue
        try:
            mime = mimetypes.guess_type(str(path))[0] or "image/png"
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{b64}"
        except Exception:
            continue
    return ""


# ---- Shared header block (matches reference receipt layout) ------------

def _header_html(company: dict, doc_label: str, q: Quote, today: date,
                 invoice_number: Optional[str] = None) -> str:
    legal = company.get("legal_name", "BLACK MOUNTAIN DIRT WORKS")
    owner = company.get("owner_name", "Michael MacKrell")
    phone = company.get("phone", "")
    email = company.get("email", "")
    inv_num = invoice_number or q.quote_id
    logo_uri = _logo_data_uri(company)

    logo_html = (
        f'<img class="brand-logo" src="{logo_uri}" alt="BMDW logo" />'
        if logo_uri else ""
    )

    # Multi-line address handling — split customer.address on commas if it
    # contains them, else show as single line.
    addr_parts = [p.strip() for p in (q.customer.address or "").split(",") if p.strip()]
    addr_lines = "<br>".join(addr_parts) if addr_parts else "&nbsp;"

    return f"""
<div class="brand-block">
  {logo_html}
  <div class="brand-name">{legal}</div>
</div>

<div class="owner-info">
  {owner}<br>
  Phone: {phone}<br>
  Email: {email}
</div>

<div class="doc-section">
  <div class="doc-label">{doc_label}</div>
  <div class="doc-meta">Invoice #: {inv_num}</div>
  <div class="doc-meta">Date Issued: {today.strftime("%B %d, %Y")}</div>
</div>

<div class="doc-section">
  <div class="section-label">BILL TO:</div>
  <div class="bill-to">
    {q.customer.name}<br>
    {addr_lines}
    {('<br>Phone: ' + q.customer.phone) if q.customer.phone else ''}
    {('<br>Email: ' + q.customer.email) if q.customer.email else ''}
  </div>
</div>
"""


# ---- Shared CSS — clean professional look, no marketing colors ---------

_BASE_CSS = """
@page { size: Letter; margin: 0.7in 0.75in; }
body { font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Helvetica, Arial, sans-serif;
       color: #111; font-size: 11pt; line-height: 1.45; }

.brand-block { text-align: center; margin-bottom: 22px; }
.brand-logo { max-height: 130px; max-width: 280px; margin: 0 auto 8px;
              display: block; }
.brand-name { font-size: 22pt; font-weight: 800; letter-spacing: 0.04em;
              color: #111; line-height: 1.1; text-transform: uppercase; }

.owner-info { font-size: 10.5pt; color: #222; margin-bottom: 22px;
              line-height: 1.5; }

.doc-section { margin-bottom: 18px; }
.doc-label { font-size: 14pt; font-weight: 800; font-style: italic;
             color: #111; margin-bottom: 6px; letter-spacing: 0.02em; }
.doc-meta { font-size: 11pt; color: #222; }
.section-label { font-size: 12.5pt; font-weight: 800; font-style: italic;
                 color: #111; margin-bottom: 6px; letter-spacing: 0.02em; }
.bill-to { font-size: 11pt; color: #222; line-height: 1.5; }

.work-desc { font-size: 11pt; color: #222; margin-bottom: 10px; }

table.summary { width: 100%; border-collapse: collapse; margin-top: 6px;
                margin-bottom: 18px; }
table.summary th { background: #cfd5dd; color: #111; font-weight: 700;
                   text-align: left; padding: 8px 12px; font-size: 11pt;
                   border: 1px solid #9ca3af; }
table.summary th.num { text-align: right; }
table.summary td { padding: 8px 12px; font-size: 11pt; color: #111;
                   border: 1px solid #9ca3af; }
table.summary td.num { text-align: right; }
table.summary tr.total td { font-weight: 700; background: #f1f3f5; }

table.lines { width: 100%; border-collapse: collapse; margin-top: 6px;
              margin-bottom: 18px; }
table.lines th { background: #cfd5dd; color: #111; font-weight: 700;
                 text-align: left; padding: 6px 10px; font-size: 10pt;
                 border: 1px solid #9ca3af; text-transform: uppercase;
                 letter-spacing: 0.04em; }
table.lines th.num { text-align: right; }
table.lines td { padding: 6px 10px; font-size: 10pt; color: #111;
                 border: 1px solid #9ca3af; }
table.lines td.num { text-align: right; }
table.lines tr.bucket-row td { background: #e9ecef; font-weight: 700;
                                font-size: 10pt; text-transform: uppercase;
                                letter-spacing: 0.04em; }

.payment-block { font-size: 11pt; color: #222; line-height: 1.7;
                 margin-bottom: 10px; }
.outstanding { font-size: 11pt; font-weight: 700; color: #111;
               margin-bottom: 14px; }

p.note { font-size: 11pt; color: #222; line-height: 1.5; margin-top: 14px; }

pre { font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Helvetica, Arial, sans-serif;
      font-size: 10.5pt; white-space: pre-wrap; line-height: 1.5; margin: 0;
      color: #222; }
"""


def _wrap(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>{_BASE_CSS}</style>
</head><body>
{body}
</body></html>"""


# ---- Document-specific bodies ------------------------------------------

def _short_scope_line(q: Quote) -> str:
    """One-line description of work for the top of summary docs."""
    if q.name:
        return q.name
    return " · ".join(li.label for li in q.line_items) or "Excavation work"


def _quote_html(q: Quote, company: dict, today: date) -> str:
    """Customer-facing quote: ONE 'Services' line with the lump-sum total.
    Customer never sees the bucket breakdown — that's internal only."""
    deposit = q.customer_total * (company.get("deposit_pct", 50.0) / 100)
    remaining = q.customer_total - deposit

    # Single-row services table — lump sum only
    services_table = (
        '<table class="summary"><thead><tr>'
        '<th>Description</th><th class="num">Amount (CAD)</th>'
        '</tr></thead><tbody>'
        f'<tr><td>Services</td><td class="num">${q.customer_total:,.2f}</td></tr>'
        '</tbody></table>'
    )

    # Payment summary — total / deposit / remaining
    payment_rows = [
        '<tr><td>Total project cost (incl. tax)</td>'
        f'<td class="num">${q.customer_total:,.2f}</td></tr>',
    ]
    if deposit > 0:
        payment_rows.append(
            '<tr><td>Deposit required (50%) — due before mobilization</td>'
            f'<td class="num">${deposit:,.2f}</td></tr>'
        )
        payment_rows.append(
            '<tr class="total"><td>Remaining balance — due upon completion</td>'
            f'<td class="num">${remaining:,.2f}</td></tr>'
        )

    payment_table = (
        '<table class="summary"><thead><tr>'
        '<th>Description</th><th class="num">Amount (CAD)</th>'
        '</tr></thead>'
        f'<tbody>{"".join(payment_rows)}</tbody></table>'
    )

    body = (
        _header_html(company, "QUOTE", q, today)
        + '<div class="doc-section"><div class="section-label">DESCRIPTION OF WORK:</div>'
        f'<div class="work-desc">{_short_scope_line(q)}</div></div>'
        + services_table
        + '<div class="section-label">Payment summary</div>'
        + payment_table
        + f'<p class="note"><strong>Terms.</strong> '
          f'{company.get("quote_terms", "Final invoice amount paid upon completion. Deposit of 50% required before equipment is mobilized.")}'
          f'</p>'
    )
    return _wrap(f"Quote {q.quote_id}", body)


def _contract_html(q: Quote, company: dict, body_text: str, today: date) -> str:
    safe_body = body_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body = (
        _header_html(company, "CONTRACT", q, today)
        + f"<pre>{safe_body}</pre>"
    )
    return _wrap(f"Contract {q.quote_id}", body)


def _invoice_html(q: Quote, company: dict, today: date,
                  deposit_received: float, deposit_received_date: Optional[date]) -> str:
    total = q.customer_total
    paid = deposit_received or 0
    outstanding = max(0.0, total - paid)

    summary_rows = [
        '<tr><td>Total project cost (incl. tax)</td>'
        f'<td class="num">${total:,.2f}</td></tr>',
    ]
    if paid > 0:
        date_str = (' on ' + deposit_received_date.strftime("%B %d, %Y")) if deposit_received_date else ''
        summary_rows.append(
            f'<tr><td>Less: Deposit received{date_str}</td>'
            f'<td class="num">−${paid:,.2f}</td></tr>'
        )
    summary_rows.append(
        '<tr class="total"><td>Outstanding balance — DUE NOW</td>'
        f'<td class="num">${outstanding:,.2f}</td></tr>'
    )

    summary_table = (
        '<table class="summary"><thead><tr>'
        '<th>Description</th><th class="num">Amount (CAD)</th>'
        '</tr></thead>'
        f'<tbody>{"".join(summary_rows)}</tbody></table>'
    )

    body = (
        _header_html(company, "INVOICE", q, today)
        + '<div class="doc-section"><div class="section-label">DESCRIPTION OF WORK:</div>'
        f'<div class="work-desc">{_short_scope_line(q)}</div></div>'
        + '<div class="section-label">Payment summary</div>'
        + summary_table
        + f'<p class="note"><strong>Payment terms.</strong> '
          f'Outstanding balance is due upon completion of the project. '
          f'Payment by e-transfer or cheque to {company.get("email", "")} / '
          f'{company.get("legal_name", "BMDW")}.</p>'
    )
    return _wrap(f"Invoice {q.quote_id}", body)


def _internal_list_html(q: Quote, company: dict, today: date,
                        bucket: CostBucket, list_label: str,
                        intro_text: str) -> str:
    """Internal-only material takeoff or equipment list.
    NOT customer-facing — Michael uses these to source materials / mobilize gear."""
    sections_html = []
    grand_total = 0.0

    for li in q.line_items:
        entries = [e for e in li.entries if e.bucket == bucket]
        if not entries:
            continue

        rows = []
        proj_total = 0.0
        for e in entries:
            cat = f' <span style="color:#666;font-size:9pt;">[{e.catalogue_sku}]</span>' if e.catalogue_sku else ""
            rows.append(
                f'<tr><td>{e.description}{cat}</td>'
                f'<td class="num">{e.quantity:g}</td>'
                f'<td>{e.unit}</td>'
                f'<td class="num">${e.unit_cost:,.2f}</td>'
                f'<td class="num">${e.total_cost:,.2f}</td></tr>'
            )
            proj_total += e.total_cost
        grand_total += proj_total

        rows.append(
            f'<tr class="total"><td colspan="4"><strong>Subtotal — {li.label}</strong></td>'
            f'<td class="num"><strong>${proj_total:,.2f}</strong></td></tr>'
        )

        sections_html.append(
            f'<div class="section-label">{li.label}</div>'
            '<table class="lines"><thead><tr>'
            '<th>Item</th><th class="num">Qty</th><th>Unit</th>'
            '<th class="num">Unit Cost</th><th class="num">Total</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table>'
        )

    if not sections_html:
        sections_html.append(
            f'<p class="note">No {bucket.value} entries on this quote.</p>'
        )
    else:
        sections_html.append(
            f'<div class="outstanding">Grand total ({list_label.lower()}): '
            f'${grand_total:,.2f} CAD</div>'
        )

    body = (
        _header_html(company, list_label, q, today)
        + '<div class="doc-section"><div class="section-label">DESCRIPTION OF WORK:</div>'
        f'<div class="work-desc">{_short_scope_line(q)}</div></div>'
        + f'<p class="note"><strong>Internal use only.</strong> {intro_text}</p>'
        + "".join(sections_html)
    )
    return _wrap(f"{list_label} {q.quote_id}", body)


def _receipt_html(q: Quote, company: dict, today: date,
                  amount_received: float,
                  receipt_kind: str,  # "deposit" or "final"
                  received_date: Optional[date]) -> str:
    """Receipt for a payment received — deposit OR final. Matches the BMDW
    reference receipt layout closely."""
    rcv_date = received_date or today
    rcv_str = rcv_date.strftime("%B %d, %Y")

    if receipt_kind == "deposit":
        row_label = "50% Deposit — Excavation Work"
        status_line = "Status: Paid (Deposit Received)"
        outstanding = q.customer_total - amount_received
    else:
        row_label = "Final Payment — Excavation Work"
        status_line = "Status: Paid (Final Payment Received)"
        outstanding = max(0.0, q.customer_total - (q.customer_total * 0.5) - amount_received)

    summary_table = (
        '<table class="summary"><thead><tr>'
        '<th>Description</th><th class="num">Amount (CAD)</th>'
        '</tr></thead>'
        f'<tbody><tr><td>{row_label}</td>'
        f'<td class="num">${amount_received:,.2f}</td></tr>'
        '</tbody></table>'
    )

    payment_block = (
        '<div class="payment-block">'
        f'Total Paid: ${amount_received:,.2f} CAD<br>'
        f'Payment Received: {rcv_str}<br>'
        f'{status_line}'
        '</div>'
    )

    if outstanding > 0:
        outstanding_block = (
            '<div class="outstanding">'
            f'Outstanding Balance: ${outstanding:,.2f} CAD '
            '(Due upon completion)</div>'
        )
        note = (
            f'<p class="note">The remaining balance of ${outstanding:,.2f} CAD '
            f'is due upon completion of the project.</p>'
        )
    else:
        outstanding_block = (
            '<div class="outstanding">Outstanding Balance: $0.00 CAD '
            '(Project paid in full)</div>'
        )
        note = (
            '<p class="note">Thank you for your business — '
            'project paid in full.</p>'
        )

    body = (
        _header_html(company, "RECEIPT", q, today)
        + '<div class="doc-section"><div class="section-label">DESCRIPTION OF WORK:</div>'
        f'<div class="work-desc">{_short_scope_line(q)}</div></div>'
        + summary_table
        + payment_block
        + outstanding_block
        + note
    )
    return _wrap(f"Receipt {q.quote_id}", body)


# ---- Public API ----------------------------------------------------------

def render_quote_pdf(q: Quote, company: dict) -> Tuple[Optional[Path], Optional[str]]:
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as exc:
        return None, f"WeasyPrint unavailable: {exc}"
    out_path = _ensure_dir(q.quote_id) / "quote.pdf"
    HTML(string=_quote_html(q, company, date.today())).write_pdf(str(out_path))
    return out_path, None


def render_contract_pdf(q: Quote, company: dict, body_text: Optional[str] = None) -> Tuple[Optional[Path], Optional[str]]:
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as exc:
        return None, f"WeasyPrint unavailable: {exc}"
    out_path = _ensure_dir(q.quote_id) / "contract.pdf"
    text = body_text or q.contract_text or draft_contract_text(q, company)
    HTML(string=_contract_html(q, company, text, date.today())).write_pdf(str(out_path))
    return out_path, None


def render_invoice_pdf(q: Quote, company: dict,
                       deposit_received: float = 0.0,
                       deposit_received_date: Optional[date] = None
                       ) -> Tuple[Optional[Path], Optional[str]]:
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as exc:
        return None, f"WeasyPrint unavailable: {exc}"
    out_path = _ensure_dir(q.quote_id) / "invoice.pdf"
    HTML(string=_invoice_html(q, company, date.today(),
                              deposit_received, deposit_received_date)).write_pdf(str(out_path))
    return out_path, None


def render_material_takeoff_pdf(q: Quote, company: dict) -> Tuple[Optional[Path], Optional[str]]:
    """Internal material takeoff list — what to source / pick up before mobilizing."""
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as exc:
        return None, f"WeasyPrint unavailable: {exc}"
    out_path = _ensure_dir(q.quote_id) / "material-takeoff.pdf"
    html = _internal_list_html(
        q, company, date.today(), CostBucket.MATERIALS, "MATERIAL TAKEOFF",
        "Materials to source / pick up for this job. Includes catalogue SKUs "
        "where available so you can match against supplier orders."
    )
    HTML(string=html).write_pdf(str(out_path))
    return out_path, None


def render_equipment_list_pdf(q: Quote, company: dict) -> Tuple[Optional[Path], Optional[str]]:
    """Internal equipment list — what gear to mobilize for this job."""
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as exc:
        return None, f"WeasyPrint unavailable: {exc}"
    out_path = _ensure_dir(q.quote_id) / "equipment-list.pdf"
    html = _internal_list_html(
        q, company, date.today(), CostBucket.EQUIPMENT, "EQUIPMENT LIST",
        "Equipment to mobilize for this job. Confirm availability and book "
        "rentals/transport before the start date."
    )
    HTML(string=html).write_pdf(str(out_path))
    return out_path, None


def render_receipt_pdf(q: Quote, company: dict,
                       amount_received: float,
                       receipt_kind: str = "deposit",
                       received_date: Optional[date] = None,
                       ) -> Tuple[Optional[Path], Optional[str]]:
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as exc:
        return None, f"WeasyPrint unavailable: {exc}"
    suffix = "deposit" if receipt_kind == "deposit" else "final"
    out_path = _ensure_dir(q.quote_id) / f"receipt-{suffix}.pdf"
    HTML(string=_receipt_html(q, company, date.today(),
                              amount_received, receipt_kind, received_date)).write_pdf(str(out_path))
    return out_path, None
