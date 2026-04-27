"""Contract drafter.

Customer-facing principles (per Michael, 2026-04-27):
- NO supplier names (Browns River / Upland's / Northwin)
- NO specific equipment models (no "9-ton excavator" — just "machine" / "equipment")
- NO depths or volumes in scope (10×15 area is fine; "3 inches" or "13 cu yd" is not)
- NO "Fuel — estimated" or other internal cost-tracking line items
- NO WCB number, NO insurance/WorkSafeBC mentions
- NO "reasonable care" — we take 100% care
- Project Plan = ONE flat numbered list (1, 2, 3, …), no PRE-WORK / WORK / WRAP-UP headings
"""

import json
import os
from datetime import date
from typing import Optional

from server.schemas import Quote


def is_ai_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _scope_block_deterministic(q: Quote) -> str:
    """Customer-facing scope: project labels only, no materials list, no
    equipment specifics, no volumes/depths. The label IS the scope."""
    if not q.line_items:
        return "  - (No projects specified)"
    return "\n".join(f"  - {li.label}" for li in q.line_items)


def _scope_block_ai(q: Quote) -> Optional[str]:
    """Plain-language scope narration via Anthropic. Returns None on any failure."""
    if not is_ai_configured():
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    project_summaries = [{"label": li.label, "job_type": li.job_type} for li in q.line_items]

    prompt = (
        "You are writing the SCOPE OF WORK section of a residential excavation "
        "contract for Black Mountain Dirt Works on Vancouver Island, BC.\n\n"
        "Write 1-2 short paragraphs in plain customer-friendly English describing "
        "the work being done. Mention each project (one paragraph each if multiple).\n\n"
        "STRICT RULES:\n"
        "- DO NOT mention specific suppliers (Browns River, Upland's, Northwin, etc.)\n"
        "- DO NOT mention specific equipment models (no '9-ton excavator', no "
        "'tandem dump truck' — use generic 'machine' or 'truck' or 'equipment')\n"
        "- DO NOT mention depths, cubic yards, or technical volume specs\n"
        "- DO NOT mention dollar amounts, hours, labour rates\n"
        "- Area dimensions like '10×15 ft pad' are OK; depths and volumes are NOT\n"
        "- Plain everyday English. Customer should be able to picture the job.\n\n"
        f"Customer: {q.customer.name}\n"
        f"Site: {q.effective_site_address}\n\n"
        f"Projects:\n{json.dumps(project_summaries, indent=2)}\n\n"
        "Output the scope-of-work paragraphs only. No headings, no preamble."
    )
    try:
        client = Anthropic()
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        return text or None
    except Exception:
        return None


def _project_plan_block(q: Quote) -> str:
    """Single flat numbered list — no PRE-WORK/WORK/WRAP-UP headings, no Day labels.
    Customer-facing, generic language. Pre-work + AI work-phase + wrap-up all
    flow into one continuous 1-N list."""
    deposit = q.customer_total * (company_deposit_pct(None) / 100)

    pre_work = [
        "Site review walk-through with the Owner — confirm access, grades, working conditions",
        "Owner reviews and signs this contract",
        "BC One Call utility locates booked and completed for any underground services",
        f"Receive 50% deposit (${deposit:,.2f} CAD) before equipment is mobilized",
        "Mobilize equipment and stage materials",
    ]

    wrap_up = [
        "Internal QA of completed work — grades, drainage, surface finish",
        "Demobilize equipment, tools, and waste from the site",
        "Final walkthrough with the Owner — confirm completion against the scope",
        "Address any punch-list items identified during walk",
        "Receive remaining balance",
        "Issue receipt for final payment; project complete",
    ]

    # Collect AI-generated work-phase steps from each project (without Day labels)
    work_steps = []
    for li in q.line_items:
        plan = []
        if isinstance(li.inputs, dict):
            plan = li.inputs.get("project_plan") or []
        for step in plan:
            desc = step.get("description", "").strip()
            if desc:
                work_steps.append(desc)

    if not work_steps:
        # Fallback when AI didn't produce a plan
        work_steps = [
            f"Carry out the work described in the SCOPE OF WORK above ({li.label})"
            for li in q.line_items
        ]

    all_steps = pre_work + work_steps + wrap_up
    return "\n".join(f"   {i}. {step}" for i, step in enumerate(all_steps, start=1))


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
  during work. No active dewatering or pumping required.
- ACCESS — Site access is suitable for the equipment used. No additional
  matting / specialized trucking / hand-bombing required beyond what is listed.
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
- CHANGES IN SCOPE. Any work outside the SCOPE OF WORK above will be billed
  on a cost-plus basis. The change order will be discussed with the Owner
  and signed off by both parties BEFORE the affected work proceeds.

- UNFORESEEN CONDITIONS. If site conditions discovered during the work
  materially differ from the key assumptions above (rock, contaminated soil,
  groundwater requiring dewatering, mismarked utilities, etc.), BMDW will
  pause work, document the condition, and issue a change order before
  resuming.

- WEATHER AND ACTS OF GOD. Schedule and cost may shift due to weather events
  (heavy rain, snow, frost, fire, flooding) or other acts of God beyond either
  party's control. Standby charges may apply for crew and equipment already
  mobilized; communicated to the Owner in advance.

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
    deposit = q.customer_total * (company.get("deposit_pct", 50.0) / 100)
    remaining = q.customer_total - deposit

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

{_clauses_block()}

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
