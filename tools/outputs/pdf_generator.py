"""PDF generation for customer-facing Quote / Contract / Invoice / Receipt.

Uses WeasyPrint to render HTML → PDF. WeasyPrint depends on native libs
(cairo, pango, gdk-pixbuf) that may not be installed in every environment;
the import is lazy so the rest of the app boots fine without it.

Output goes to <data_dir>/pdfs/<quote_id>/{quote,contract,invoice,receipt}.pdf
and the path is returned. These files are attached when emailing the customer.

All four documents share the same BMDW header (legal name, owner-operator,
phone, email) and a "Bill To" block. The payment-summary mini-spreadsheet is
the same shape for Quote / Invoice / Receipt — the labels and what's
highlighted differ per document.
"""

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


# ---- Shared header block ------------------------------------------------

def _header_html(company: dict, doc_label: str, q: Quote, today: date,
                 invoice_number: Optional[str] = None) -> str:
    """Standard header used on every document type."""
    legal = company.get("legal_name", "BLACK MOUNTAIN DIRT WORKS")
    owner = company.get("owner_name", "Michael MacKrell")
    title = company.get("owner_title", "Owner Operator")
    phone = company.get("phone", "")
    email = company.get("email", "")
    addr = company.get("address", "")
    inv_num = invoice_number or q.quote_id

    return f"""
<div class="header">
  <div class="brand">
    <div class="brand-name">{legal}</div>
    <div class="brand-owner">{owner}, {title}</div>
    <div class="brand-meta">{phone} &nbsp;·&nbsp; {email}</div>
    <div class="brand-meta">{addr}</div>
  </div>
  <div class="doc-info">
    <div class="doc-label">{doc_label}</div>
    <div class="doc-number">#{inv_num}</div>
    <div class="doc-date">Date issued: {today.isoformat()}</div>
  </div>
</div>

<div class="bill-to">
  <div class="bill-to-label">BILL TO</div>
  <div class="bill-to-name">{q.customer.name}</div>
  <div class="bill-to-line">{q.customer.address or ''}</div>
  <div class="bill-to-line">{q.customer.phone or ''}</div>
  <div class="bill-to-line">{q.customer.email or ''}</div>
</div>
"""


def _payment_summary_html(q: Quote, deposit_received: float = 0.0,
                          final_received: float = 0.0,
                          deposit_received_date: Optional[date] = None,
                          final_received_date: Optional[date] = None) -> str:
    """Mini-spreadsheet of totals + payment status. Same shape on quote/invoice/receipt."""
    total = q.customer_total
    deposit_required = total * 0.50  # default 50% deposit
    paid_total = (deposit_received or 0) + (final_received or 0)
    outstanding = total - paid_total

    rows = [
        ("Total project cost (incl. tax)", f"${total:,.2f}"),
    ]
    if deposit_required > 0:
        rows.append(("Deposit required (50%)", f"${deposit_required:,.2f}"))
    if deposit_received > 0:
        rows.append((f"Deposit received{(' on ' + deposit_received_date.isoformat()) if deposit_received_date else ''}",
                     f"$({deposit_received:,.2f})"))
    if final_received > 0:
        rows.append((f"Final payment received{(' on ' + final_received_date.isoformat()) if final_received_date else ''}",
                     f"$({final_received:,.2f})"))
    rows.append(("OUTSTANDING BALANCE", f"${max(0, outstanding):,.2f}"))

    row_html = "".join(
        f'<tr class="{"summary-total" if "OUTSTANDING" in label else ""}">'
        f'<td>{label}</td><td class="num">{val}</td></tr>'
        for label, val in rows
    )

    return f"""
<table class="summary">
  <thead><tr><th>Description</th><th class="num">Amount (CAD)</th></tr></thead>
  <tbody>{row_html}</tbody>
</table>
"""


# ---- Shared CSS ---------------------------------------------------------

_BASE_CSS = """
@page { size: Letter; margin: 0.5in 0.6in; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       color: #111; font-size: 11pt; }

.header { display: flex; justify-content: space-between;
          border-bottom: 3px solid #0a0f1a; padding-bottom: 14px;
          margin-bottom: 18px; }
.brand-name { font-size: 22pt; font-weight: 800; letter-spacing: 0.04em;
              color: #0a0f1a; line-height: 1; }
.brand-owner { font-size: 11pt; color: #444; margin-top: 4px; }
.brand-meta { font-size: 10pt; color: #555; margin-top: 1px; }

.doc-info { text-align: right; }
.doc-label { font-size: 18pt; font-weight: 800; color: #0a0f1a;
             text-transform: uppercase; letter-spacing: 0.06em; }
.doc-number { font-size: 13pt; color: #333; margin-top: 2px; }
.doc-date { font-size: 10pt; color: #555; margin-top: 4px; }

.bill-to { background: #f5f7fa; padding: 12px 16px; border-radius: 6px;
           margin-bottom: 18px; }
.bill-to-label { font-size: 9pt; text-transform: uppercase; letter-spacing: 0.1em;
                 color: #666; margin-bottom: 4px; }
.bill-to-name { font-size: 13pt; font-weight: 700; color: #111; }
.bill-to-line { font-size: 10pt; color: #444; margin-top: 1px; }

h2 { font-size: 12pt; font-weight: 700; color: #0a0f1a; text-transform: uppercase;
     letter-spacing: 0.06em; border-bottom: 1px solid #ccc; padding-bottom: 4px;
     margin-top: 22px; margin-bottom: 8px; }

.summary { width: 100%; border-collapse: collapse; margin-top: 10px; }
.summary th, .summary td { padding: 8px 10px; border-bottom: 1px solid #e5e7eb; }
.summary th { background: #0a0f1a; color: white; font-size: 9pt; text-align: left;
              text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }
.summary th.num, .summary td.num { text-align: right; }
.summary tr.summary-total td { font-weight: 800; font-size: 12pt; background: #f0f3f7;
                                color: #0a0f1a; border-top: 2px solid #0a0f1a; }

.line-table { width: 100%; border-collapse: collapse; margin-top: 6px; }
.line-table th, .line-table td { padding: 5px 8px; border-bottom: 1px solid #e5e7eb;
                                  font-size: 10pt; }
.line-table th { background: #f5f7fa; color: #444; font-weight: 700;
                 text-align: left; font-size: 9pt; text-transform: uppercase;
                 letter-spacing: 0.04em; }
.line-table td.num, .line-table th.num { text-align: right; }
.bucket-header td { background: #ebeef3; font-weight: 700; color: #0a0f1a;
                    font-size: 10pt; text-transform: uppercase;
                    letter-spacing: 0.04em; padding-top: 8px; }

.terms { font-size: 10pt; color: #444; margin-top: 18px; line-height: 1.5;
         padding: 12px 14px; background: #fafbfc; border-left: 3px solid #0a0f1a; }
pre { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 10pt; white-space: pre-wrap; line-height: 1.5; margin: 0; }

.footer { margin-top: 24px; padding-top: 10px; border-top: 1px solid #ddd;
          font-size: 9pt; color: #777; text-align: center; }
"""


def _wrap(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>{_BASE_CSS}</style>
</head><body>
{body}
</body></html>"""


# ---- Document templates -------------------------------------------------

def _quote_html(q: Quote, company: dict, today: date) -> str:
    BUCKET_ORDER = [CostBucket.EQUIPMENT, CostBucket.MATERIALS,
                    CostBucket.LABOUR, CostBucket.TRUCKING, CostBucket.SPOIL]

    line_rows = []
    for li in q.line_items:
        for bucket in BUCKET_ORDER:
            entries = [e for e in li.entries if e.bucket == bucket]
            if not entries:
                continue
            line_rows.append(
                f'<tr class="bucket-header"><td colspan="3">'
                f'{bucket.value.upper()} — {li.label}</td></tr>'
            )
            for e in entries:
                line_rows.append(
                    f"<tr><td>{e.description}</td>"
                    f'<td class="num">{e.quantity:g} {e.unit}</td>'
                    f'<td class="num">${e.total_cost:,.2f}</td></tr>'
                )
    line_table = (
        '<table class="line-table"><thead><tr><th>Item</th>'
        '<th class="num">Qty</th><th class="num">Subtotal</th></tr></thead>'
        f'<tbody>{"".join(line_rows)}</tbody></table>'
    )

    body = (
        _header_html(company, "QUOTE", q, today)
        + "<h2>Description of Work</h2>"
        + "<div>" + " · ".join(li.label for li in q.line_items) + "</div>"
        + "<h2>Line items</h2>"
        + line_table
        + "<h2>Payment Summary</h2>"
        + _payment_summary_html(q)
        + f'<div class="terms"><strong>Terms.</strong> '
          f'{company.get("quote_terms", "Final invoice amount paid upon completion. Deposit of 50% required before equipment is mobilized.")}'
          f'</div>'
        + f'<div class="footer">{company.get("legal_name", "BMDW")} '
          f'· WCB # {company.get("wcb_number", "—")} '
          f'· {company.get("phone", "")} · {company.get("email", "")}</div>'
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
    body = (
        _header_html(company, "INVOICE", q, today)
        + "<h2>Description of Work</h2>"
        + "<div>" + " · ".join(li.label for li in q.line_items) + "</div>"
        + "<h2>Payment Summary</h2>"
        + _payment_summary_html(q,
                                deposit_received=deposit_received,
                                deposit_received_date=deposit_received_date)
        + '<div class="terms"><strong>Payment terms.</strong> '
          'Outstanding balance is due upon completion of the project. '
          'Payment by e-transfer or cheque to '
          f'{company.get("email", "")} / {company.get("legal_name", "BMDW")}.</div>'
        + f'<div class="footer">{company.get("legal_name", "BMDW")} '
          f'· {company.get("phone", "")} · {company.get("email", "")}</div>'
    )
    return _wrap(f"Invoice {q.quote_id}", body)


def _receipt_html(q: Quote, company: dict, today: date,
                  amount_received: float,
                  receipt_kind: str,  # "deposit" or "final"
                  received_date: Optional[date]) -> str:
    """Receipt for a payment received — deposit OR final."""
    receipt_label = "Deposit Receipt" if receipt_kind == "deposit" else "Final Payment Receipt"
    paid_total_for_summary = amount_received

    if receipt_kind == "deposit":
        summary = _payment_summary_html(q,
                                        deposit_received=paid_total_for_summary,
                                        deposit_received_date=received_date)
    else:
        # Final payment — assume 50% deposit was already paid
        deposit_amount = q.customer_total * 0.50
        summary = _payment_summary_html(q,
                                        deposit_received=deposit_amount,
                                        final_received=paid_total_for_summary,
                                        final_received_date=received_date)

    status_block = f"""
<div class="terms">
  <strong>Payment status.</strong>
  Received from <strong>{q.customer.name}</strong>
  on <strong>{(received_date or today).isoformat()}</strong>:
  <strong>${amount_received:,.2f} CAD</strong>
  ({receipt_kind.upper()} payment for {q.quote_id}).<br>
  Status: <strong>PAID</strong>.
</div>
"""

    body = (
        _header_html(company, receipt_label.upper(), q, today)
        + "<h2>Description of Work</h2>"
        + "<div>" + (" · ".join(li.label for li in q.line_items) or "—")
        + (f" — {receipt_kind.upper()} payment" if receipt_kind == "deposit" else "")
        + "</div>"
        + "<h2>Payment Summary</h2>"
        + summary
        + status_block
        + f'<div class="footer">{company.get("legal_name", "BMDW")} '
          f'· {company.get("phone", "")} · {company.get("email", "")}</div>'
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
