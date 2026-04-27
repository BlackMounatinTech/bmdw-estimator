"""Dashboard — desktop home view per the signed-off wireframe."""

import streamlit as st

from tools.shared import (
    RAG_GREEN,
    RAG_RED,
    RAG_YELLOW,
    apply_theme,
    fmt_money,
    require_auth,
    section_header,
    status_dot,
)
from tools.storage import (
    dashboard_metrics,
    init_db,
    list_recent_quotes,
)
from tools.storage.paths import data_dir, db_path, is_persistent
import os as _os

st.set_page_config(page_title="BMDW · Dashboard", page_icon="◆", layout="wide")
apply_theme()
require_auth()
init_db()

STATUS_COLORS = {
    "draft": "#64748b",
    "sent": RAG_YELLOW,
    "won": RAG_GREEN,
    "lost": RAG_RED,
}

st.markdown("# Dashboard")

# ---- Persistence diagnostic ---------------------------------------------
# Auto-detect: app uses /var/data if it exists (Render disk), else env var,
# else local ./data/. is_persistent() returns True only when we're NOT on
# the local fallback path.
_resolved_dir = data_dir()
_db_file = db_path()
_db_exists = _db_file.exists()
_db_size = _db_file.stat().st_size if _db_exists else 0
_persistent = is_persistent()
_env_var = _os.environ.get("BMDW_DATA_DIR", "").strip() or "(not set — auto-detected)"

if _persistent:
    _status_color = "#22c55e"
    _status_msg = "✓ Persistent storage active. Data survives every deploy."
else:
    _status_color = "#ef4444"
    _status_msg = ("🔴 EPHEMERAL storage. Data WILL BE WIPED on every deploy. "
                   "On Render: confirm a Disk is attached at /var/data (Settings → Disks). "
                   "If your disk mounts elsewhere, set BMDW_DATA_DIR env var to that path.")

st.markdown(
    f'<div style="background:#111827;border:1px solid #1e293b;'
    f'border-left:4px solid {_status_color};border-radius:8px;'
    f'padding:10px 14px;margin-bottom:12px;color:#cbd5e1;font-size:12px;">'
    f'<strong style="color:{_status_color};">{_status_msg}</strong><br>'
    f'<span style="color:#94a3b8;">Data dir: <code>{_resolved_dir}</code> · '
    f'DB: <code>{_db_file.name}</code> '
    f'({_db_size:,} bytes, {"exists" if _db_exists else "MISSING"}) · '
    f'BMDW_DATA_DIR env: <code>{_env_var}</code></span>'
    f"</div>",
    unsafe_allow_html=True,
)


# ---- Top-line metrics ----------------------------------------------------

m = dashboard_metrics()
c1, c2, c3 = st.columns(3)
c1.metric("Quotes Open", m["open_quotes"])
c2.metric("Won This Week", fmt_money(m["week_won_dollars"]))
c3.metric("Avg Margin (won)", f'{m["avg_margin_pct"]:.1f}%' if m["avg_margin_pct"] else "—")

st.markdown("&nbsp;", unsafe_allow_html=True)
section_header("Recent Quotes")

quotes = list_recent_quotes(limit=15)

if not quotes:
    st.markdown(
        '<div class="project-card">'
        '<div class="card-label">No quotes yet</div>'
        '<div class="card-project">Generate one from the capture screen</div>'
        '<div class="card-detail">Once you save your first quote, it lands here.</div>'
        "</div>",
        unsafe_allow_html=True,
    )
else:
    for q in quotes:
        color = STATUS_COLORS.get(q["status"], "#64748b")
        link = f"Quote_Detail?quote_id={q['quote_id']}"
        st.markdown(
            f'<a href="/{link}" target="_self" style="text-decoration:none;">'
            f'<div style="background:#111827;border:1px solid #1e293b;'
            f'border-left:4px solid {color};border-radius:12px;'
            f'padding:14px 18px;margin-bottom:8px;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;">'
            f'<div>'
            f'<div style="color:#f1f5f9;font-weight:600;font-size:14px;">'
            f'{q["customer_name"]} — {q["quote_id"]}'
            f"</div>"
            f'<div style="color:#64748b;font-size:12px;margin-top:2px;">'
            f'{q["status"].upper()} · margin {q["margin_pct"]}% · '
            f'updated {q["updated_at"][:10]}'
            f"</div></div>"
            f'<div style="color:#e2e8f0;font-weight:700;font-size:16px;">'
            f'{fmt_money(q["final_invoiced"] or q["customer_total"])}'
            f"</div></div></div></a>",
            unsafe_allow_html=True,
        )

st.markdown("&nbsp;", unsafe_allow_html=True)
section_header("Check My List · across all open quotes")

st.markdown(
    '<div class="risk-row">'
    '<div>'
    f'<div class="risk-label">{status_dot(RAG_YELLOW)} Empty — fires once the historian (Layer 2) is wired</div>'
    '<div class="risk-project">cross-quote gap detection comes online with past-job data</div>'
    '</div>'
    '</div>',
    unsafe_allow_html=True,
)
