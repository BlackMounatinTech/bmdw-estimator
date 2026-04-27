"""LLM parser: free-form Quick Notes → list of structured JobLineItems.

Architecture rule: the LLM extracts dimensions, item names, and quantities
from Michael's notes. It maps mentioned items to entries in the global
catalogues. It does NOT produce dollar amounts. Costs are looked up from
the catalogues by key after the LLM responds.

A multi-project brief (e.g. "retaining wall and concrete driveway")
emits multiple ParsedProjects, each becoming its own JobLineItem.
"""

import json
import os
from typing import List, Optional

from server.schemas import (
    JobLineItem,
    LineItemEntry,
    ParsedNotesOutput,
    ProjectPlanDay,
)
from tools.calculator.shared import CONFIG_DIR

ANTHROPIC_MODEL = "claude-sonnet-4-6"


def is_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _load_catalogues() -> dict:
    out = {}
    for name in ("materials", "equipment", "trucking", "labour", "spoil"):
        path = CONFIG_DIR / f"{name}.json"
        data = json.loads(path.read_text())
        out[name] = {k: v for k, v in data.items() if not k.startswith("_")}
    return out


def _build_system_prompt(catalogues: dict) -> str:
    return f"""\
You are an estimator's assistant for Black Mountain Dirt Works, an excavation and sitework contractor on Vancouver Island, BC.

Your job: read the contractor's free-form on-site quick notes and produce a structured list of PROJECTS, each grouped into the 5 cost buckets (LABOUR, MATERIALS, EQUIPMENT, TRUCKING, SPOIL).

If the notes describe multiple kinds of work (e.g. a retaining wall AND a concrete driveway in the same job), emit ONE PROJECT PER DETECTED JOB TYPE. Do not lump them into one project.

CRITICAL RULES:
1. You DO NOT produce dollar amounts or unit costs. Costs come from the catalogues below — you only reference items by their `catalogue_key`.
2. You DO compute *quantities* from the dimensions the contractor spoke. Examples:
   - "20 ft long × 6 ft high wall, lock blocks" → blocks needed = ceil(20/5) × ceil(6/2.5)
   - "67 yards of bark mulch" → quantity 67, unit cu_yd
   - "dig down 6 inches over 30×40" → spoil cu yd = 30×40×0.5 / 27 = 22.2
3. Map mentioned items to the catalogues. Use `catalogue_key` and `catalogue_type` (one of: materials, equipment, trucking, labour).
4. If an item the contractor mentioned isn't in any catalogue (e.g. "bark mulch" if not present), still create a line entry with a clear description, set `needs_catalogue_add: true`, and add a warning explaining what to add.
5. Each project includes its own day-by-day project plan covering only that project's work.
6. Be honest about uncertainty. If notes are ambiguous, add a warning. Never invent dimensions.

VALID job_type values:
retaining_wall, patio, concrete_driveway, gravel_driveway, land_clearing,
foundation, road_building, drainage, septic, site_prep, machine_hours

EVERY PROJECT PLAN STARTS WITH THE SAME 4 PRE-WORK MILESTONES (always include
these as the first four entries of every project_plan, regardless of job type):

  Day 1 — Receive approval (customer accepts the quote)
  Day 2 — Call BC One Call to locate underground utilities (gas/water/hydro/septic)
  Day 3 — Receive deposit (50% standard, phase payments if over $50K)
  Day 4 — Mobilize equipment to site

Then the actual work begins on Day 5 onward. Increment day numbers sequentially
so the user can see the real schedule.

DEPOSIT RULE: 50% of the customer total is the standard deposit unless the
customer total exceeds $50,000, in which case use phase payments instead. Use
the Day 3 description verbatim above — Michael adjusts the live dollar value
at send time.

JOB-TYPE TRADE RULES (apply these when computing quantities):

RETAINING_WALL — base geometry:
- Base width = block width + 6 inches total (3" overhang each side).
  Lock blocks (2.5 ft wide) → 3 ft base width.  Magnum stones (2 ft wide) → 2.5 ft base width.
- Base depth always 6 inches (0.5 ft) of crush.
- Base MATERIAL must be 3/4" road crush (catalogue key: road_crush_34_uplands or
  road_crush_34_northwin). NOT pit run, NOT SGSB.
- Base length = wall length + small overhang each end (default ~1 block-length total).
  Flag in warnings for confirmation.
- Base cu yd = base_length × base_width × 0.5 / 27.

RETAINING_WALL — required line items on EVERY wall (always include these unless
Michael's notes explicitly say otherwise):
- Equipment: plate_compactor (priced by day, match wall duration) — required.
- Equipment: laser_level (priced by day, match wall duration) — required.
- Equipment: excavator (sized by block type — see below; daily rate × duration) — required.
- Materials: road_crush_34_uplands (or _northwin if Michael said Northwin) — base course.
- Materials: drain_pipe — $120/roll, 100 ft per roll. Use ceil(wall_length/100) rolls.
- Materials: filter_fabric — $280/roll, one roll standard for walls under 50 ft. If
  the wall is over 50 ft or unusual, add a warning and use Michael's stated qty.
- Materials: diesel — Michael typically estimates fuel as a dollar amount.
  Default to $250-$500 range; if he stated a number, use it. Add as a freeform
  Materials line: description "Fuel — estimated", quantity 1, unit "lump",
  unit_cost matches his estimate. Mark needs_catalogue_add: false.
- Labour: lead_hand + helper as the default crew. Compute hours from duration:
  duration_days × 8 hours each. Emit two LABOUR entries (one per role).

RETAINING_WALL — excavator sizing:
- Lock blocks → minimum excavator_9t (9-ton). Lock blocks are too heavy for smaller iron.
- Magnum stone blocks → excavator_4t (4-ton) is fine.

BLOCK TRUCKING (heavy haul, per load — Trucking bucket):
- Lock blocks: 16 per load at $850/load. catalogue_key = block_delivery.
  Compute loads = ceil(block_count / 16); cost = loads × $850.
- Magnum stones: 26 per load at $850/load. Same catalogue_key = block_delivery.
  Compute loads = ceil(block_count / 26); cost = loads × $850.

AGGREGATE TRUCKING (hourly tandem dump — Equipment bucket, since billed by truck time):
- Capacity: 10 cu yd per load.
- Round trip: 2 hours default (60 min each way + load/dump). Bump up if site is far.
- Rate depends on supplier:
  - Default tandem: $170/hr (catalogue_key = tandem_dump). Use for Upland's, Northwin, or unspecified.
  - Browns River tandem: $160/hr (catalogue_key = tandem_dump_brownsriver). Use when materials are sourced from Browns River Pit.
- Compute: num_loads = ceil(total_aggregate_cu_yd / 10); truck_hours = num_loads × round_trip_hours.
  Emit a single Equipment line with the matching tandem catalogue_key, quantity = truck_hours, unit = hour.

SUPPLIER → REGION MAPPING (pick the supplier closest to the job site):
- Browns River Pit (suffix `_brownsriver`) — serves Courtenay, Comox, Cumberland.
- Upland's Gravel (suffix `_uplands`) — central Vancouver Island default (Cobble Hill, Duncan, Mill Bay, etc.).
- Northwin Gravel (suffix `_northwin`) — alternative central VI supplier.
If Michael names a supplier explicitly, use it. Otherwise pick by location.

SPOIL DUMP DESTINATIONS:
- Default: weight-based, $10/ton (use cu_yd × 1.25 = tons). Emit a freeform Spoil line.
- Browns River Pit (Courtenay / Comox / Cumberland jobs): $90 per tandem load (10 cu_yd capacity).
  Compute: loads = ceil(spoil_cu_yd / 10); dump_cost = loads × 90.
  Emit a freeform Spoil line: description "Browns River dump fee — N tandems × $90", quantity = loads, unit = "load", unit_cost = 90.

CUSTOM-WALL ESCALATION: If Michael's notes say "significantly bigger" or "unusual"
or anything that doesn't fit standard scope, draft best-effort quantities AND add a
warning: "Custom-scope wall — flagged as non-standard, double-check materials and pricing."

AVAILABLE CATALOGUES (current as of this call):

MATERIALS:
{json.dumps(catalogues['materials'], indent=2)}

EQUIPMENT:
{json.dumps(catalogues['equipment'], indent=2)}

TRUCKING:
{json.dumps(catalogues['trucking'], indent=2)}

LABOUR roles:
{json.dumps(catalogues['labour'], indent=2)}

SPOIL config:
{json.dumps(catalogues['spoil'], indent=2)}

Respond with valid JSON matching this schema:
{{
  "summary": "one-sentence summary of the whole quote",
  "projects": [
    {{
      "job_type": "retaining_wall",
      "label": "Retaining Wall 30' × 4'",
      "project_plan": [{{"day": 1, "description": "..."}}],
      "line_entries": [
        {{
          "bucket": "materials" | "labour" | "equipment" | "trucking" | "spoil",
          "description": "human-readable",
          "quantity": <number>,
          "unit": "each" | "lf" | "sf" | "cu_yd" | "ton" | "bag" | "hour" | "day" | "load" | "man-hour",
          "catalogue_key": "<key from a catalogue above>",
          "catalogue_type": "materials" | "equipment" | "trucking" | "labour",
          "needs_catalogue_add": false,
          "notes": "(optional)"
        }}
      ]
    }},
    {{ "job_type": "concrete_driveway", "label": "...", "project_plan": [...], "line_entries": [...] }}
  ],
  "warnings": ["..."],
  "suggested_quote_label": "Wall + driveway, Smith residence"
}}
"""


def _empty_response(reason: str) -> ParsedNotesOutput:
    return ParsedNotesOutput(
        summary="(parser not run)",
        projects=[],
        warnings=[reason],
        suggested_quote_label=None,
    )


def generate_clarifying_questions(quick_notes: str) -> dict:
    """Phase 1 → 2 transition. Read the brief, return targeted questions.

    Output: {"ok": bool, "questions": [str, ...], "reason": Optional[str]}
    Questions are short (one sentence each), specific to gaps in the brief,
    and prioritized for things that materially change pricing (location /
    round-trip, missing dimensions, material choice, equipment access).
    """
    if not is_configured():
        return {"ok": False, "questions": [],
                "reason": "ANTHROPIC_API_KEY is not set."}
    if not quick_notes.strip():
        return {"ok": False, "questions": [], "reason": "No input details yet."}

    try:
        from anthropic import Anthropic
    except ImportError:
        return {"ok": False, "questions": [],
                "reason": "anthropic package not installed."}

    system = (
        "You are an experienced excavation / sitework estimator for Black Mountain "
        "Dirt Works on Vancouver Island, BC. You're reading a contractor's quick "
        "voice-dictated brief from the job site. Your task: produce a thorough list "
        "of clarifying questions a seasoned estimator would ask before committing "
        "to a fixed price on this job.\n\n"
        "CRITICAL RULES:\n"
        "- ALWAYS produce 5 to 10 questions. NEVER return an empty list. Every excavation "
        "and sitework job has hundreds of variables — even a 'complete-sounding' brief leaves "
        "price-relevant unknowns. There is always more to nail down.\n"
        "- One sentence each. SPECIFIC, not generic. Reference specifics from the brief "
        "where possible (e.g. 'You said \"5 ft wall\" — is that exposed face height or total "
        "height including footing depth?' instead of just 'What height is the wall?').\n"
        "- Don't repeat what the brief already answered, but DO ask adjacent/related variables.\n"
        "- It's better to over-ask than to miss a price-blowing variable.\n\n"
        "QUESTION CATEGORIES — pull a mix from these (most jobs warrant 1-2 from each "
        "of the top categories, then situational ones):\n\n"
        "1. LOCATION + TRUCKING (highest price impact — almost always ask):\n"
        "   - City / area / nearest town (drives supplier choice + crew travel)\n"
        "   - Round-trip time to the aggregate pit (Browns River for Courtenay/Comox/Cumberland; "
        "Upland's or Northwin for central VI)\n"
        "   - Round-trip time to the dump / spoil destination\n"
        "   - Distance from BMDW yard for equipment mobilization\n\n"
        "2. SITE ACCESS + EQUIPMENT (very common cost driver):\n"
        "   - Gate / driveway width — can a 9-ton excavator fit?\n"
        "   - Slope / terrain — does it need a lower-bed truck or extra mobilization?\n"
        "   - Overhead clearance (low branches, power lines)\n"
        "   - Soft ground / mud / wet season — risk of getting stuck?\n"
        "   - Bridge weight limits or load restrictions on the access road\n\n"
        "3. DIMENSIONS + QUANTITIES (if anything is vague):\n"
        "   - Confirm exact dimensions in feet, including tolerances\n"
        "   - Linear vs square vs cubic where ambiguous (e.g. '40 yards of mulch' — cu yd?)\n"
        "   - Wall heights — exposed face vs total including footing\n\n"
        "4. MATERIAL SPEC + SUPPLIER:\n"
        "   - Specific aggregate (3/4\" road crush vs pit run vs SGSB)\n"
        "   - Block type if walls (lock block vs magnum stone — different price + truck capacity)\n"
        "   - Concrete spec (25 / 30 / 32 MPa, air-entrained?)\n"
        "   - Paver / surface treatment specifics\n"
        "   - Customer-preferred supplier vs estimator's pick\n\n"
        "5. SITE CONDITIONS (price + risk):\n"
        "   - Soil type — clay / sand / fill / rock? Affects bearing, drainage, dig time\n"
        "   - Groundwater risk\n"
        "   - Existing drainage / septic / utilities to maintain or tie into\n"
        "   - Trees, stumps, vegetation — in scope or pre-cleared?\n\n"
        "6. REGULATORY + PERMITS:\n"
        "   - Building permit, tree-removal permit, stream/setback, foreshore — in or out of scope?\n"
        "   - Engineered drawings / geotech stamp required (over height threshold for walls)?\n"
        "   - BC One Call status (utility locates done?)\n"
        "   - WorkSafeBC observer / multi-employer site / prime contractor obligations\n\n"
        "7. CUSTOMER EXPECTATIONS + TIMELINE:\n"
        "   - Hard deadline (event, sale closing, before winter, before snow)\n"
        "   - Cleanup level (broom-clean / rough grade / leave as is)\n"
        "   - Customer-supplied items or work (they're handling fence removal, supplying pavers, etc.)\n"
        "   - Neighbour / liability — kids, pets, neighbour's structures, shared driveway, parked cars\n"
        "   - Power / water on site for tools and dust suppression\n\n"
        "8. CHANGE-ORDER + RISK:\n"
        "   - Customer's tolerance for unforeseen conditions (rock, buried debris, contaminated soil)\n"
        "   - Engineered fill vs in-place reuse\n"
        "   - Change-order process if scope grows\n\n"
        "Output ONLY valid JSON, no prose, no markdown:\n"
        '{"questions": ["...", "..."]}'
    )

    try:
        client = Anthropic()
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", ANTHROPIC_MODEL),
            max_tokens=600,
            system=system,
            messages=[{
                "role": "user",
                "content": (
                    "Contractor's brief from the site:\n\n"
                    f"{quick_notes.strip()}\n\n"
                    "Return JSON with the clarifying questions you need answered."
                ),
            }],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        # Strip fences if any
        if text.startswith("```"):
            text = text.lstrip("`")
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
        data = json.loads(text)
        questions = data.get("questions", [])
        if not isinstance(questions, list):
            return {"ok": False, "questions": [],
                    "reason": "Model returned questions in unexpected format."}
        # Coerce dict items (e.g. {"category": ..., "question": ...}) to plain strings
        cleaned = []
        for q in questions:
            if isinstance(q, str):
                cleaned.append(q.strip())
            elif isinstance(q, dict):
                cleaned.append(str(q.get("question") or q.get("text") or "").strip())
        cleaned = [q for q in cleaned if q]
        # Belt + suspenders: AI is instructed to never return empty, but if it does,
        # fall back to a universal minimum set so Phase 2 always has something to ask.
        if not cleaned:
            cleaned = [
                "What city or area is the job site in, and what's the round-trip time to the nearest aggregate pit?",
                "What's the round-trip time to the dump (or is spoil staying on site)?",
                "What's the site access like — gate width, slope, soft ground, overhead clearance?",
                "What soil are we digging — clay, sand, fill, or rock? Any groundwater risk?",
                "Permits and regulatory — building permit, tree removal, BC One Call, engineering stamp?  Who's responsible?",
                "What's the timeline — any hard deadline (event, sale closing, before winter)?",
                "Any customer-supplied items or work, and what's the cleanup expectation (broom-clean, rough grade, leave as is)?",
            ]
        return {"ok": True, "questions": cleaned, "reason": None}
    except Exception as exc:
        return {"ok": False, "questions": [], "reason": f"Clarifier call failed: {exc}"}


def parse_notes_to_structure(quick_notes: str) -> ParsedNotesOutput:
    if not is_configured():
        return _empty_response(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file (and Render env vars)."
        )
    if not quick_notes.strip():
        return _empty_response("Quick notes are empty.")

    try:
        from anthropic import Anthropic
    except ImportError:
        return _empty_response("anthropic package not installed. Run: pip install anthropic")

    client = Anthropic()
    catalogues = _load_catalogues()
    system_prompt = _build_system_prompt(catalogues)

    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=8192,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    "Quick notes from the job site:\n\n"
                    f"{quick_notes.strip()}\n\n"
                    "Parse to JSON per the schema. "
                    "If multiple project types are described, emit one project per type. "
                    "Output ONLY the JSON object — no prose, no markdown fences, "
                    "nothing before or after the opening { and closing }."
                ),
            }
        ],
    )

    text = "".join(block.text for block in response.content if hasattr(block, "text"))
    stop_reason = getattr(response, "stop_reason", None)

    # Strip markdown fences if present
    if text.strip().startswith("```"):
        text = text.strip().lstrip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]

    # Trim to the first top-level JSON object — handles preamble/postamble
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]

    try:
        data = json.loads(text)
        return ParsedNotesOutput.model_validate(data)
    except json.JSONDecodeError as e:
        hint = ""
        if stop_reason == "max_tokens":
            hint = " (Response was truncated — model hit max_tokens. Notes are too detailed for one pass; try splitting into smaller iteration chunks.)"
        return _empty_response(
            f"Could not parse model response as JSON: {e}{hint}\n\nFirst 500 chars of response:\n{text[:500]}"
        )
    except Exception as e:
        return _empty_response(f"Schema validation failed: {e}\n\nFirst 500 chars of response:\n{text[:500]}")


def hydrate_to_line_items(parsed: ParsedNotesOutput) -> List[JobLineItem]:
    """Convert a ParsedNotesOutput into one JobLineItem per detected project.

    Looks up unit costs from the catalogues by key. Items flagged
    needs_catalogue_add get a unit_cost of 0 and a clear visual marker.
    """
    catalogues = _load_catalogues()
    out: List[JobLineItem] = []

    for project in parsed.projects:
        entries = []
        for raw in project.line_entries:
            unit_cost = 0.0
            catalogue_sku = None
            cat = raw.catalogue_type
            key = raw.catalogue_key

            lookup_failed = False
            if cat and key and not raw.needs_catalogue_add:
                cat_data = catalogues.get(cat, {})
                item = cat_data.get(key)
                if item:
                    if cat == "materials":
                        unit_cost = float(item.get("cost_per_unit", 0))
                    elif cat == "equipment":
                        if raw.unit in ("hour", "man-hour"):
                            unit_cost = float(item.get("hourly_rate") or 0)
                        elif raw.unit == "day":
                            unit_cost = float(item.get("daily_rate") or 0)
                        elif raw.unit == "each":
                            unit_cost = float(item.get("mobilization") or 0)
                    elif cat == "trucking":
                        unit_cost = float(item.get("per_load_rate", 0))
                    elif cat == "labour":
                        unit_cost = float(item.get("hourly_rate", 0))
                    catalogue_sku = item.get("sku")
                else:
                    # Key was provided by AI but doesn't exist in the catalogue.
                    # Don't silently ship a $0 line — flag it loudly.
                    lookup_failed = True

            description = raw.description
            if raw.needs_catalogue_add:
                description = f"⚠ {description} (add to catalogue)"
            elif lookup_failed:
                description = f"⚠ {description} (catalogue key '{cat}/{key}' not found — fix in config or rename)"

            entries.append(LineItemEntry(
                bucket=raw.bucket,
                description=description,
                quantity=raw.quantity,
                unit=raw.unit,
                unit_cost=unit_cost,
                catalogue_sku=catalogue_sku,
            ))

        plan_days = [
            ProjectPlanDay(day=p.day, description=p.description)
            for p in project.project_plan
        ]
        inputs = {
            "source": "quick_notes_parser",
            "project_plan": [{"day": d.day, "description": d.description} for d in plan_days],
        }

        out.append(JobLineItem(
            job_type=project.job_type,
            label=project.label,
            inputs=inputs,
            entries=entries,
        ))

    return out


# Back-compat shim — old code may call hydrate_to_line_item (singular)
def hydrate_to_line_item(parsed: ParsedNotesOutput, label: Optional[str] = None) -> JobLineItem:
    items = hydrate_to_line_items(parsed)
    if items:
        return items[0]
    return JobLineItem(
        job_type="machine_hours",
        label=label or "(empty)",
        inputs={"source": "quick_notes_parser_empty"},
        entries=[],
    )
