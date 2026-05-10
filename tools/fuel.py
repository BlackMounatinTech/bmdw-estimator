"""Auto fuel calculation.

Standard rule (Michael's): fuel is 7% of internal cost (pre-markup, pre-tax),
with a $300 minimum for small jobs. The fuel line lives in the Materials
bucket of the first project and is auto-synced whenever the parser hydrates
a new quote or the user presses Save changes on Quote Detail.

If the user manually states a fuel dollar amount in voice ("$1200 in fuel"),
the parser emits a line tagged catalogue_sku="FUEL-STATED" — auto-sync sees
that marker and leaves the user-stated amount alone.
"""

from typing import Optional

from server.schemas import CostBucket, LineItemEntry, Quote

FUEL_PCT = 0.07
FUEL_MIN = 300.0
FUEL_AUTO_SKU = "FUEL-AUTO"
FUEL_STATED_SKU = "FUEL-STATED"


def _is_fuel_line(e: LineItemEntry) -> bool:
    return (e.description or "").strip().lower().startswith("fuel")


def _has_any_fuel_line(q: Quote) -> bool:
    for li in q.line_items:
        for e in li.entries:
            if _is_fuel_line(e):
                return True
    return False


def sync_fuel(q: Quote) -> Optional[float]:
    """Inject a fuel line ONLY when the quote doesn't already have one.

    Once a fuel line exists — whether auto-injected on first save, dictated
    by Michael in voice, or hand-typed in the spreadsheet — it stays put.
    Manual edits to the fuel amount are permanent.

    To force a fresh 7%-of-internal recalc: delete the fuel line in the
    spreadsheet and save — sync_fuel sees no fuel line and re-injects.

    Returns the auto fuel amount applied, or None if a fuel line already exists.
    """
    if not q.line_items:
        return None
    if _has_any_fuel_line(q):
        return None

    fuel = max(FUEL_MIN, round(FUEL_PCT * q.internal_cost, 2))
    fuel_line = LineItemEntry(
        bucket=CostBucket.MATERIALS,
        description="Fuel (auto — 7% of internal, $300 min)",
        quantity=1.0,
        unit="lump",
        unit_cost=fuel,
        catalogue_sku=FUEL_AUTO_SKU,
        rental_insurance_eligible=False,
    )
    # Drop into the first project so it shows up consistently
    q.line_items[0].entries.append(fuel_line)
    return fuel
