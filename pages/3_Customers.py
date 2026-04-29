"""Customers page — roster + per-customer profile with lead status and projects."""

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
from tools.storage import (
    delete_customer,
    init_db,
    list_customers,
    list_quotes_for_customer,
    update_customer_full,
    update_customer_meta,
)

st.set_page_config(page_title="BMDW · Customers", page_icon="", layout="wide")
apply_theme()
require_auth()
init_db()

QUOTE_STATUS_COLORS = {
    "draft": "#64748b",
    "sent": RAG_YELLOW,
    "won": RAG_GREEN,
    "lost": RAG_RED,
}

LEAD_STATUS_COLORS = {
    "cold":  "#64748b",
    "warm":  "#f59e0b",
    "hot":   "#ef4444",
    "sold":  "#22c55e",
    "lost":  "#475569",
}

LEAD_STATUS_LABELS = {
    "cold":  "Cold",
    "warm":  "Warm",
    "hot":   "Hot",
    "sold":  "Sold",
    "lost":  "Lost",
}


st.markdown("# Customers")

selected_id = st.query_params.get("customer_id")
customers = list_customers()


# ---- Roster view ---------------------------------------------------------

if not selected_id:
    if not customers:
        st.markdown(
            '<div class="project-card">'
            '<div class="card-label">No customers yet</div>'
            '<div class="card-project">Generate a quote from the Quoting page</div>'
            '<div class="card-detail">Customers populate automatically when their first quote is saved.</div>'
            "</div>",
            unsafe_allow_html=True,
        )
        st.stop()

    # Top-line lead pipeline
    section_header("Pipeline")
    counts = {s: 0 for s in LEAD_STATUS_LABELS}
    for c in customers:
        counts[c.get("lead_status", "cold")] = counts.get(c.get("lead_status", "cold"), 0) + 1
    pcols = st.columns(len(LEAD_STATUS_LABELS))
    for col, (k, label) in zip(pcols, LEAD_STATUS_LABELS.items()):
        col.metric(label, counts[k])

    st.markdown("&nbsp;", unsafe_allow_html=True)
    section_header(f"Roster · {len(customers)} customers")

    # Filter by lead status
    filt = st.selectbox(
        "Filter by status", ["all"] + list(LEAD_STATUS_LABELS.keys()),
        format_func=lambda x: "All" if x == "all" else LEAD_STATUS_LABELS[x],
    )
    filtered = customers if filt == "all" else [c for c in customers if c.get("lead_status") == filt]

    for c in filtered:
        lead_color = LEAD_STATUS_COLORS.get(c.get("lead_status", "cold"), "#64748b")
        lead_label = LEAD_STATUS_LABELS.get(c.get("lead_status", "cold"), "Cold")
        link = f"?customer_id={c['customer_id']}"
        st.markdown(
            f'<a href="{link}" target="_self" style="text-decoration:none;">'
            f'<div class="project-card" style="border-left:4px solid {lead_color};">'
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;">'
            f'<div>'
            f'<div class="card-label">{c["customer_id"]}</div>'
            f'<div class="card-project">{c["name"]}</div>'
            f'<div class="card-detail">'
            f'<span class="card-detail-value">{c["job_count"]}</span> jobs · '
            f'<span class="card-detail-value">{fmt_money(c["lifetime_revenue"])}</span> lifetime · '
            f'last activity {(c["last_activity_at"] or "—")[:10]}'
            f"</div></div>"
            f'<div style="background:{lead_color};color:white;font-size:10px;'
            f'font-weight:700;padding:3px 8px;border-radius:4px;'
            f'text-transform:uppercase;letter-spacing:0.06em;">{lead_label}</div>'
            f"</div></div></a>",
            unsafe_allow_html=True,
        )
    st.stop()


# ---- Customer profile (drill-in) ----------------------------------------

cust = next((c for c in customers if c["customer_id"] == selected_id), None)
if not cust:
    st.error(f"Customer {selected_id} not found.")
    st.stop()

if st.button("← All customers"):
    st.query_params.clear()
    st.rerun()

st.markdown(f"## {cust['name']}")

contact_bits = []
if cust.get("phone"):
    contact_bits.append(f"📞 {cust['phone']}")
if cust.get("email"):
    contact_bits.append(f"✉️ {cust['email']}")
if cust.get("address"):
    contact_bits.append(f"📍 {cust['address']}")
st.markdown(
    f'<div style="color:#64748b;font-size:13px;margin-bottom:8px;">'
    f"{' · '.join(contact_bits) if contact_bits else 'No contact info on file.'}</div>",
    unsafe_allow_html=True,
)


# ---- Contact info editor (name / phone / email / address) -------------
section_header("Contact info")
st.caption(
    "Fix typos or update contact info. Changes propagate into every existing quote "
    "for this customer (so old PDFs/contracts you regenerate use the corrected info)."
)

with st.form("contact_form"):
    cf1, cf2 = st.columns(2)
    with cf1:
        new_name = st.text_input("Name", value=cust.get("name") or "")
        new_phone = st.text_input("Phone", value=cust.get("phone") or "")
    with cf2:
        new_email = st.text_input("Email", value=cust.get("email") or "")
        new_address = st.text_input("Address", value=cust.get("address") or "")

    if st.form_submit_button("Save contact info", type="primary"):
        result = update_customer_full(
            cust["customer_id"],
            name=new_name.strip() or cust.get("name"),
            email=new_email.strip(),
            phone=new_phone.strip(),
            address=new_address.strip(),
            propagate_to_quotes=True,
        )
        st.success(
            f"Saved. Propagated to {result['quotes_propagated']} existing quote"
            f"{'s' if result['quotes_propagated'] != 1 else ''}."
        )
        st.rerun()


# Lead status + notes editor
st.markdown("&nbsp;", unsafe_allow_html=True)
section_header("Lead status + notes")

c1, c2 = st.columns([1, 3])
with c1:
    current_status = cust.get("lead_status", "cold")
    options = list(LEAD_STATUS_LABELS.keys())
    idx = options.index(current_status) if current_status in options else 0
    new_status = st.selectbox(
        "Lead status", options, index=idx,
        format_func=lambda x: LEAD_STATUS_LABELS[x],
    )
with c2:
    new_notes = st.text_area(
        "Customer notes (private)",
        value=cust.get("notes") or "",
        placeholder="Payment habits, preferences, referral source, anything you want to remember...",
        height=100,
    )

if st.button("Save customer info"):
    update_customer_meta(cust["customer_id"], lead_status=new_status, notes=new_notes)
    st.success("Saved.")
    st.rerun()


# Stats strip
st.markdown("&nbsp;", unsafe_allow_html=True)
m1, m2, m3 = st.columns(3)
m1.metric("Jobs", cust["job_count"])
m2.metric("Lifetime Revenue", fmt_money(cust["lifetime_revenue"]))
m3.metric("Last Activity", (cust["last_activity_at"] or "—")[:10])


# Projects (quotes)
section_header("Projects")

quotes = list_quotes_for_customer(cust["customer_id"])
if not quotes:
    st.info("No projects with this customer yet.")
else:
    for q in quotes:
        color = QUOTE_STATUS_COLORS.get(q["status"], "#64748b")
        link = f"Quote_Detail?quote_id={q['quote_id']}"
        st.markdown(
            f'<a href="/{link}" target="_self" style="text-decoration:none;">'
            f'<div style="background:#ffffff;border:1px solid #e2e8f0;'
            f'border-left:4px solid {color};border-radius:12px;'
            f'padding:14px 18px;margin-bottom:8px;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;">'
            f"<div>"
            f'<div style="color:#0f172a;font-weight:600;font-size:14px;">{q["quote_id"]}</div>'
            f'<div style="color:#64748b;font-size:12px;margin-top:2px;">'
            f'{q["created_at"][:10]} · {q["status"].upper()} · margin {q["margin_pct"]}%'
            f"</div></div>"
            f'<div style="color:#1e293b;font-weight:700;font-size:16px;">'
            f"{fmt_money(q['final_invoiced'] or q['customer_total'])}"
            f"</div></div></div></a>",
            unsafe_allow_html=True,
        )


# ---- Danger zone — delete customer (cascades to all their quotes) ------
st.markdown("---")
st.markdown(
    '<div style="color:#ef4444;font-size:11px;font-weight:700;'
    'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">'
    "Danger zone</div>",
    unsafe_allow_html=True,
)
st.caption(
    f"Permanently deletes {cust['name']} and ALL of their quotes "
    f"({cust['job_count']} on file), including PDFs, snapshots, and event log. "
    "This is irreversible."
)

_confirm_key = f"_del_cust_confirm_{cust['customer_id']}"
if st.session_state.get(_confirm_key):
    dc1, dc2, _ = st.columns([1, 1, 3])
    with dc1:
        if st.button(f"Confirm — DELETE {cust['name']}",
                     type="primary", use_container_width=True,
                     key=f"_del_cust_yes_{cust['customer_id']}"):
            result = delete_customer(cust["customer_id"])
            st.session_state[_confirm_key] = False
            st.success(
                f"Deleted {cust['name']} — {result['quotes_removed']} quote(s) "
                f"and {result['snapshots_removed']} snapshot file(s) also removed."
            )
            st.query_params.clear()
            st.rerun()
    with dc2:
        if st.button("Cancel", use_container_width=True,
                     key=f"_del_cust_cancel_{cust['customer_id']}"):
            st.session_state[_confirm_key] = False
            st.rerun()
else:
    if st.button("Delete customer", key=f"_del_cust_btn_{cust['customer_id']}",
                 help="Two-step. Click here, then click Confirm."):
        st.session_state[_confirm_key] = True
        st.rerun()
