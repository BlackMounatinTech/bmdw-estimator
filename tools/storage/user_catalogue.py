"""User catalogue — custom line items captured from past quotes for reuse.

Every time Michael locks in a quote, any line entry that DOESN'T map to a
catalogue SKU (config/*.json) gets recorded here. Over time this becomes
his personal "rolodex" of one-off items: tree removals, niche materials,
specific suppliers, etc.

Stored as a JSON file on the persistent disk so it survives deploys.
Future: surface entries as autocomplete suggestions in the catalogue
dropdowns on the Edit Quote tab and Phase 3 spreadsheet.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from server.schemas import CostBucket
from tools.storage.paths import data_dir


def _path() -> Path:
    return data_dir() / "user_catalogue.json"


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def load_user_catalogue() -> List[dict]:
    """Return all custom items the user has captured. Empty list if no file yet."""
    p = _path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text()).get("items", [])
    except Exception:
        return []


def _normalize_key(bucket: str, description: str, unit: str) -> str:
    """Stable identity for dedup — bucket + lowercased description + unit."""
    return f"{bucket}|{description.strip().lower()}|{unit.strip().lower()}"


def add_item(bucket: str, description: str, quantity: float, unit: str,
             unit_cost: float, notes: Optional[str] = None) -> dict:
    """Add (or update) a single custom item. Bumps last_used_at on every call.
    Returns the canonical record."""
    items = load_user_catalogue()
    key = _normalize_key(bucket, description, unit)
    matched = None
    for it in items:
        if _normalize_key(it.get("bucket", ""), it.get("description", ""), it.get("unit", "")) == key:
            matched = it
            break

    if matched:
        # Update existing — keep first-seen, bump last-used + count + cost
        matched["last_used_at"] = _now()
        matched["use_count"] = int(matched.get("use_count", 1)) + 1
        matched["last_unit_cost"] = float(unit_cost)
        if notes:
            matched["notes"] = notes
    else:
        record = {
            "bucket": bucket,
            "description": description,
            "unit": unit,
            "last_unit_cost": float(unit_cost),
            "use_count": 1,
            "first_seen_at": _now(),
            "last_used_at": _now(),
            "notes": notes or "",
        }
        items.append(record)
        matched = record

    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"items": items}, indent=2))
    return matched


def capture_quote_customs(line_items, static_skus: set) -> int:
    """Walk a quote's line_items and add any entry that's not in the static
    catalogue (no catalogue_sku, or sku not in static_skus). Returns the count
    of items added/updated."""
    count = 0
    for li in line_items:
        for e in li.entries:
            sku = (e.catalogue_sku or "").strip()
            # Treat as custom when no SKU OR SKU not recognized
            if not sku or sku not in static_skus:
                bucket = e.bucket.value if isinstance(e.bucket, CostBucket) else str(e.bucket)
                add_item(
                    bucket=bucket,
                    description=e.description,
                    quantity=float(e.quantity),
                    unit=e.unit,
                    unit_cost=float(e.unit_cost),
                )
                count += 1
    return count


def static_catalogue_skus() -> set:
    """Pull every SKU from the static config/*.json catalogues so we can tell
    custom items apart from catalogue items."""
    config_dir = Path(__file__).resolve().parents[2] / "config"
    skus = set()
    for name in ("materials", "equipment", "trucking", "labour"):
        p = config_dir / f"{name}.json"
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
            for k, v in data.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, dict) and v.get("sku"):
                    skus.add(v["sku"])
        except Exception:
            continue
    return skus
