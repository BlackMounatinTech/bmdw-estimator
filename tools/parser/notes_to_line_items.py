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
- Rate depends on who's doing the trucking:
  - **BMDW in-house tandem: $100/hr** (catalogue_key = tandem_dump_bmdw). Use when
    Michael says "I'll handle the trucking" / "we're doing trucking" / "in-house" /
    or names BMDW's truck. This is cheapest — no contractor markup.
  - Default contracted tandem: $170/hr (catalogue_key = tandem_dump). Use for hired
    Upland's, Northwin, or unspecified contractor truck.
  - Browns River tandem: $160/hr (catalogue_key = tandem_dump_brownsriver). Use when
    materials are sourced from Browns River Pit AND Michael isn't using his own truck.
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
        "Dirt Works on Vancouver Island, BC. The contractor (Michael) has dictated "
        "a quick voice brief from the site. Produce ONLY the clarifying questions "
        "you genuinely need answered before you can quote accurately. Zero questions "
        "is a valid answer if the brief covers everything. Never pad to hit a quota.\n\n"
        "Aim for 0 to 5 questions. Each one must close a REAL ambiguity or fill a "
        "price-relevant gap — not 'ask to ask'. If something is already clear from "
        "the brief, don't restate it as a question.\n\n"
        "==============================\n"
        "DO NOT ASK ABOUT — these are stupid questions for a working contractor:\n"
        "==============================\n"
        "- Equipment ownership / rental status. BMDW handles equipment internally — "
        "don't ask 'do you own this excavator?' or 'is that a rental?'. The estimator "
        "decides what gear to bring.\n"
        "- Material spec details when Michael uses a STANDARD trade term. If he says "
        "'blue chip' — that's blue chip. If he says 'road crush' — that's 3/4\" road "
        "crush. If he says 'lock block' — that's a lock block. NEVER ask 'are you "
        "specifying 3/4\" clean crush no fines?' or similar pedantic spec questions. "
        "Treat his terms as authoritative.\n"
        "- Compaction DEPTH. When Michael states a depth (e.g. '3 inches deep'), that "
        "always means 3 inches COMPACTED. NEVER ask 'is that compacted or loose?' for "
        "stated depths. Only ask if he explicitly says 'loose fill'. (This rule is about "
        "depths only — swell/fluff factor on excavation volumes IS worth asking; see DO ASK.)\n"
        "- Anything Michael explicitly said he's handling. If he says 'I'll handle the "
        "trucking' — that means HE picks up the materials, end of story. Don't ask "
        "'does that mean you're picking it up yourself?' — yes, that's what it means.\n"
        "- Engineering / spec minutiae beyond what a typical residential customer cares "
        "about. Don't ask about MPa concrete spec for a regular driveway, batter angle "
        "for a 4-foot wall, geogrid layer count, etc. Only ask if there's a real reason.\n"
        "- Generic catch-alls like 'anything else?' or 'any other requirements?'.\n"
        "- Restating what's in the brief in question form. If he said 'magnum stones' "
        "don't ask 'are you using magnum stones or lock blocks?'.\n\n"
        "==============================\n"
        "DO ASK ABOUT — these actually move the price or change scope:\n"
        "==============================\n"
        "- LOCATION (almost always ask) — what city/area is the job in? This drives "
        "supplier choice (Browns River for Courtenay/Comox/Cumberland; Upland's or "
        "Northwin for central VI) AND crew travel cost.\n"
        "- ROUND-TRIP TIMES if not stated — to the pit, to the dump, mobilization.\n"
        "- DIMENSIONS that are genuinely vague — 'a small wall' → ask for ft. But if "
        "he gives clear numbers, accept them.\n"
        "- SITE ACCESS if it could affect equipment choice — narrow gate, steep slope, "
        "low overhead, soft ground. Skip if obvious or already mentioned.\n"
        "- SPOIL DESTINATION if not stated — staying on site, going to a dump, fill request.\n"
        "- PERMITS / ENGINEERING only when actually likely — e.g. ask about engineering "
        "stamp for retaining walls over ~4 ft, or stream-setback for waterfront work.\n"
        "- TIMELINE / hard deadlines — before winter, before a sale closing, etc.\n"
        "- CLEANUP expectations — broom-clean / rough grade / leave as is.\n"
        "- CUSTOMER-SUPPLIED items if relevant (they removing the fence? supplying pavers?).\n"
        "- NEIGHBOUR / LIABILITY where the site is tight — shared driveway, parked cars, kids/pets.\n"
        "- SOIL TYPE only for big excavations where bearing/drainage matters.\n"
        "- BC ONE CALL status if there's any digging.\n"
        "- ELEVATIONS + WATER POOLING — does the site slope toward or away from the "
        "structure? Any low spots where water collects after rain? Where does runoff go? "
        "Affects drainage spec, sub-base prep, and whether extra grading is in scope.\n"
        "- LAWN / SURFACE PROTECTION — does equipment have to drive across the customer's "
        "lawn / sod / pavers / concrete to reach the work area? If yes, plywood mats or "
        "ground protection might be needed (extra cost), or the customer needs to know "
        "what damage to expect.\n"
        "- UNDERGROUND UTILITIES TO CROSS (not just to dig — to drive heavy equipment over). "
        "Are there gas / water / sewer / septic / hydro lines running across the access "
        "path that a 9-ton excavator would cross? Different from BC One Call status — this "
        "is about loading capacity, not just locating.\n"
        "- PERMITS — building permit, tree-removal permit, stream/setback, foreshore, "
        "developmental permit. Are any required, and is the customer or BMDW responsible "
        "for getting them? Big scope/timeline impact.\n"
        "- NEIGHBOUR RELATIONS / SHARED ACCESS — shared driveway, neighbour's structures "
        "close to the work, parked cars, kids/pets, access easement, likelihood of "
        "complaints. Affects scheduling, liability, and how careful crew needs to be.\n"
        "- SWELL / FLUFF FACTOR for big excavations. When Michael describes excavating "
        "a meaningful volume of material (rule of thumb: more than ~100 cu yd), ask "
        "what swell factor to assume — excavated material expands when dug up "
        "(typical 15-20%, so 600 cu yd in-place becomes ~720 cu yd loose). This drives "
        "spoil-haul truck count and cost. Same logic for IMPORTED fill: confirm whether "
        "his stated volume is loose (delivered) or compacted (in-place). Skip for small "
        "jobs where it's a few extra inches in a tandem.\n\n"
        "==============================\n"
        "FORMATTING:\n"
        "==============================\n"
        "- 0 to 5 questions. Prefer fewer. Empty list is valid.\n"
        "- One sentence each. Plain English, no jargon.\n"
        "- Reference the brief specifically when natural.\n"
        "- Order by price impact (location/access first).\n\n"
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
        # Empty list is valid — means the AI judged the brief complete enough.
        # Phase 2 UI handles the empty state with a "skip to generation" prompt.
        return {"ok": True, "questions": cleaned, "reason": None}
    except Exception as exc:
        return {"ok": False, "questions": [], "reason": f"Clarifier call failed: {exc}"}


def generate_review_questions(brief: str, answers: str, generated_quote_summary: str) -> dict:
    """Phase 3 second-round clarifier. After the AI generated a quote, look at the
    OUTPUT and ask any final questions Michael should confirm before locking in.

    Different from Phase 2 (which asks about the input). This one looks at what
    was assumed/produced and flags anything worth a final confirmation.

    Returns: {"ok": bool, "questions": [str, ...], "reason": Optional[str]}
    """
    if not is_configured():
        return {"ok": False, "questions": [],
                "reason": "ANTHROPIC_API_KEY is not set."}
    if not generated_quote_summary.strip():
        return {"ok": True, "questions": [], "reason": None}

    try:
        from anthropic import Anthropic
    except ImportError:
        return {"ok": False, "questions": [],
                "reason": "anthropic package not installed."}

    system = (
        "You're an excavation estimator at BMDW reviewing a quote you just generated, "
        "looking for things to confirm with the contractor before locking it in. "
        "Produce 0 to 4 SHORT review questions about ASSUMPTIONS or DERIVED VALUES "
        "in the generated quote that would change the price if wrong.\n\n"
        "Focus on:\n"
        "- Assumptions you had to make because the brief was silent on something "
        "(e.g. you assumed 1-hr round trip, you picked Upland's as supplier, you "
        "assumed standard duration days).\n"
        "- Quantities or units that look unusual / could be a typo.\n"
        "- Catalogue key choices when there were multiple plausible options.\n"
        "- Anything where a small change would meaningfully shift the customer total.\n\n"
        "DO NOT ask about:\n"
        "- Things already covered in Phase 2 answers.\n"
        "- Spec details for standard trade terms (blue chip, road crush, lock block, etc).\n"
        "- Equipment ownership.\n"
        "- Compaction depth (always assumed compacted).\n"
        "- Restating what's in the quote.\n"
        "- Generic catch-alls.\n\n"
        "Empty list is fine if the quote is solid. Prefer 0-2 questions over padding.\n\n"
        "Output ONLY valid JSON, no prose, no markdown:\n"
        '{"questions": ["...", "..."]}'
    )

    user_msg = (
        f"ORIGINAL BRIEF:\n{brief.strip() or '(empty)'}\n\n"
        f"PHASE 2 ANSWERS:\n{answers.strip() or '(none)'}\n\n"
        f"GENERATED QUOTE (line items, summary, warnings):\n{generated_quote_summary}\n\n"
        "What would you confirm with the contractor before locking in?"
    )

    try:
        client = Anthropic()
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", ANTHROPIC_MODEL),
            max_tokens=600,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
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
            return {"ok": True, "questions": [], "reason": None}
        cleaned = []
        for q in questions:
            if isinstance(q, str):
                cleaned.append(q.strip())
            elif isinstance(q, dict):
                cleaned.append(str(q.get("question") or q.get("text") or "").strip())
        cleaned = [q for q in cleaned if q]
        return {"ok": True, "questions": cleaned, "reason": None}
    except Exception as exc:
        return {"ok": False, "questions": [], "reason": f"Review call failed: {exc}"}


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
