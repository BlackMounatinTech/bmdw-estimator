"""Contract drafter.

Per Michael's decision (2026-04-24): no lawyer template required. The contract
states what's happening, the price, and the payment terms — customer
acceptance is binding.

Two render paths:
- `draft_contract_text(q, company)` — deterministic templated version. Always
  works, no API call.
- `draft_contract_text_ai(q, company)` — uses Anthropic to write a 2-3 paragraph
  plain-language narration of the SCOPE OF WORK, then drops it into the same
  fixed template. All binding terms (price, payment, parties) stay deterministic.

The Job Hub uses the deterministic version when populating the Contract tab.
Michael can swap to the AI narration via the "Regenerate with AI" button (TODO).
"""

import json
import os
from datetime import date
from typing import Optional

from server.schemas import CostBucket, Quote


def is_ai_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _scope_block_deterministic(q: Quote) -> str:
    """Materials list per project — deterministic fallback."""
    scope_lines = []
    for li in q.line_items:
        materials = [
            f"  - {e.description}: {e.quantity:g} {e.unit}"
            for e in li.entries if e.bucket == CostBucket.MATERIALS
        ]
        scope_lines.append(f"\n{li.label}\n" + "\n".join(materials))
    return "\n".join(scope_lines)


def _scope_block_ai(q: Quote) -> Optional[str]:
    """Plain-language scope narration via Anthropic. Returns None on any failure."""
    if not is_ai_configured():
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    # Build a compact summary the model can narrate from.
    project_summaries = []
    for li in q.line_items:
        materials = [
            f"{e.description} ({e.quantity:g} {e.unit})"
            for e in li.entries if e.bucket == CostBucket.MATERIALS
        ]
        equipment = [
            e.description for e in li.entries
            if e.bucket == CostBucket.EQUIPMENT
        ]
        project_summaries.append({
            "label": li.label,
            "job_type": li.job_type,
            "materials": materials,
            "equipment": equipment,
        })

    prompt = (
        "You are writing the SCOPE OF WORK section of a residential excavation "
        "contract for Black Mountain Dirt Works on Vancouver Island, BC.\n\n"
        "Write 2-3 short paragraphs in plain customer-friendly English describing "
        "the work being done. Mention each project (one paragraph each if multiple). "
        "Reference the key materials and equipment naturally — not as a bulleted "
        "list. Do NOT mention dollar amounts, hours, or labour rates. Do NOT add "
        "warranties, disclaimers, or boilerplate — that's handled elsewhere in the "
        "contract. Just describe what we'll do.\n\n"
        f"Customer: {q.customer.name}\n"
        f"Site: {q.effective_site_address}\n\n"
        f"Projects:\n{json.dumps(project_summaries, indent=2)}\n\n"
        "Output the scope-of-work paragraphs only. No headings, no preamble."
    )
    try:
        client = Anthropic()
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        return text or None
    except Exception:
        return None


def _render(q: Quote, company: dict, scope_block: str, today: date) -> str:
    return f"""\
CONTRACT FOR SERVICES

Date: {today.isoformat()}
Contract # {q.quote_id}

BETWEEN:
  {company.get('legal_name', 'Black Mountain Dirt Works')}  ("Contractor")
  {company.get('address', '[address]')}
  WCB # {company.get('wcb_number', '[WCB#]')}

AND:
  {q.customer.name}  ("Owner")
  {q.customer.address}
  {q.customer.email or ''}

SCOPE OF WORK

The Contractor will supply labour, materials, equipment, trucking, and spoil
removal to complete the following work at the address above:
{scope_block}

PRICE

Total contract price: ${q.customer_total:,.2f} CAD.

This price is firm subject to site conditions discovered during work that
materially change the scope. Any changes will be agreed in writing between
the parties before the affected work proceeds.

PAYMENT TERMS

{company.get('quote_terms', 'Net 30 days from invoice. Final amount may vary based on site conditions.')}

START DATE

Estimated start: {q.start_date.isoformat() if q.start_date else 'To be confirmed'}.
Project plan provided as a separate attachment.

INSURANCE & WORKSAFEBC

The Contractor is fully insured. WorkSafeBC clearance # {company.get('wcb_number', '[WCB#]')}.
Certificates attached.

ACCEPTANCE

The Owner accepts this contract by reply email confirming acceptance, or by
making any payment toward the contract price. Acceptance constitutes a binding
agreement under the laws of British Columbia.

For: {company.get('legal_name', 'Black Mountain Dirt Works')}

_____________________________
{company.get('legal_name', 'Black Mountain Dirt Works')}
{company.get('email', '')}  ·  {company.get('phone', '')}
"""


def draft_contract_text(q: Quote, company: dict, today: Optional[date] = None) -> str:
    """Deterministic templated contract — always works, no API call."""
    today = today or date.today()
    return _render(q, company, _scope_block_deterministic(q), today)


def draft_contract_text_ai(q: Quote, company: dict, today: Optional[date] = None) -> str:
    """AI-narrated scope wrapped in the same binding template.

    Falls back to deterministic version if Anthropic call fails or unconfigured.
    """
    today = today or date.today()
    ai_scope = _scope_block_ai(q)
    if ai_scope:
        return _render(q, company, "\n" + ai_scope + "\n", today)
    return _render(q, company, _scope_block_deterministic(q), today)
