"""Pre-bid checklist questions.

Universal questions apply to every quote. Per-job-type questions layer on
top for the specific projects on the quote. Capture screen renders these
as a passive reference Michael reads while talking to the customer; he
dictates answers into Quick Notes.
"""

from typing import List

from server.schemas import Quote


UNIVERSAL_QUESTIONS = [
    "Site access — gate width, slope, low branches, soft ground? Can equipment get in?",
    "Trucking time — how far is the pit / dump? Round-trip estimate (Campbell River ~1 hr each way as baseline).",
    "Spoil — staying on site, going to the dump, or fill request? How much room to stockpile?",
    "Underground utilities — BC One Call done? Any known gas/water/hydro/septic to avoid?",
    "Water on site — risk of hitting groundwater? Existing drainage to maintain or tie into?",
    "Permits — building permit, tree removal, stream/setback, foreshore? Customer responsible or us?",
    "Trees / vegetation removal — in scope or already done? Stumps, roots, brush?",
    "Power and water on site — for tools, dust suppression, washing equipment?",
    "Customer-supplied items / work — anything they're handling? (e.g. removing fence, supplying pavers)",
    "Site cleanup expectations — broom-clean, rough grade, leave as is?",
    "Neighbours / liability — kids, pets, neighbour's structures, parked cars, narrow shared driveway?",
    "Timeline — when do they want it done? Any hard deadline (event, sale closing, before winter)?",
]


JOB_TYPE_QUESTIONS = {
    "retaining_wall": [
        "Wall height + length confirmed? Any curves or step-downs?",
        "Engineering stamp — required (over height threshold) or already on file from the customer?",
        "Geogrid — needed for this height? How many courses?",
        "Caps / wall finish — standard caps or something custom?",
        "Drainage behind wall — drain rock + pipe daylighted where? Any tie-in?",
        "Soil type behind wall — clay, sand, fill? Affects bearing + drainage.",
    ],
    "concrete_driveway": [
        "Concrete spec — 25 / 30 / 32 MPa? Air entrained?",
        "Reinforcement — rebar grid, mesh, fibre, none?",
        "Finish — broom, exposed aggregate, stamped?",
        "Saw cuts / control joints — standard pattern or specified?",
        "Apron / curb tie-in — to existing driveway, garage slab, sidewalk?",
        "Slope and drainage — water goes where?",
    ],
    "gravel_driveway": [
        "Length × width × depth of base + finish layers?",
        "Material — 3/4\" minus, 2\" minus, road mulch, recycled?",
        "Edge containment — none, curbing, geo-edge?",
        "Slope and crown — drainage strategy?",
        "Geofabric under — yes/no?",
    ],
    "patio": [
        "Paver type and size — confirmed with customer? Supplier in stock?",
        "Pattern — running bond, herringbone, basket weave, soldier course?",
        "Edge restraint — concrete edge, aluminum, none?",
        "Drainage slope — where does water go?",
        "Polymeric sand or sweep sand?",
        "Steps or transitions to existing levels?",
    ],
    "land_clearing": [
        "Acreage and density — light brush, dense, mature trees?",
        "Burnable on site — any open burning permits in effect?",
        "Stumps — grub out, mulch in place, or haul away?",
        "Disposal — chip on site, haul to dump, leave windrowed?",
        "Wildlife / setbacks — any environmental constraints?",
    ],
    "road_building": [
        "Road length + width + design speed?",
        "Cuts / fills required — engineered or eyeballed?",
        "Subgrade prep — strip topsoil, geofabric, base depth?",
        "Surfacing — gravel, paved, chip seal?",
        "Drainage — ditches both sides, culverts, daylighting?",
    ],
    "foundation": [
        "Footprint dimensions confirmed?",
        "Excavation depth — frost line + footing depth?",
        "Backfill spec — clean fill, drain rock, in-place?",
        "Perimeter drain — required, daylighted to where?",
        "Engineer drawings on file — anything unusual on the geotech?",
        "Access for concrete truck — width, slope, overhead clearance?",
    ],
    "drainage": [
        "Linear feet of drain + depth?",
        "Pipe spec — 4\" perf, sock, solid, mix?",
        "Daylight or sump pit at the end?",
        "Tie-in to existing storm or just to grade?",
        "Trench restoration — bare, seeded, sod, paved?",
    ],
    "machine_hours": [
        "Specific scope — what's the customer asking us to do for X hours?",
        "Equipment confirmed — right size for the work?",
        "Spoil / debris — handled in scope or extra?",
        "Hard time cap or flexible?",
    ],
}


def checklist_for_quote(quote: Quote) -> List[dict]:
    """Return the merged list of relevant questions for this quote.

    Output: list of {"category": str, "question": str} dicts.
    'category' is either 'Universal' or the job type's display label.
    """
    out = [{"category": "Universal", "question": q} for q in UNIVERSAL_QUESTIONS]
    seen_types = set()
    for li in quote.line_items:
        if li.job_type in seen_types:
            continue
        seen_types.add(li.job_type)
        questions = JOB_TYPE_QUESTIONS.get(li.job_type, [])
        label = li.job_type.replace("_", " ").title()
        out.extend({"category": label, "question": q} for q in questions)
    return out
