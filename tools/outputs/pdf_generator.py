"""PDF generation for customer-facing quote + contract.

Uses WeasyPrint to render HTML → PDF. WeasyPrint depends on native libs
(cairo, pango, gdk-pixbuf) that may not be installed in every environment;
the import is lazy so the rest of the app boots fine without it.

Output goes to data/pdfs/<quote_id>/{quote,contract}.pdf and the path is
returned. These files are attached when emailing the customer.
"""

from datetime import date
from pathlib import Path
from typing import Optional, Tuple

from server.schemas import CostBucket, Quote
from tools.outputs.contract_drafter import draft_contract_text

PDF_DIR = Path(__file__).resolve().parents[2] / "data" / "pdfs"


def _ensure_dir(quote_id: str) -> Path:
    out = PDF_DIR / quote_id
    out.mkdir(parents=True, exist_ok=True)
    return out


def is_configured() -> bool:
    """Return True if WeasyPrint is importable on this machine."""
    try:
        import weasyprint  # noqa: F401
        return True
    except Exception:
        return False


# ---- HTML templates -----------------------------------------------------

def _quote_html(q: Quote, company: dict) -> str:
    today = date.today().isoformat()
    legal = company.get("legal_name", "Black Mountain Dirt Works")
    addr = company.get("address", "")
    phone = company.get("phone", "")
    email = company.get("email", "")
    website = company.get("website", "")
    primary = company.get("brand_color_primary", "#1f2937")
    if primary.startswith("#TBD"):
        primary = "#1f2937"

    project_blocks = []
    for li in q.line_items:
        materials = [
            f"<li>{e.description} — {e.quantity:g} {e.unit}</li>"
            for e in li.entries if e.bucket == CostBucket.MATERIALS
        ]
        materials_html = (
            "<ul>" + "".join(materials) + "</ul>" if materials else "<p>—</p>"
        )
        project_blocks.append(f"""
            <div class="project">
              <h3>{li.label}</h3>
              <div class="job-type">{li.job_type.replace('_', ' ').title()}</div>
              <h4>Scope of materials</h4>
              {materials_html}
            </div>
        """)
    projects_html = "\n".join(project_blocks) if project_blocks else "<p>—</p>"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Quote {q.quote_id}</title>
<style>
  @page {{ size: Letter; margin: 0.75in; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          color: #111; font-size: 11pt; }}
  h1 {{ font-size: 22pt; margin: 0 0 4px; color: {primary}; }}
  h2 {{ font-size: 14pt; margin-top: 24px; border-bottom: 2px solid {primary};
        padding-bottom: 4px; color: {primary}; }}
  h3 {{ font-size: 12pt; margin: 12px 0 4px; }}
  h4 {{ font-size: 10pt; margin: 8px 0 4px; color: #555; text-transform: uppercase;
        letter-spacing: 0.05em; }}
  .header-row {{ display: flex; justify-content: space-between; align-items: flex-start; }}
  .meta {{ font-size: 10pt; color: #555; margin-top: 8px; }}
  .total-card {{ background: {primary}; color: white; padding: 16px 20px;
                 border-radius: 8px; margin-top: 24px; }}
  .total-card .label {{ font-size: 10pt; text-transform: uppercase;
                        letter-spacing: 0.08em; opacity: 0.85; }}
  .total-card .amount {{ font-size: 26pt; font-weight: 700; margin-top: 4px; }}
  .project {{ margin-bottom: 16px; padding: 12px 14px; background: #f8f9fa;
              border-left: 3px solid {primary}; }}
  .job-type {{ font-size: 10pt; color: #555; }}
  ul {{ margin: 4px 0 0 18px; padding: 0; }}
  li {{ margin: 2px 0; }}
  .terms {{ font-size: 10pt; color: #555; margin-top: 24px; line-height: 1.5; }}
  .footer {{ margin-top: 32px; padding-top: 12px; border-top: 1px solid #ddd;
             font-size: 9pt; color: #777; }}
</style></head>
<body>

<div class="header-row">
  <div>
    <h1>{legal}</h1>
    <div class="meta">{addr}<br>{phone} · {email}<br>{website}</div>
  </div>
  <div style="text-align:right;">
    <div style="font-size:10pt;color:#555;text-transform:uppercase;letter-spacing:0.08em;">Quote</div>
    <div style="font-size:18pt;font-weight:700;">{q.quote_id}</div>
    <div style="font-size:10pt;color:#555;">Date: {today}</div>
  </div>
</div>

<h2>Prepared for</h2>
<div>{q.customer.name}<br>
  {q.customer.address}<br>
  {q.customer.phone or ''} {('· ' + q.customer.email) if q.customer.email else ''}
</div>

<h2>Project site</h2>
<div>{q.effective_site_address}</div>

<h2>Scope of work</h2>
{projects_html}

<div class="total-card">
  <div class="label">Total contract price (incl. tax)</div>
  <div class="amount">${q.customer_total:,.2f} CAD</div>
</div>

<div class="terms">
  <strong>Terms.</strong> {company.get('quote_terms', 'Quote valid 30 days. Final invoiced amount may vary based on site conditions.')}
  Deposit of 50% required to schedule (or phase plan if total exceeds $50,000).
  Insurance + WorkSafeBC certificates available on request.
</div>

<div class="footer">
  {legal} · WCB # {company.get('wcb_number', '—')} · {phone} · {email}
</div>

</body></html>"""


def _contract_html(q: Quote, company: dict, body_text: str) -> str:
    primary = company.get("brand_color_primary", "#1f2937")
    if primary.startswith("#TBD"):
        primary = "#1f2937"
    safe_body = body_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Contract {q.quote_id}</title>
<style>
  @page {{ size: Letter; margin: 0.75in; }}
  body {{ font-family: "Times New Roman", Georgia, serif; color: #111;
          font-size: 11pt; line-height: 1.5; }}
  h1 {{ font-size: 18pt; color: {primary}; margin: 0 0 4px; }}
  pre {{ font-family: "Times New Roman", Georgia, serif; font-size: 11pt;
         white-space: pre-wrap; margin: 0; }}
  .header {{ border-bottom: 2px solid {primary}; padding-bottom: 8px; margin-bottom: 16px; }}
  .meta {{ font-size: 10pt; color: #555; }}
</style></head>
<body>
<div class="header">
  <h1>{company.get('legal_name', 'Black Mountain Dirt Works')}</h1>
  <div class="meta">Contract # {q.quote_id}</div>
</div>
<pre>{safe_body}</pre>
</body></html>"""


# ---- Public API ----------------------------------------------------------

def render_quote_pdf(q: Quote, company: dict) -> Tuple[Optional[Path], Optional[str]]:
    """Render the customer-facing quote PDF. Returns (path, error_reason)."""
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as exc:
        return None, f"WeasyPrint unavailable: {exc}"

    out_dir = _ensure_dir(q.quote_id)
    out_path = out_dir / "quote.pdf"
    html = _quote_html(q, company)
    HTML(string=html).write_pdf(str(out_path))
    return out_path, None


def render_contract_pdf(q: Quote, company: dict, body_text: Optional[str] = None) -> Tuple[Optional[Path], Optional[str]]:
    """Render the contract PDF. body_text defaults to q.contract_text or auto-draft."""
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as exc:
        return None, f"WeasyPrint unavailable: {exc}"

    out_dir = _ensure_dir(q.quote_id)
    out_path = out_dir / "contract.pdf"
    text = body_text or q.contract_text or draft_contract_text(q, company)
    HTML(string=_contract_html(q, company, text)).write_pdf(str(out_path))
    return out_path, None
