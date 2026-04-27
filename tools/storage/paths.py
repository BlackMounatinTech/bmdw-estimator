"""Centralized data paths.

In production (Render), `BMDW_DATA_DIR` env var points at a mounted persistent
disk so SQLite + uploaded files survive deploys. Locally it falls back to
`./data/` in the project root.
"""

import os
from pathlib import Path


def data_dir() -> Path:
    env = os.environ.get("BMDW_DATA_DIR", "").strip()
    if env:
        p = Path(env)
    else:
        # Project root → tools/storage/paths.py is 3 levels deep
        p = Path(__file__).resolve().parents[2] / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "bmdw.db"


def attachments_dir() -> Path:
    p = data_dir() / "attachments"
    p.mkdir(parents=True, exist_ok=True)
    return p
