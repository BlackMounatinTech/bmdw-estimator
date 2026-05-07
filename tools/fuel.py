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

from server.schemas import CostBucket, JobLineItem, LineItemEntry, Quote

FUEL_PCT = 0.07
FUEL_MIN = 300.0
FUEL_AUTO_SKU = "FUEL-AUTO"
FUEL_STATED_SKU = "FUEL-STATED"


def _is_fuel_line(e: LineItemEntry) -> bool:
    return (e.description or "").strip().lower().startswith("fuel")


def _has_user_stated_fuel(q: Quote) -> bool:
    """User dictated a specific fuel amount — leave it alone."""
    for li in q.line_items:
        for e in li.entries:
            if _is_fuel_line(e) and (e.catalogue_sku or "") == FUEL_STATED_SKU:
                return True
    return False


def _internal_cost_excluding_fuel(q: Quote) -> float:
    """Total internal cost minus any existing fuel lines (auto or stated).
    Used as the base for the 7% calc so fuel doesn't compound on itself."""
    fuel_total = 0.0
    for li in q.line_items:
        for e in li.entries:
            if _is_fuel_line(e):
                fuel_total += e.total_cost
    return max(0.0, q.internal_cost - fuel_total)


def _remove_auto_fuel_lines(q: Quote) -> None:
    """Strip every FUEL-AUTO line from every project so we can re-add a
    single accurate one. User-stated fuel lines are left alone."""
    for li in q.line_items:
        li.entries = [
            e for e in li.entries
            if not (_is_fuel_line(e) and (e.catalogue_sku or "") == FUEL_AUTO_SKU)
        ]


def sync_fuel(q: Quote) -> Optional[float]:
    """Make sure the quote has exactly one fuel line at the correct amount.

    - If the user dictated a specific fuel amount (FUEL-STATED line), do nothing.
    - Otherwise: remove any stale FUEL-AUTO lines, compute fuel = max($300,
      7% of internal-excluding-fuel), and inject one Materials-bucket fuel
      line into the first project.

    Returns the auto fuel amount applied, or None if user-stated fuel exists.
    """
    if not q.line_items:
        return None
    if _has_user_stated_fuel(q):
        return None

    _remove_auto_fuel_lines(q)
    base = _internal_cost_excluding_fuel(q)
    fuel = max(FUEL_MIN, round(FUEL_PCT * base, 2))

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
