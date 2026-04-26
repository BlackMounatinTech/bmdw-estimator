"""Jobs page — flat list of every quote across customers, sortable by status/date.

Customers page groups by person; this page is the chronological queue —
"what's open, what's sent, what's won, what needs follow-up." Click through
to the Job Hub for any quote.
"""

import streamlit as st

from tools.shared import (
    RAG_GREEN,
    RAG_RED,
    RAG_YELLOW,
    apply_theme,
    fmt_money,
    require_auth,
    section_header,
)
from tools.storage import init_db, list_recent_quotes

st.set_page_config(page_title="BMDW · Jobs", page_icon="◆", layout="wide")
apply_theme()
require_auth()
init_db()

STATUS_COLORS = {
    "draft": "#64748b",
    "sent": RAG_YELLOW,
    "won": RAG_GREEN,
    "lost": RAG_RED,
}

st.markdown("## Jobs")
st.caption("Every quote across every customer. Filter by status to triage your pipeline.")

quotes = list_recent_quotes(limit=500)

if not quotes:
    st.info("No quotes yet. Create one from the Quoting page.")
    st.stop()


# ---- Top-line counts ----------------------------------------------------

counts = {"draft": 0, "sent": 0, "won": 0, "lost": 0}
totals = {"draft": 0.0, "sent": 0.0, "won": 0.0, "lost": 0.0}
for q in quotes:
    s = q["status"]
    counts[s] = counts.get(s, 0) + 1
    totals[s] = totals.get(s, 0.0) + (q.get("final_invoiced") or q["customer_total"])

c1, c2, c3, c4 = st.columns(4)
c1.metric("DRAFTS", counts["draft"], help=fmt_money(totals["draft"]))
c2.metric("SENT (open)", counts["sent"], help=fmt_money(totals["sent"]))
c3.metric("WON", counts["won"], help=fmt_money(totals["won"]))
c4.metric("LOST", counts["lost"], help=fmt_money(totals["lost"]))


# ---- Filters ------------------------------------------------------------

section_header("All Jobs")

f1, f2 = st.columns([1, 3])
with f1:
    status_filter = st.selectbox(
        "Status", ["all", "draft", "sent", "won", "lost"], index=0,
    )
with f2:
    search = st.text_input("Search (customer name or invoice #)", placeholder="e.g. Smith or 2026-")


def _matches(q: dict) -> bool:
    if status_filter != "all" and q["status"] != status_filter:
        return False
    if search:
        s = search.lower()
        if s not in (q.get("customer_name") or "").lower() and s not in q["quote_id"].lower():
            return False
    return True


filtered = [q for q in quotes if _matches(q)]

if not filtered:
    st.caption("No jobs match the current filter.")
    st.stop()


# ---- Job rows -----------------------------------------------------------

head = st.columns([1.4, 2.2, 0.8, 1.2, 1.2, 1.0, 0.8])
labels = ["Invoice", "Customer", "Status", "Updated", "Customer total", "Margin", ""]
for col, label in zip(head, labels):
    col.markdown(
        f'<div style="color:#64748b;font-size:11px;text-transform:uppercase;'
        f'letter-spacing:0.06em;padding:6px 0;">{label}</div>',
        unsafe_allow_html=True,
    )
st.markdown('<div style="border-top:1px solid #1e293b;margin-bottom:4px;"></div>',
            unsafe_allow_html=True)

for q in filtered:
    color = STATUS_COLORS.get(q["status"], "#64748b")
    row = st.columns([1.4, 2.2, 0.8, 1.2, 1.2, 1.0, 0.8])
    row[0].markdown(
        f'<div style="color:#f1f5f9;font-size:13px;font-weight:700;padding:8px 0;">'
        f'{q["quote_id"]}</div>',
        unsafe_allow_html=True,
    )
    row[1].markdown(
        f'<div style="color:#cbd5e1;font-size:13px;padding:8px 0;">'
        f'{q.get("customer_name", "—")}</div>',
        unsafe_allow_html=True,
    )
    row[2].markdown(
        f'<div style="display:inline-block;background:{color};color:white;'
        f'font-size:10px;font-weight:700;padding:3px 8px;border-radius:6px;'
        f'text-transform:uppercase;letter-spacing:0.06em;margin-top:8px;">'
        f'{q["status"]}</div>',
        unsafe_allow_html=True,
    )
    row[3].markdown(
        f'<div style="color:#94a3b8;font-size:13px;padding:8px 0;">'
        f'{q["updated_at"][:10]}</div>',
        unsafe_allow_html=True,
    )
    row[4].markdown(
        f'<div style="color:#f1f5f9;font-size:13px;font-weight:700;padding:8px 0;text-align:right;">'
        f'{fmt_money(q.get("final_invoiced") or q["customer_total"])}</div>',
        unsafe_allow_html=True,
    )
    row[5].markdown(
        f'<div style="color:#94a3b8;font-size:13px;padding:8px 0;text-align:right;">'
        f'{q["margin_pct"]:g}%</div>',
        unsafe_allow_html=True,
    )
    with row[6]:
        if st.button("Open", key=f"open_{q['quote_id']}", use_container_width=True):
            st.query_params["quote_id"] = q["quote_id"]
            st.switch_page("pages/4_Job_Hub.py")
