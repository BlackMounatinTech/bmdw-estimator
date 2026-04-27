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
        link = f"Job_Hub?quote_id={q['quote_id']}"
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
