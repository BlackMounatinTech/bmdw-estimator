"""Shared helpers for the deterministic calculators."""

from __future__ import annotations

import json
from math import ceil
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def load_config(filename: str) -> dict:
    path = CONFIG_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Config file missing: {path}. Populate it with Michael's real numbers."
        )
    with path.open() as f:
        return json.load(f)


def round_up(value: float) -> int:
    return int(ceil(value))


def cu_yd(length_ft: float, width_ft: float, depth_ft: float) -> float:
    """Volume in cubic yards from length × width × depth in feet."""
    cubic_feet = length_ft * width_ft * depth_ft
    return round(cubic_feet / 27.0, 2)
