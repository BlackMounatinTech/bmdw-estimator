"""Centralized data paths.

In production (Render), data needs to live on a persistent disk so SQLite +
uploaded files survive deploys. Resolution order:

1. `BMDW_DATA_DIR` env var (explicit override — set this if your disk mounts
   at a non-default path).
2. `/var/data` if it exists and is writable (Render auto-detect — works for
   any disk mounted at the conventional path, no env var required).
3. `./data/` in the project root (local development fallback).

This auto-detection means: if the Render Disk is mounted at /var/data, the app
WILL find it whether or not BMDW_DATA_DIR is set. Misconfiguration is forgiven.
"""

import os
from pathlib import Path

_VAR_DATA = Path("/var/data")


def _writable(p: Path) -> bool:
    try:
        return p.exists() and os.access(str(p), os.W_OK)
    except Exception:
        return False


def data_dir() -> Path:
    env = os.environ.get("BMDW_DATA_DIR", "").strip()
    if env:
        p = Path(env)
    elif _writable(_VAR_DATA):
        p = _VAR_DATA
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


def is_persistent() -> bool:
    """True if data_dir() resolves to a likely-persistent path (i.e. NOT the
    repo's local ./data folder, which is ephemeral on Render)."""
    repo_local = (Path(__file__).resolve().parents[2] / "data").resolve()
    return data_dir().resolve() != repo_local
