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

The Quote Detail uses the deterministic version when populating the Contract tab.
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


def _project_plan_block(q: Quote) -> str:
    """Comprehensive standard project plan. Universal phases applied to every job."""
    deposit = q.customer_total * (company_deposit_pct(None) / 100)
    return f"""\
1. SITE REVIEW + PRE-WORK
   - Site walk and review of access points, grades, working conditions.
   - Coordinate utility locates (BC One Call) for any underground services.
   - Confirm access route, lawn / landscape protection requirements, and
     neighbour considerations.
   - Receive customer-signed acceptance of this contract.
   - Receive 50% deposit (${deposit:,.2f} CAD) before equipment is mobilized.

2. MOBILIZATION
   - Transport excavator, attachments, and any other required equipment to site.
   - Stage materials and trucking schedule with suppliers.

3. EXECUTION OF WORK
   - Perform the scope of work described above.
   - Manage spoil, imported fill, drainage, and on-site staging per project plan.
   - Daily site cleanup and securing of work area at end of each work day.
   - Communicate progress and any unforeseen conditions to the Owner promptly.

4. INSPECTION + ADJUSTMENTS
   - Internal QA of completed work — grades, compaction, drainage, surface finish.
   - Address minor finishing items identified during walk.

5. DEMOBILIZATION
   - Remove all equipment, tools, and waste from the site.
   - Final cleanup of access route and work area.

6. FINAL WALKTHROUGH WITH CLIENT
   - Joint walk with the Owner to confirm completion against the scope.
   - Punch-list items (if any) addressed before sign-off.

7. RECEIVE FINAL PAYMENT
   - Owner remits remaining balance upon completion.

8. RECEIPT ISSUED + PROJECT COMPLETE
   - Receipt for final payment provided to the Owner.
   - Project archived in BMDW records.
"""


def company_deposit_pct(_) -> float:
    """Helper to read deposit_pct from company.json with a 50% default."""
    import json as _json
    from pathlib import Path as _Path
    try:
        cfg = _json.loads((_Path(__file__).resolve().parents[2] / "config" / "company.json").read_text())
        return float(cfg.get("deposit_pct", 50.0))
    except Exception:
        return 50.0


def _key_assumptions_block() -> str:
    """Standard assumptions BMDW relies on when pricing. Customer accepts these
    by accepting the contract — anything outside these triggers a change order."""
    return """\
This price is based on the following key assumptions. If any are found NOT to
be true once work begins, a change order will be issued and signed off by both
parties before the affected work proceeds:

- EXCAVATING CONDITIONS — Soil is workable native ground (clay / sand / fill /
  light gravel). No solid bedrock, no buried debris, no contaminated soil, no
  unmarked structures or buried tanks.
- GROUNDWATER — Trench / excavation can be kept dry by normal site drainage
  during work. No active dewatering, well-pointing, or pumping required.
- ACCESS — Site access is suitable for the equipment quoted (gate width,
  slope, bridge weights, overhead clearance). No additional matting / lower-bed
  trucking / hand-bombing required beyond what is listed.
- UTILITIES — All underground utilities (gas, water, sewer, hydro, septic,
  irrigation) have been located by BC One Call (and customer-supplied locates
  for private services) and are at expected depths. Damage to undisclosed or
  mismarked utilities is not BMDW's responsibility.
- MATERIAL QUANTITIES — Quantities are based on visual site assessment and
  the dimensions provided. Variances of more than 10% may trigger a change
  order on materials, trucking, or spoil disposal.
- WEATHER — Pricing assumes normal seasonal weather for Vancouver Island.
  Extended rain, frost, snow, or other acts of God that prevent work may
  shift the schedule and trigger standby charges (communicated in advance).
- PERMITS + ENGINEERING — Where shown as Owner's responsibility, all required
  permits and engineered drawings are in place and valid before BMDW mobilizes.
- WORK HOURS — Pricing assumes standard daytime hours, Monday to Friday.
  Weekend, evening, or holiday work available at a premium and quoted separately.
"""


def _clauses_block() -> str:
    return """\
- CHANGES IN SCOPE. Any work outside the SCOPE OF WORK above will be billed on
  a cost-plus basis (BMDW's actual cost of labour, materials, equipment, and
  trucking + 40% markup) and communicated to the Owner BEFORE the work
  proceeds. Both parties must sign off on the change order.

- UNFORESEEN CONDITIONS. If site conditions discovered during the work
  materially differ from the key assumptions above (rock, contaminated soil,
  groundwater requiring dewatering, mismarked utilities, etc.), BMDW will
  pause work, document the condition, and issue a change order before
  resuming.

- WEATHER AND ACTS OF GOD. Schedule and cost may shift due to weather events
  (heavy rain, snow, frost, fire, flooding) or other acts of God beyond either
  party's control. Standby time at ${LEAD_HAND_RATE}/hr lead hand + equipment
  daily rate may apply for crew already mobilized.

- DAMAGE TO PROPERTY. BMDW takes reasonable care to protect the Owner's lawn,
  landscaping, hardscape, and structures. Where heavy equipment must cross
  these features, the Owner accepts that some surface damage (lawn ruts, sod
  disturbance, minor cosmetic) is expected and is not BMDW's responsibility
  unless caused by negligence. Plywood mats / ground protection available at
  additional cost if requested.

- WORKSAFEBC AND INSURANCE. BMDW maintains current WorkSafeBC clearance and
  $5M general liability insurance. Certificates available on request.

- DISPUTE RESOLUTION. Any dispute will first be addressed through good-faith
  discussion between the parties. If unresolved within 30 days, the matter
  will be referred to mediation under the laws of British Columbia.
"""


def _render(q: Quote, company: dict, scope_block: str, today: date) -> str:
    legal = company.get("legal_name", "BLACK MOUNTAIN DIRT WORKS")
    owner = company.get("owner_name", "Michael MacKrell")
    title = company.get("owner_title", "Owner Operator")
    phone = company.get("phone", "")
    email = company.get("email", "")
    addr = company.get("address", "")
    wcb = company.get("wcb_number", "TBD")
    deposit = q.customer_total * (company.get("deposit_pct", 50.0) / 100)
    remaining = q.customer_total - deposit

    clauses = _clauses_block().replace("{LEAD_HAND_RATE}", "90")

    return f"""\
{legal}
{owner}, {title}
{phone}  ·  {email}
{addr}

================================================================
CONTRACT FOR SERVICES
================================================================

Contract #: {q.quote_id}
Date issued: {today.isoformat()}

BETWEEN:
  {legal} ("Contractor")
  WCB # {wcb}

AND:
  {q.customer.name} ("Owner")
  {q.customer.address}
  {q.customer.phone or ''}  {('· ' + q.customer.email) if q.customer.email else ''}

----------------------------------------------------------------
SCOPE OF WORK
----------------------------------------------------------------

The Contractor will supply labour, materials, equipment, trucking, and spoil
removal to complete the following work at the Owner's site:
{scope_block}

----------------------------------------------------------------
PRICE
----------------------------------------------------------------

Total contract price (incl. GST + PST):  ${q.customer_total:,.2f} CAD

  Deposit (50%, due before mobilization):  ${deposit:,.2f} CAD
  Remaining balance (due upon completion): ${remaining:,.2f} CAD

This price is firm based on the SCOPE OF WORK and KEY ASSUMPTIONS sections.

----------------------------------------------------------------
PAYMENT TERMS
----------------------------------------------------------------

{company.get('quote_terms', 'Final invoice amount paid upon completion. Deposit of 50% required before equipment is mobilized.')}

----------------------------------------------------------------
PROJECT PLAN
----------------------------------------------------------------

{_project_plan_block(q)}

----------------------------------------------------------------
KEY ASSUMPTIONS
----------------------------------------------------------------

{_key_assumptions_block()}

----------------------------------------------------------------
CLAUSES
----------------------------------------------------------------

{clauses}

----------------------------------------------------------------
ACCEPTANCE
----------------------------------------------------------------

The Owner accepts this contract by reply email confirming acceptance, or by
making the deposit payment toward the contract price. Acceptance constitutes
a binding agreement under the laws of British Columbia.

For: {legal}


_____________________________
{owner}, {title}
{phone}  ·  {email}
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
