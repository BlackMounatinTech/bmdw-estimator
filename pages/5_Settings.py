"""Settings page — backups, restore, logo upload, persistence diagnostics.

Triple-redundancy story for quote data:
1. SQLite DB on the persistent disk (primary source of truth)
2. Per-quote JSON snapshots on the same disk (auto-written on every save)
3. Manual download bundle (zip of DB + all snapshot JSONs) — save this to your Mac
   periodically as off-site insurance.

If the disk ever gets wiped, "Restore from snapshots" rebuilds the DB from
the JSON sidecars. If BOTH are gone, the manual download bundle restores
from a known-good local copy.
"""

import io
import json
import os
import zipfile
from datetime import datetime
from pathlib import Path

import streamlit as st

from tools.shared import apply_theme, fmt_money, require_auth, section_header
from tools.storage import (
    init_db,
    list_recent_quotes,
    list_snapshot_files,
    restore_from_snapshots,
)
from tools.storage.paths import data_dir, db_path, is_persistent

st.set_page_config(page_title="Black Mountain Dirt Works · Settings", page_icon="", layout="wide")
apply_theme()
require_auth()
init_db()

st.markdown("# Settings")
st.caption("Backups, restore, logo, persistence — admin stuff.")


# ---- Persistence status -------------------------------------------------

section_header("Persistence")

_resolved = data_dir()
_db_file = db_path()
_db_exists = _db_file.exists()
_db_size = _db_file.stat().st_size if _db_exists else 0
_persistent = is_persistent()
_env_var = os.environ.get("BMDW_DATA_DIR", "").strip() or "(not set — auto-detected)"

if _persistent:
    color = "#22c55e"
    msg = "Persistent storage active. Data survives every deploy."
else:
    color = "#ef4444"
    msg = "EPHEMERAL storage. Data WILL BE WIPED on every deploy."

st.markdown(
    f'<div style="background:#ffffff;border:1px solid #e2e8f0;'
    f'border-left:4px solid {color};border-radius:8px;'
    f'padding:12px 16px;margin-bottom:8px;color:#334155;font-size:13px;">'
    f'<strong style="color:{color};">{msg}</strong><br>'
    f'<span style="color:#475569;">Data dir: <code>{_resolved}</code> · '
    f'DB: <code>{_db_file.name}</code> ({_db_size:,} bytes) · '
    f'BMDW_DATA_DIR env: <code>{_env_var}</code></span>'
    "</div>",
    unsafe_allow_html=True,
)


# ---- Backup bundle ------------------------------------------------------

section_header("Backup — download to your Mac")
st.caption(
    "Run this at the end of every work day (or whenever you want peace of mind). "
    "Saves a zip with the SQLite DB + every per-quote JSON snapshot. Keep these "
    "on your Mac / iCloud as off-site insurance against Render losing the disk."
)

snapshots = list_snapshot_files()
all_quotes = list_recent_quotes(limit=10_000)

m1, m2, m3 = st.columns(3)
m1.metric("Quotes in DB", len(all_quotes))
m2.metric("JSON snapshots on disk", len(snapshots))
m3.metric("DB size", f"{_db_size / 1024:,.1f} KB")

st.markdown("&nbsp;", unsafe_allow_html=True)

# Build the zip in-memory on demand
def _build_bundle() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. SQLite DB
        if _db_file.exists():
            zf.write(_db_file, arcname="bmdw.db")
        # 2. All snapshot JSONs
        backup_dir = _resolved / "backups"
        if backup_dir.exists():
            for path in sorted(backup_dir.glob("*.json")):
                zf.write(path, arcname=f"backups/{path.name}")
        # 3. Manifest
        manifest = {
            "exported_at": datetime.utcnow().isoformat(timespec="seconds"),
            "quote_count_in_db": len(all_quotes),
            "snapshot_count": len(snapshots),
            "data_dir": str(_resolved),
            "db_size_bytes": _db_size,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    return buf.getvalue()


b1, b2 = st.columns(2)
with b1:
    if st.button("📦 Build backup bundle", use_container_width=True):
        bundle_bytes = _build_bundle()
        st.session_state["_bundle_bytes"] = bundle_bytes
        st.session_state["_bundle_built_at"] = datetime.utcnow().isoformat(timespec="seconds")
        st.success(f"Bundle ready — {len(bundle_bytes) / 1024:,.1f} KB.")
with b2:
    if "_bundle_bytes" in st.session_state:
        ts = st.session_state["_bundle_built_at"].replace(":", "-").replace("T", "_")
        st.download_button(
            "⬇ Download backup zip",
            data=st.session_state["_bundle_bytes"],
            file_name=f"bmdw-backup-{ts}.zip",
            mime="application/zip",
            use_container_width=True,
        )

# Snapshots inventory
with st.expander(f"📂 {len(snapshots)} snapshot file(s) on the persistent disk", expanded=False):
    if not snapshots:
        st.caption("No snapshots yet. Snapshots are written automatically every time you save a quote.")
    else:
        head = st.columns([3, 1.2, 2])
        head[0].markdown("**File**")
        head[1].markdown("**Size**")
        head[2].markdown("**Last modified (UTC)**")
        for snap in snapshots:
            row = st.columns([3, 1.2, 2])
            row[0].markdown(f"`{snap['filename']}`")
            row[1].markdown(f"{snap['size']:,} B")
            row[2].markdown(snap["modified"])


# ---- Restore from snapshots --------------------------------------------

section_header("Restore from JSON snapshots")
st.caption(
    "If the SQLite DB is missing or corrupted, this rebuilds it from the JSON "
    "sidecar files. Idempotent — safe to run anytime; quotes already in the DB "
    "are skipped."
)
if st.button("Restore from snapshots", use_container_width=False):
    with st.spinner("Walking snapshot files..."):
        result = restore_from_snapshots()
    st.success(
        f"Done. Found {result['found']} snapshot file(s); "
        f"restored {result['restored']}, skipped {result['already_present']} "
        f"already in DB, {result['failed']} failed."
    )
    if result.get("failures"):
        with st.expander("Failures"):
            for f in result["failures"]:
                st.markdown(f"- {f}")


# ---- Logo upload --------------------------------------------------------

section_header("Logo")
st.caption(
    "Upload your logo here — it gets saved to the persistent disk and embeds in "
    "every PDF (quote, contract, invoice, receipts) automatically. PNG with "
    "transparent background works best. Max 70 px tall × 220 px wide in the PDF."
)

logo_target_dir = _resolved / "branding"
logo_target = logo_target_dir / "logo.png"
logo_exists = logo_target.exists()

if logo_exists:
    st.markdown(
        f'<div style="color:#22c55e;font-size:13px;margin-bottom:8px;">'
        f"Current logo: <code>{logo_target}</code> "
        f"({logo_target.stat().st_size:,} bytes)</div>",
        unsafe_allow_html=True,
    )
    st.image(str(logo_target), caption="Current logo", width=300)

uploaded = st.file_uploader(
    "Upload PNG / JPG logo",
    type=["png", "jpg", "jpeg"],
    accept_multiple_files=False,
    key="logo_upload",
)
if uploaded is not None:
    logo_target_dir.mkdir(parents=True, exist_ok=True)
    # Save as logo.png regardless of original extension — standardize
    logo_target.write_bytes(uploaded.read())
    st.success(f"Logo saved to {logo_target}. PDFs will pick it up immediately.")
    st.rerun()

if logo_exists:
    if st.button("Remove current logo", help="Removes the file. PDFs will fall back to text-only header."):
        try:
            logo_target.unlink()
            st.success("Removed.")
            st.rerun()
        except Exception as exc:
            st.warning(f"Couldn't remove: {exc}")


# ---- Quotes-only JSON export (human-readable, single file) -------------

section_header("Export all quotes as one JSON file")
st.caption("Single human-readable JSON of every quote in the DB. Useful for spreadsheet imports or sharing with the accountant.")

if st.button("📄 Build quotes JSON"):
    from tools.storage import load_quote
    out = []
    for row in all_quotes:
        full = load_quote(row["quote_id"])
        if full is not None:
            out.append(json.loads(full.model_dump_json()))
    payload = json.dumps(out, indent=2).encode()
    st.session_state["_json_export_bytes"] = payload
    st.session_state["_json_export_count"] = len(out)
    st.success(f"Built JSON of {len(out)} quote(s) — {len(payload) / 1024:.1f} KB")

if "_json_export_bytes" in st.session_state:
    ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
    st.download_button(
        f"⬇ Download all-quotes-{ts}.json",
        data=st.session_state["_json_export_bytes"],
        file_name=f"bmdw-all-quotes-{ts}.json",
        mime="application/json",
    )
