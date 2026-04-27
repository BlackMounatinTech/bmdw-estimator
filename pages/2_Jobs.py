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
from tools.storage import init_db, list_recent_quotes, load_quote

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
st.caption("Every quote across every customer. Search by customer, location, or job type — filters cross-reference (AND).")

quotes = list_recent_quotes(limit=500)

if not quotes:
    st.info("No quotes yet. Create one from the Quoting page.")
    st.stop()


# ---- Hydrate each quote with city + job_types for faceted search.
# Cheap for Michael's volume (low hundreds of quotes max).

def _extract_city(addr: str) -> str:
    """Heuristic — pull the city from a free-form address.
    'Lot 5, Smith Rd, Cumberland, BC' → 'Cumberland'. Returns '' if uncertain."""
    if not addr:
        return ""
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    # Drop a trailing province/postal-code segment (e.g. 'BC' or 'BC V0R1Z0')
    while parts and (parts[-1].upper() in {"BC", "AB", "ON"} or len(parts[-1]) <= 3):
        parts.pop()
    if not parts:
        return ""
    last = parts[-1]
    # If it's all digits or starts with a number, it's a street segment, not a city
    if last and not last[0].isdigit():
        return last.title()
    return ""


@st.cache_data(ttl=30)
def _hydrate(quote_ids: tuple) -> dict:
    """Return {quote_id: {city, job_types_list, site_address}} from full quote JSON."""
    out = {}
    for qid in quote_ids:
        full = load_quote(qid)
        if full is None:
            out[qid] = {"city": "", "job_types": [], "site_address": ""}
            continue
        addr = full.effective_site_address or ""
        out[qid] = {
            "city": _extract_city(addr),
            "job_types": sorted({li.job_type for li in full.line_items}),
            "site_address": addr,
        }
    return out


hydration = _hydrate(tuple(q["quote_id"] for q in quotes))
for q in quotes:
    h = hydration.get(q["quote_id"], {})
    q["_city"] = h.get("city", "")
    q["_job_types"] = h.get("job_types", [])
    q["_site_address"] = h.get("site_address", "")


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

section_header("Search + filter")

# Build option lists from observed values
all_cities = sorted({q["_city"] for q in quotes if q["_city"]})
all_job_types = sorted({jt for q in quotes for jt in q["_job_types"]})

# Free-text search at the top — matches across customer / invoice / address / job type
search = st.text_input(
    "Search anything",
    placeholder="e.g. Paxton, Cumberland, retaining wall, 2026-",
    help="Searches customer name, invoice #, site address, and job type at once.",
)

f1, f2, f3 = st.columns(3)
with f1:
    status_filter = st.selectbox(
        "Status", ["all", "draft", "sent", "won", "lost"], index=0,
    )
with f2:
    city_filter = st.selectbox(
        "Location", ["all"] + all_cities,
        format_func=lambda x: "All locations" if x == "all" else x,
    )
with f3:
    jt_filter = st.selectbox(
        "Job type", ["all"] + all_job_types,
        format_func=lambda x: "All job types" if x == "all" else x.replace("_", " ").title(),
    )


def _matches(q: dict) -> bool:
    if status_filter != "all" and q["status"] != status_filter:
        return False
    if city_filter != "all" and q["_city"] != city_filter:
        return False
    if jt_filter != "all" and jt_filter not in q["_job_types"]:
        return False
    if search:
        s = search.lower()
        haystack = " ".join([
            q.get("customer_name") or "",
            q["quote_id"],
            q["_site_address"],
            " ".join(q["_job_types"]),
        ]).lower()
        if s not in haystack:
            return False
    return True


filtered = [q for q in quotes if _matches(q)]
st.caption(f"Showing {len(filtered)} of {len(quotes)} jobs.")

if not filtered:
    st.info("No jobs match the current filters. Clear them above to see everything.")
    st.stop()

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
    chips = []
    if q.get("_city"):
        chips.append(
            f'<span style="display:inline-block;background:#1e293b;color:#94a3b8;'
            f'font-size:10px;padding:2px 8px;border-radius:4px;margin-right:4px;'
            f'letter-spacing:0.04em;">📍 {q["_city"]}</span>'
        )
    for jt in q.get("_job_types", []):
        chips.append(
            f'<span style="display:inline-block;background:#1e293b;color:#cbd5e1;'
            f'font-size:10px;padding:2px 8px;border-radius:4px;margin-right:4px;'
            f'letter-spacing:0.04em;">{jt.replace("_", " ").title()}</span>'
        )
    chips_html = ("".join(chips)) if chips else ""
    row[1].markdown(
        f'<div style="color:#cbd5e1;font-size:13px;padding:8px 0;">'
        f'{q.get("customer_name", "—")}'
        f'<div style="margin-top:3px;">{chips_html}</div>'
        f'</div>',
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
