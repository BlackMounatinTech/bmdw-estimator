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

from tools.dates import pacific_today as _pacific_today
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
/* ---- Apple / BMT-website aesthetic ------------------------------------
   Matches blackmountaintech.ca: SF Pro, pure black/white + gray scale, NO
   colors, hairline rules, negative letter-spacing on headings, weight 600
   (not 800), and lots of white space. Premium through restraint + air.
*/
@page { size: Letter; margin: 1.0in 0.9in; }
body { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display",
       "SF Pro Text", "Segoe UI", Helvetica, Arial, sans-serif;
       color: #262626; font-size: 10.5pt; line-height: 1.6;
       font-weight: 400; -webkit-font-smoothing: antialiased; }

/* ---- Brand header: quiet, centered, tons of air ---- */
.brand-block { text-align: center; margin-bottom: 54px; }
.brand-logo { max-height: 96px; max-width: 220px; margin: 0 auto 14px;
              display: block; }
.brand-name { font-size: 15pt; font-weight: 600; letter-spacing: -0.01em;
              color: #171717; line-height: 1.2; }

.owner-info { font-size: 9pt; color: #737373; margin-bottom: 44px;
              line-height: 1.7; letter-spacing: 0.01em; }

/* ---- Doc title: big, light, Apple-hero style ---- */
.doc-section { margin-bottom: 34px; }
.doc-label { font-size: 30pt; font-weight: 600; letter-spacing: -0.03em;
             color: #171717; margin-bottom: 14px; line-height: 1.05; }
.doc-meta { font-size: 9.5pt; color: #a3a3a3; line-height: 1.7;
            letter-spacing: 0.01em; }

/* ---- Section labels: small, uppercase, tracked-out (the Apple eyebrow) ---- */
.section-label { font-size: 8.5pt; font-weight: 600; text-transform: uppercase;
                 color: #a3a3a3; margin-bottom: 14px; letter-spacing: 0.12em; }
.bill-to { font-size: 10.5pt; color: #404040; line-height: 1.7; }

.work-desc { font-size: 11pt; color: #404040; margin-bottom: 14px;
             line-height: 1.65; }

/* ---- Tables: no fills, hairline rules only, generous padding ---- */
table.summary { width: 100%; border-collapse: collapse; margin: 10px 0 34px; }
table.summary th { background: transparent; color: #a3a3a3; font-weight: 600;
                   text-align: left; padding: 12px 4px; font-size: 8.5pt;
                   text-transform: uppercase; letter-spacing: 0.1em;
                   border: none; border-bottom: 1px solid #e5e5e5; }
table.summary th.num { text-align: right; }
table.summary td { padding: 14px 4px; font-size: 11pt; color: #262626;
                   border: none; border-bottom: 1px solid #f5f5f5; }
table.summary td.num { text-align: right; }
table.summary tr.total td { font-weight: 600; font-size: 12pt; color: #171717;
                            border-bottom: none; border-top: 1.5px solid #171717;
                            padding-top: 16px; }

table.lines { width: 100%; border-collapse: collapse; margin: 10px 0 34px; }
table.lines th { background: transparent; color: #a3a3a3; font-weight: 600;
                 text-align: left; padding: 10px 4px; font-size: 8pt;
                 border: none; border-bottom: 1px solid #e5e5e5;
                 text-transform: uppercase; letter-spacing: 0.1em; }
table.lines th.num { text-align: right; }
table.lines td { padding: 11px 4px; font-size: 10pt; color: #404040;
                 border: none; border-bottom: 1px solid #f5f5f5; }
table.lines td.num { text-align: right; }
table.lines tr.bucket-row td { background: transparent; font-weight: 600;
                                color: #737373; font-size: 8.5pt;
                                text-transform: uppercase; letter-spacing: 0.1em;
                                padding-top: 18px; }

.payment-block { font-size: 11pt; color: #404040; line-height: 1.9;
                 margin-bottom: 14px; }
.outstanding { font-size: 11pt; font-weight: 600; color: #171717;
               margin-bottom: 18px; }

p.note { font-size: 8.5pt; color: #a3a3a3; line-height: 1.7; margin-top: 40px;
         border-top: 1px solid #f5f5f5; padding-top: 20px; letter-spacing: 0.01em; }

/* ---- Plan: airy numbered list, hairline dividers between steps ---- */
ol.plan { font-size: 11pt; color: #262626; line-height: 1.6; margin: 8px 0 34px;
          padding: 0; list-style: none; counter-reset: step; }
ol.plan li { margin: 0; padding: 15px 0 15px 46px; position: relative;
             border-bottom: 1px solid #f5f5f5; counter-increment: step; }
ol.plan li:last-child { border-bottom: none; }
ol.plan li::before { content: counter(step, decimal-leading-zero);
                     position: absolute; left: 0; top: 15px;
                     font-size: 9pt; font-weight: 600; color: #d4d4d4;
                     letter-spacing: 0.02em; }

/* ---- Accept box: quiet framed panel, no color ---- */
.accept-box { border: 1px solid #e5e5e5; background: #fafafa; border-radius: 14px;
              padding: 28px 30px; margin: 20px 0 28px; }
.accept-title { font-size: 15pt; font-weight: 600; color: #171717;
                margin-bottom: 8px; letter-spacing: -0.02em; }
.accept-box p { font-size: 10.5pt; color: #525252; line-height: 1.65; }
.etransfer { font-size: 13pt; font-weight: 600; color: #171717; margin: 10px 0;
             letter-spacing: -0.01em; }

.plan-intro-box { background: transparent; padding: 0; margin: 6px 0 30px;
                  font-size: 12.5pt; color: #525252; line-height: 1.6;
                  font-weight: 400; letter-spacing: -0.01em; }

pre { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI",
      Helvetica, Arial, sans-serif; font-size: 10pt; white-space: pre-wrap;
      line-height: 1.75; margin: 0; color: #404040; }
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


# ---- Project-plan skeleton (the GUARDRAIL) -----------------------------
# Modelled on Michael's real BMDW project outlines. Every job, no matter the
# type, runs on the SAME fixed bookends — these are non-negotiable and can
# never be skipped. Only the MIDDLE (the actual work steps) changes per job.
#
#   OPENING (always, in this order):
#     1. Receive approval
#     2. Call BC 1 Call to locate underground utilities
#     3. Receive deposit
#     4. Mobilize equipment to site
#   ... real work steps, in dig/build sequence ...
#   CLOSING (always, in this order):
#     - Thorough site cleanup, haul away all waste and equipment
#     - Final walkthrough with the customer
#     - Receive final payment, and the project is done

_PLAN_OPENING = [
    "Receive approval to proceed",
    "Call BC 1 Call to locate all underground utilities",
    "Receive deposit and lock in the schedule",
    "Mobilize and truck equipment to site",
]

_PLAN_CLOSING = [
    "Complete a thorough site cleanup and haul away all waste and equipment",
    "Conduct a final walkthrough with the customer",
    "Receive final payment, and the project is done",
]


def _plan_work_steps(q: Quote) -> list:
    """The MIDDLE of the plan — the real, sequenced work for THIS job.
    Pulled from the project-plan steps saved on the quote's line items."""
    work = []
    for li in q.line_items:
        plan = li.inputs.get("project_plan") if isinstance(li.inputs, dict) else None
        for step in (plan or []):
            desc = (step.get("description") or "").strip()
            if desc:
                work.append(desc)
    return work


def plan_is_thin(q: Quote) -> bool:
    """GUARDRAIL CHECK: True when the quote has no real per-job work steps saved,
    so the plan would fall back to a generic middle. The UI uses this to WARN
    Michael to fill in the real dig/build sequence before sending — instead of
    silently shipping a plan that jumps over the key steps."""
    return len(_plan_work_steps(q)) < 2


def _project_plan_steps(q: Quote) -> list:
    """The ordered list of plain-language job steps (start to finish). Shared by
    the quote, the standalone plan doc, and the contract so they never drift.

    Structure = fixed OPENING bookend + real work steps + fixed CLOSING bookend,
    modelled on Michael's actual BMDW outlines. If no real work steps are saved,
    a labelled placeholder makes the gap OBVIOUS (never a silent generic plan)."""
    work = _plan_work_steps(q)
    if not work:
        work = [f"Complete the {li.label.lower()}" for li in q.line_items] or \
               ["Complete the work described in the quote"]
    return _PLAN_OPENING + work + _PLAN_CLOSING


def _project_plan_rows(q: Quote) -> str:
    """Friendly numbered 'how it'll go' plan block for the customer — pulled from
    the shared steps so quote/plan/contract stay in sync. '' if no steps."""
    steps = _project_plan_steps(q)
    if not steps:
        return ""
    items = "".join(f"<li>{s}</li>" for s in steps)
    return (
        '<div class="section-label">How the job will go</div>'
        '<p class="work-desc" style="margin-bottom:8px;">'
        "Here's exactly how we'll take this from start to finish, so you know what to expect:"
        '</p>'
        f'<ol class="plan">{items}</ol>'
    )


def _quote_html(q: Quote, company: dict, today: date) -> str:
    """Customer-facing quote — the COMPLETE selling document in ONE page:
    a friendly intro, the price, the project plan (so they can picture it),
    and a clear e-Transfer 'ready to go' acceptance block. Customer never
    sees the internal bucket breakdown."""
    deposit = q.customer_total * (company.get("deposit_pct", 50.0) / 100)
    remaining = q.customer_total - deposit
    etransfer_email = company.get("email", "blackmountaindirtworks@gmail.com")
    first_name = (q.customer.name or "there").split()[0]

    # Single-row services table — lump sum only
    services_table = (
        '<table class="summary"><thead><tr>'
        '<th>Description</th><th class="num">Amount (CAD)</th>'
        '</tr></thead><tbody>'
        f'<tr><td>Services</td><td class="num">${q.customer_total:,.2f}</td></tr>'
        '</tbody></table>'
    )

    payment_rows = [
        '<tr><td>Total project cost (incl. tax)</td>'
        f'<td class="num">${q.customer_total:,.2f}</td></tr>',
    ]
    if deposit > 0:
        payment_rows.append(
            '<tr><td>Deposit to book your spot (50%)</td>'
            f'<td class="num">${deposit:,.2f}</td></tr>'
        )
        payment_rows.append(
            '<tr class="total"><td>Balance when the job is done</td>'
            f'<td class="num">${remaining:,.2f}</td></tr>'
        )

    payment_table = (
        '<table class="summary"><thead><tr>'
        '<th>Description</th><th class="num">Amount (CAD)</th>'
        '</tr></thead>'
        f'<tbody>{"".join(payment_rows)}</tbody></table>'
    )

    # Friendly intro line
    intro = (
        f'<p class="work-desc" style="margin-bottom:16px;">Hi {first_name}, thanks for '
        "the opportunity to quote your project. Here's what the work is and what it costs "
        "all-in. I've also included a separate project plan that walks through exactly how "
        "it'll go from start to finish. Any questions at all, just reach out.</p>"
    )

    # The "ready to go" e-Transfer acceptance block
    accept_block = (
        '<div class="accept-box">'
        '<div class="accept-title">Ready to go?</div>'
        f'<p style="margin:6px 0 8px;">Booking your spot is easy — just send your deposit of '
        f'<strong>${deposit:,.2f}</strong> by e-Transfer to:</p>'
        f'<p class="etransfer">{etransfer_email}</p>'
        '<p style="margin:8px 0 0;font-size:11pt;color:#444;">Sending the deposit confirms you '
        "accept this quote and locks in your place on the schedule. That's it — no forms to "
        "chase, no hassle. Prefer to talk it through first? Just give me a call.</p>"
        "</div>"
    )

    body = (
        _header_html(company, "QUOTE", q, today)
        + intro
        + '<div class="doc-section"><div class="section-label">YOUR PROJECT:</div>'
        f'<div class="work-desc">{_short_scope_line(q)}</div></div>'
        + services_table
        + '<div class="section-label">Payment</div>'
        + payment_table
        + accept_block
        + f'<p class="note"><strong>The fine print.</strong> '
          f'{company.get("quote_terms", "Final invoice amount paid upon completion. Deposit of 50% required before equipment is mobilized.")} '
          "This price is all-in for the work described, based on normal ground conditions. "
          "If something unexpected turns up underground, we'll always talk it through with you "
          "before any extra work or cost.</p>"
    )
    return _wrap(f"Quote {q.quote_id}", body)


def _plan_html(q: Quote, company: dict, today: date) -> str:
    """Standalone customer-facing PROJECT PLAN document — the free-value piece
    sent alongside the quote so they can picture the whole job before they buy.
    No price, no contract language — just how it'll go, start to finish."""
    first_name = (q.customer.name or "there").split()[0]
    intro = (
        '<div class="plan-intro-box">'
        f"Hi {first_name}, here's the plan for your project laid out step by step. "
        "This is exactly how we'll run the job from the first site walk to the final "
        "handover, so you know what to expect at every stage."
        "</div>"
    )
    body = (
        _header_html(company, "PROJECT PLAN", q, today)
        + intro
        + '<div class="doc-section"><div class="section-label">YOUR PROJECT:</div>'
        f'<div class="work-desc">{_short_scope_line(q)}</div></div>'
        + _project_plan_rows(q)
        + '<p class="note"><strong>Note.</strong> This plan is our roadmap for the work '
          "described. Timing of each step can shift with weather, ground conditions, and "
          "utility-locate scheduling — if anything changes, we talk it through with you first.</p>"
    )
    return _wrap(f"Project Plan {q.quote_id}", body)


def _contract_html(q: Quote, company: dict, body_text: str, today: date) -> str:
    safe_body = body_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # The contract INCLUDES the project plan (so the signed doc carries the full
    # scope of how the job will run, not just the legal terms).
    plan_block = _project_plan_rows(q)
    plan_section = (
        ('<div class="doc-section" style="margin-top:22px;">' + plan_block + "</div>")
        if plan_block else ""
    )
    body = (
        _header_html(company, "CONTRACT", q, today)
        + f"<pre>{safe_body}</pre>"
        + plan_section
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
    HTML(string=_quote_html(q, company, _pacific_today())).write_pdf(str(out_path))
    return out_path, None


def render_plan_pdf(q: Quote, company: dict) -> Tuple[Optional[Path], Optional[str]]:
    """Standalone PROJECT PLAN PDF — the free-value doc sent with the quote."""
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as exc:
        return None, f"WeasyPrint unavailable: {exc}"
    out_path = _ensure_dir(q.quote_id) / "project-plan.pdf"
    HTML(string=_plan_html(q, company, _pacific_today())).write_pdf(str(out_path))
    return out_path, None


def render_contract_pdf(q: Quote, company: dict, body_text: Optional[str] = None) -> Tuple[Optional[Path], Optional[str]]:
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as exc:
        return None, f"WeasyPrint unavailable: {exc}"
    out_path = _ensure_dir(q.quote_id) / "contract.pdf"
    text = body_text or q.contract_text or draft_contract_text(q, company)
    HTML(string=_contract_html(q, company, text, _pacific_today())).write_pdf(str(out_path))
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
    HTML(string=_invoice_html(q, company, _pacific_today(),
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
        q, company, _pacific_today(), CostBucket.MATERIALS, "MATERIAL TAKEOFF",
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
        q, company, _pacific_today(), CostBucket.EQUIPMENT, "EQUIPMENT LIST",
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
    HTML(string=_receipt_html(q, company, _pacific_today(),
                              amount_received, receipt_kind, received_date)).write_pdf(str(out_path))
    return out_path, None
