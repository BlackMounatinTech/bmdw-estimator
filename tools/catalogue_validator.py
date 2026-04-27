"""Catalogue integrity validator.

Run via: python -m tools.catalogue_validator

Walks every config/*.json and verifies:
- Each entry has required fields (sku, name, cost or rate fields).
- No required-rate field is missing AND zero (would silently produce $0 lines).
- Catalogue keys referenced in the parser system prompt actually exist.

Returns a non-zero exit code if any issues are found, so this can run in CI.
"""

import json
import sys
from pathlib import Path
from typing import List, Tuple

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
PARSER_PATH = Path(__file__).resolve().parent / "parser" / "notes_to_line_items.py"

# Fields each catalogue entry must define for at least one cost lookup to work.
REQUIRED_RATE_FIELDS = {
    "materials": ["cost_per_unit"],
    "labour": ["hourly_rate"],
    "trucking": ["per_load_rate"],
    "equipment": ["hourly_rate", "daily_rate", "weekly_rate", "monthly_rate"],
}

# Catalogue files we expect to exist.
CATALOGUES = ["materials", "equipment", "trucking", "labour", "spoil"]


def _load(name: str) -> dict:
    path = CONFIG_DIR / f"{name}.json"
    data = json.loads(path.read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def validate_catalogues() -> Tuple[List[str], List[str]]:
    """Return (errors, warnings) for the whole config dir."""
    errors: List[str] = []
    warnings: List[str] = []

    for name in CATALOGUES:
        path = CONFIG_DIR / f"{name}.json"
        if not path.exists():
            errors.append(f"missing catalogue file: {path.name}")
            continue
        cat = _load(name)
        for key, item in cat.items():
            if not isinstance(item, dict):
                continue  # nested settings blob, skip
            # Spoil is a settings file, not item rows — skip name + rate checks.
            if name == "spoil":
                continue
            if "name" not in item:
                warnings.append(f"{name}/{key}: missing 'name'")
            required = REQUIRED_RATE_FIELDS.get(name, [])
            if required and not any(item.get(f) for f in required):
                errors.append(
                    f"{name}/{key}: no rate field set "
                    f"(needs one of: {', '.join(required)})"
                )

    return errors, warnings


def find_referenced_keys_in_parser() -> List[str]:
    """Heuristic — pull catalogue_key strings literally mentioned in the prompt."""
    if not PARSER_PATH.exists():
        return []
    text = PARSER_PATH.read_text()
    candidates = []
    # Look for snake_case_with_underscores wrapped in backticks or quotes,
    # or after "catalogue_key = " patterns.
    import re
    for match in re.finditer(r"`([a-z][a-z0-9_]+)`", text):
        candidates.append(match.group(1))
    for match in re.finditer(r"catalogue_key\s*=\s*([a-z][a-z0-9_]+)", text):
        candidates.append(match.group(1))
    # Filter to plausible catalogue keys (>= 2 underscores OR ends with known suffix)
    plausible = [c for c in candidates if "_" in c and len(c) >= 5]
    return sorted(set(plausible))


def validate_parser_references() -> List[str]:
    """Return errors for catalogue keys referenced in the parser but missing from configs."""
    all_keys = set()
    for name in CATALOGUES:
        path = CONFIG_DIR / f"{name}.json"
        if not path.exists():
            continue
        for k in _load(name).keys():
            all_keys.add(k)

    errors = []
    referenced = find_referenced_keys_in_parser()
    # Whitelist non-catalogue snake_case tokens that show up in the prompt.
    whitelist = {
        "catalogue_key", "catalogue_type", "needs_catalogue_add",
        "project_plan", "line_entries", "suggested_quote_label",
        "retaining_wall", "concrete_driveway", "gravel_driveway",
        "land_clearing", "road_building", "machine_hours", "site_prep",
        "needs_confirmation",
    }
    for key in referenced:
        if key in whitelist:
            continue
        if key in all_keys:
            continue
        errors.append(f"parser references unknown catalogue_key: {key}")
    return errors


def main() -> int:
    print("Catalogue integrity check")
    print("=" * 50)

    errors, warnings = validate_catalogues()
    parser_errors = validate_parser_references()
    errors.extend(parser_errors)

    if warnings:
        print("\nWARNINGS")
        for w in warnings:
            print(f"  ! {w}")

    if errors:
        print("\nERRORS")
        for e in errors:
            print(f"  ✗ {e}")
        print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
        return 1

    print(f"\n✓ All catalogues clean. ({len(warnings)} warnings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
