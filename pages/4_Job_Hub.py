"""Job Hub — everything about a single job in one place.

Per Michael's design: this is where the quote becomes a "job" with the
contract drafter, send buttons, attachments, project plan, event log, and
the three-tier takeoff (project → bucket tab → entries).
"""

import json
from pathlib import Path

import streamlit as st

from server.schemas import CostBucket, LineItemEntry, QuoteStatus
from tools.calculator import JOB_TYPES
from tools.outputs.contract_drafter import draft_contract_text, draft_contract_text_ai
from tools.outputs.contract_drafter import is_ai_configured as contract_ai_configured
from tools.outputs.email_sender import is_configured as email_configured
from tools.outputs.email_sender import send_email
from tools.outputs.pdf_generator import is_configured as pdf_configured
from tools.outputs.pdf_generator import render_contract_pdf, render_quote_pdf
from tools.outputs.sheets_sync import is_configured as sheets_configured
from tools.outputs.sheets_sync import push_full_sync
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
    init_db,
    load_events,
    load_quote,
    log_event,
    mark_status,
    save_quote,
)
from tools.storage.paths import attachments_dir, data_dir

st.set_page_config(page_title="BMDW · Job Hub", page_icon="◆", layout="wide")
apply_theme()
require_auth()
init_db()

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
COMPANY = json.loads((CONFIG_DIR / "company.json").read_text())

STATUS_COLORS = {
    "draft": "#64748b",
    "sent": RAG_YELLOW,
    "won": RAG_GREEN,
    "lost": RAG_RED,
}
URGENCY_COLORS = {"low": "#64748b", "moderate": "#3b82f6", "high": RAG_RED}

BUCKET_TO_CATALOGUE = {
    CostBucket.MATERIALS: "materials",
    CostBucket.EQUIPMENT: "equipment",
    CostBucket.TRUCKING: "trucking",
    CostBucket.LABOUR: "labour",
    CostBucket.SPOIL: None,  # freeform only
}


def _load_cat(name: str) -> dict:
    path = CONFIG_DIR / f"{name}.json"
    data = json.loads(path.read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _entry_from_catalogue(bucket: CostBucket, cat_key: str, qty: float) -> LineItemEntry:
    cat_name = BUCKET_TO_CATALOGUE[bucket]
    cat = _load_cat(cat_name)
    item = cat[cat_key]
    insurance_eligible = bool(item.get("rental_insurance_eligible", True))

    if cat_name == "materials":
        unit, cost = item["unit"], float(item["cost_per_unit"])
    elif cat_name == "equipment":
        if item.get("hourly_rate"):
            unit, cost = "hour", float(item["hourly_rate"])
        elif item.get("daily_rate"):
            unit, cost = "day", float(item["daily_rate"])
        else:
            unit, cost = "each", 0.0
    elif cat_name == "trucking":
        unit, cost = "load", float(item.get("per_load_rate", 0))
    elif cat_name == "labour":
        unit, cost = "hour", float(item["hourly_rate"])
    else:
        unit, cost = "each", 0.0

    return LineItemEntry(
        bucket=bucket,
        description=item["name"],
        quantity=float(qty),
        unit=unit,
        unit_cost=cost,
        catalogue_sku=item.get("sku"),
        rental_insurance_eligible=insurance_eligible,
    )


# ---- Resolve quote ------------------------------------------------------

quote_id = st.query_params.get("quote_id")
if not quote_id:
    # Fallback for the case where another page just saved a quote and routed
    # us here — st.query_params may not have flushed yet, but session_state did.
    quote_id = st.session_state.pop("_pending_quote_id", None)
    if quote_id:
        st.query_params["quote_id"] = quote_id

if not quote_id:
    st.error("No quote selected. Open a quote from the Customers page.")
    st.stop()

q = load_quote(quote_id)
if q is None:
    st.error(f"Quote {quote_id} not found.")
    st.stop()


# ---- Header --------------------------------------------------------------

if st.button("← Back to customer"):
    st.query_params.clear()
    st.switch_page("pages/3_Customers.py")

st.markdown(f"## {q.customer.name} — {q.quote_id}")

contact_bits = []
if q.customer.phone:
    contact_bits.append(q.customer.phone)
if q.customer.email:
    contact_bits.append(q.customer.email)
contact_line = " · ".join(contact_bits)

from tools.storage.db import _slug as _customer_slug
customer_id_for_link = f"CUST-{_customer_slug(q.customer.name)[:20] or 'unnamed'}"

st.markdown(
    f'<div style="color:#64748b;font-size:13px;margin-bottom:8px;">'
    f"📍 {q.effective_site_address}"
    f"{'<br>📞 ' + contact_line if contact_line else ''}"
    f'<br><a href="/Customers?customer_id={customer_id_for_link}" target="_self" '
    f'style="color:#3b82f6;font-size:12px;text-decoration:none;">'
    f"✏ Edit customer info →</a>"
    f"</div>",
    unsafe_allow_html=True,
)

status_color = STATUS_COLORS.get(q.status.value, "#64748b")
urgency_color = URGENCY_COLORS.get(q.urgency.value, "#64748b")
st.markdown(
    f'<div style="display:inline-block;background:{status_color};color:white;'
    f'font-size:11px;font-weight:700;padding:4px 10px;border-radius:6px;'
    f'text-transform:uppercase;letter-spacing:0.06em;margin-right:8px;">{q.status.value}</div>'
    f'<div style="display:inline-block;background:{urgency_color};color:white;'
    f'font-size:11px;font-weight:700;padding:4px 10px;border-radius:6px;'
    f'text-transform:uppercase;letter-spacing:0.06em;">urgency · {q.urgency.value}</div>',
    unsafe_allow_html=True,
)

if q.quick_notes:
    st.markdown(
        f'<div style="background:#111827;border:1px solid #1e293b;'
        f'border-left:4px solid #3b82f6;border-radius:12px;'
        f'padding:14px 18px;margin-top:14px;">'
        f'<div style="color:#64748b;font-size:11px;text-transform:uppercase;'
        f'letter-spacing:0.1em;margin-bottom:6px;">Quick notes (on-site)</div>'
        f'<div style="color:#cbd5e1;font-size:13px;white-space:pre-wrap;">{q.quick_notes}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )


# ---- Pricing chain + adjustments ---------------------------------------

st.markdown("&nbsp;", unsafe_allow_html=True)
section_header("Pricing")

c1, c2, c3 = st.columns(3)
c1.metric("CUSTOMER TOTAL", fmt_money(q.customer_total))
c2.metric("Internal Cost", fmt_money(q.internal_cost))
c3.metric("Margin", f"{q.margin_pct}%")


chain_col, controls_col = st.columns([3, 2])
with chain_col:
    insurance_line = ""
    if q.rental_insurance_amount > 0:
        insurance_line = (
            f'<div style="display:flex;justify-content:space-between;color:#94a3b8;font-size:13px;padding:4px 0;">'
            f"<span>+ Rental insurance ({q.rental_insurance_pct:g}% on eligible equipment)</span>"
            f"<span style='color:#cbd5e1;'>{fmt_money(q.rental_insurance_amount)}</span>"
            f"</div>"
        )
    discount_line = ""
    if q.discount_amount > 0:
        discount_line = (
            f'<div style="display:flex;justify-content:space-between;color:#22c55e;font-size:13px;padding:4px 0;">'
            f"<span>− Discount ({q.discount_pct:g}%)</span>"
            f"<span>−{fmt_money(q.discount_amount)}</span>"
            f"</div>"
        )
    st.markdown(
        f'<div style="background:#111827;border:1px solid #1e293b;border-radius:12px;padding:18px 20px;">'
        f'<div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:10px;">Pricing chain</div>'
        f'<div style="display:flex;justify-content:space-between;color:#cbd5e1;font-size:13px;padding:4px 0;">'
        f"<span>Raw entries (5 buckets)</span><span>{fmt_money(q.raw_entries_total)}</span></div>"
        f"{insurance_line}"
        f'<div style="display:flex;justify-content:space-between;color:#cbd5e1;font-size:13px;padding:4px 0;border-top:1px solid #1e293b;margin-top:6px;padding-top:8px;">'
        f"<span><strong>Internal cost</strong></span><span><strong>{fmt_money(q.internal_cost)}</strong></span></div>"
        f'<div style="display:flex;justify-content:space-between;color:#94a3b8;font-size:13px;padding:4px 0;">'
        f"<span>+ Markup ({q.markup.overall_pct:g}%)</span><span style='color:#cbd5e1;'>{fmt_money(q.markup_amount)}</span></div>"
        f'<div style="display:flex;justify-content:space-between;color:#cbd5e1;font-size:13px;padding:4px 0;border-top:1px solid #1e293b;margin-top:6px;padding-top:8px;">'
        f"<span>Subtotal pre-discount</span><span>{fmt_money(q.subtotal_pre_discount)}</span></div>"
        f"{discount_line}"
        f'<div style="display:flex;justify-content:space-between;color:#cbd5e1;font-size:13px;padding:4px 0;border-top:1px solid #1e293b;margin-top:6px;padding-top:8px;">'
        f"<span>Subtotal</span><span>{fmt_money(q.subtotal)}</span></div>"
        f'<div style="display:flex;justify-content:space-between;color:#94a3b8;font-size:13px;padding:4px 0;">'
        f"<span>+ GST + PST ({q.tax_pct:g}%)</span><span style='color:#cbd5e1;'>{fmt_money(q.tax_amount)}</span></div>"
        f'<div style="display:flex;justify-content:space-between;color:#f1f5f9;font-size:16px;font-weight:700;padding:8px 0 0;border-top:1px solid #1e293b;margin-top:6px;">'
        f"<span>Customer total</span><span>{fmt_money(q.customer_total)}</span></div>"
        f"</div>",
        unsafe_allow_html=True,
    )

with controls_col:
    with st.form("pricing_controls"):
        st.markdown(
            '<div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px;">Adjust pricing</div>',
            unsafe_allow_html=True,
        )
        new_markup = st.number_input(
            "Markup %", min_value=0.0, max_value=300.0, step=1.0,
            value=float(q.markup.overall_pct),
            help="Default 40% from company config.",
        )
        new_discount = st.number_input(
            "Discount %", min_value=0.0, max_value=100.0, step=0.5,
            value=float(q.discount_pct),
            help="Customer never sees this — they see the final total only.",
        )
        new_discount_flat = st.number_input(
            "Discount $ (flat, optional)", min_value=0.0, step=10.0,
            value=float(q.discount_flat),
        )
        new_tax = st.number_input(
            "Tax %", min_value=0.0, max_value=30.0, step=0.5,
            value=float(q.tax_pct),
            help="GST + PST = 12% in BC.",
        )
        new_insurance = st.number_input(
            "Rental insurance %", min_value=0.0, max_value=50.0, step=1.0,
            value=float(q.rental_insurance_pct),
            help="Applied only to equipment-bucket entries flagged as insurance-eligible (excludes trucks).",
        )
        if st.form_submit_button("Apply", use_container_width=True, type="primary"):
            q.markup.overall_pct = float(new_markup)
            q.discount_pct = float(new_discount)
            q.discount_flat = float(new_discount_flat)
            q.tax_pct = float(new_tax)
            q.rental_insurance_pct = float(new_insurance)
            save_quote(q)
            log_event(q.quote_id, "pricing_adjusted", {
                "markup_pct": new_markup, "discount_pct": new_discount,
                "discount_flat": new_discount_flat,
                "tax_pct": new_tax, "rental_insurance_pct": new_insurance,
            })
            st.rerun()


# ---- 5-bucket totals (across whole quote) -------------------------------

section_header("Bucket Totals (whole quote)")
b1, b2, b3, b4, b5 = st.columns(5)
b1.metric("Labour", fmt_money(q.bucket_total(CostBucket.LABOUR)))
b2.metric("Materials", fmt_money(q.bucket_total(CostBucket.MATERIALS)))
b3.metric("Equipment", fmt_money(q.bucket_total(CostBucket.EQUIPMENT)))
b4.metric("Trucking", fmt_money(q.bucket_total(CostBucket.TRUCKING)))
b5.metric("Spoil", fmt_money(q.bucket_total(CostBucket.SPOIL)))


# ---- Tabs ----------------------------------------------------------------

section_header("Job Details")

tab_desc, tab_takeoff, tab_sheet, tab_math, tab_contract, tab_plan, tab_events, tab_attach = st.tabs(
    ["Description", "✏ Edit Quote", "📊 Spreadsheet", "Math Breakdown", "Contract", "Plan", "Events", "Attachments"]
)

with tab_desc:
    st.markdown("### Project description")
    bullets = "\n".join(f"- **{li.label}**" for li in q.line_items)
    st.markdown(bullets if bullets else "_No projects yet._")
    if q.notes:
        st.markdown(f"_Notes: {q.notes}_")


with tab_takeoff:
    st.markdown("### Edit quote — every line, every project")
    st.markdown(
        '<div style="background:#111827;border:1px solid #1e293b;'
        'border-left:4px solid #3b82f6;border-radius:8px;'
        'padding:12px 16px;margin-bottom:12px;color:#cbd5e1;font-size:13px;">'
        "📝 <strong>This is where you fix anything in the quote after the AI generated it.</strong> "
        "Tap a project to expand → tap a bucket tab (Labour / Materials / Equipment / Trucking / Spoil) "
        "→ tap the ✏ button next to a line to edit, or use the forms at the bottom of each bucket "
        "to add from the catalogue or as a freeform line. Changes save automatically."
        "</div>",
        unsafe_allow_html=True,
    )

    job_type_labels = {j["key"]: j["label"] for j in JOB_TYPES}
    edited = False

    if not q.line_items:
        st.info("No projects on this quote yet. Add one from the capture screen.")

    for li_idx, li in enumerate(q.line_items):
        proj_total = li.internal_cost
        proj_label = (
            f"◆ {li.label}  ·  {job_type_labels.get(li.job_type, li.job_type)}  ·  "
            f"{fmt_money(proj_total) if li.entries else '—'}"
        )

        with st.expander(proj_label, expanded=(li_idx == 0)):
            # Per-project bucket totals strip
            cols = st.columns(5)
            for col, bucket in zip(cols, CostBucket):
                col.metric(bucket.value.title(), fmt_money(li.bucket_total(bucket)))

            st.markdown("&nbsp;", unsafe_allow_html=True)

            # Bucket TABS — drilldown level 2
            bucket_list = list(CostBucket)
            bucket_tabs = st.tabs([
                f"{bucket.value.title()} · {fmt_money(li.bucket_total(bucket))}"
                for bucket in bucket_list
            ])

            for tab, bucket in zip(bucket_tabs, bucket_list):
                with tab:
                    entries = [(i, e) for i, e in enumerate(li.entries) if e.bucket == bucket]

                    if not entries:
                        st.caption(f"No {bucket.value} entries yet. Add one below.")
                    else:
                        for entry_idx, e in entries:
                            row_key = f"row_{q.quote_id}_{li_idx}_{entry_idx}"
                            edit_flag_key = f"editing_{row_key}"
                            editing = st.session_state.get(edit_flag_key, False)

                            if not editing:
                                ec = st.columns([5, 2, 2, 1])
                                ec[0].markdown(
                                    f'<div style="color:#cbd5e1;font-size:13px;padding:4px 0;">'
                                    f"{e.description}</div>",
                                    unsafe_allow_html=True,
                                )
                                ec[1].markdown(
                                    f'<div style="color:#94a3b8;font-size:12px;padding:4px 0;text-align:right;">'
                                    f"{e.quantity:g} {e.unit} × {fmt_money(e.unit_cost)}</div>",
                                    unsafe_allow_html=True,
                                )
                                ec[2].markdown(
                                    f'<div style="color:#f1f5f9;font-size:13px;font-weight:700;padding:4px 0;text-align:right;">'
                                    f"{fmt_money(e.total_cost)}</div>",
                                    unsafe_allow_html=True,
                                )
                                with ec[3]:
                                    if st.button("✏", key=f"edit_btn_{row_key}", help="Edit"):
                                        st.session_state[edit_flag_key] = True
                                        st.rerun()
                            else:
                                with st.form(key=f"form_{row_key}"):
                                    fc = st.columns([3, 1, 1])
                                    new_desc = fc[0].text_input("Description", value=e.description, key=f"desc_{row_key}")
                                    new_qty = fc[1].number_input("Qty", min_value=0.0, value=float(e.quantity), step=0.5, key=f"qty_{row_key}")
                                    new_cost = fc[2].number_input("Unit $", min_value=0.0, value=float(e.unit_cost), step=0.10, key=f"cost_{row_key}")
                                    fr = st.columns([1, 1, 1, 1])
                                    new_unit = fr[0].text_input("Unit", value=e.unit, key=f"unit_{row_key}")
                                    new_eligible = fr[1].checkbox(
                                        "Insurance eligible", value=bool(e.rental_insurance_eligible),
                                        help="Only meaningful in Equipment bucket. Trucks should be unticked.",
                                        key=f"eligible_{row_key}",
                                    )
                                    saved = fr[2].form_submit_button("Save", use_container_width=True, type="primary")
                                    cancelled = fr[3].form_submit_button("Cancel", use_container_width=True)
                                    deleted = st.form_submit_button("Delete row")

                                    if saved:
                                        e.description = new_desc
                                        e.quantity = float(new_qty)
                                        e.unit_cost = float(new_cost)
                                        e.unit = new_unit
                                        e.rental_insurance_eligible = bool(new_eligible)
                                        edited = True
                                        st.session_state[edit_flag_key] = False
                                    elif cancelled:
                                        st.session_state[edit_flag_key] = False
                                        st.rerun()
                                    elif deleted:
                                        li.entries.pop(entry_idx)
                                        edited = True
                                        st.session_state[edit_flag_key] = False

                    # ---- Add-entry section ----
                    st.markdown("&nbsp;", unsafe_allow_html=True)
                    cat_name = BUCKET_TO_CATALOGUE[bucket]
                    add_key_base = f"add_{q.quote_id}_{li_idx}_{bucket.value}"

                    if cat_name:
                        cat = _load_cat(cat_name)
                        # Catalogue add form
                        with st.form(f"{add_key_base}_cat", clear_on_submit=True):
                            st.caption(f"Add from {cat_name} catalogue")
                            if not cat:
                                st.caption(f"(empty — add items on the {cat_name.title()} page)")
                                st.form_submit_button("(disabled)", disabled=True)
                            else:
                                ac1, ac2, ac3 = st.columns([3, 1, 1])
                                pick = ac1.selectbox(
                                    "Item", list(cat.keys()),
                                    format_func=lambda k, c=cat: c[k]["name"],
                                    label_visibility="collapsed",
                                    key=f"pick_{add_key_base}",
                                )
                                qty = ac2.number_input("Qty", min_value=0.0, value=1.0, step=0.5,
                                                      label_visibility="collapsed", key=f"qty_{add_key_base}")
                                add_btn = ac3.form_submit_button("+ Add", use_container_width=True)
                                if add_btn and qty > 0:
                                    li.entries.append(_entry_from_catalogue(bucket, pick, qty))
                                    edited = True

                    # Freeform add form (every bucket)
                    with st.form(f"{add_key_base}_free", clear_on_submit=True):
                        st.caption("Or add a custom freeform line")
                        fc1, fc2, fc3, fc4 = st.columns([2, 1, 1, 1])
                        new_desc = fc1.text_input(
                            "Description", placeholder="e.g. Fuel — estimated",
                            label_visibility="collapsed", key=f"desc_{add_key_base}_free",
                        )
                        new_qty = fc2.number_input(
                            "Qty", min_value=0.0, value=1.0, step=0.5,
                            label_visibility="collapsed", key=f"qty_{add_key_base}_free",
                        )
                        new_cost = fc3.number_input(
                            "Unit $", min_value=0.0, value=0.0, step=1.0,
                            label_visibility="collapsed", key=f"cost_{add_key_base}_free",
                        )
                        free_add = fc4.form_submit_button("+ Add", use_container_width=True)
                        if free_add and new_desc and new_qty > 0:
                            li.entries.append(LineItemEntry(
                                bucket=bucket,
                                description=new_desc,
                                quantity=float(new_qty),
                                unit_cost=float(new_cost),
                                unit="lump",
                                rental_insurance_eligible=False,
                            ))
                            edited = True

            # ---- Per-project NOTES + FILES (BC One Call, permits, plans, photos) ----
            st.markdown("&nbsp;", unsafe_allow_html=True)
            st.markdown(
                '<div style="color:#64748b;font-size:11px;font-weight:700;'
                'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">'
                "Project notes + files</div>",
                unsafe_allow_html=True,
            )

            note_val = st.text_area(
                "Project notes",
                value=li.project_notes or "",
                key=f"pnotes_{li_idx}",
                height=100,
                placeholder="Site reminders, customer asks, deviations, anything specific to THIS project.",
                label_visibility="collapsed",
            )
            if note_val != (li.project_notes or ""):
                li.project_notes = note_val or None
                edited = True

            # File upload — accepts photos, PDFs, anything. Saves under
            # <data_dir>/attachments/<quote_id>/<project_idx>/ (persistent on Render).
            attach_dir = attachments_dir() / q.quote_id / str(li_idx)

            uploaded = st.file_uploader(
                "Upload photos / plans / permits / BC One Call docs",
                accept_multiple_files=True,
                key=f"upload_{q.quote_id}_{li_idx}",
                type=None,  # any file type
            )
            if uploaded:
                attach_dir.mkdir(parents=True, exist_ok=True)
                added = 0
                for f in uploaded:
                    target = attach_dir / f.name
                    target.write_bytes(f.read())
                    # Store relative to the persistent data dir (works locally + on Render disk).
                    rel = str(target.relative_to(data_dir()))
                    if rel not in li.attachments:
                        li.attachments.append(rel)
                        added += 1
                if added:
                    log_event(q.quote_id, "project_files_added",
                              {"project": li.label, "count": added})
                    edited = True
                    st.success(f"Added {added} file(s).")

            # Existing attachments list
            if li.attachments:
                st.markdown(
                    '<div style="color:#94a3b8;font-size:11px;'
                    'margin-top:8px;margin-bottom:4px;">'
                    f"📎 {len(li.attachments)} file(s) on this project</div>",
                    unsafe_allow_html=True,
                )
                root = data_dir()
                for a_idx, rel_path in list(enumerate(li.attachments)):
                    full = root / rel_path
                    fname = Path(rel_path).name
                    fcols = st.columns([5, 1, 1])
                    fcols[0].markdown(
                        f'<div style="color:#cbd5e1;font-size:13px;padding:6px 0;">'
                        f"📄 {fname}</div>",
                        unsafe_allow_html=True,
                    )
                    if full.exists():
                        with fcols[1]:
                            st.download_button(
                                "⬇", data=full.read_bytes(), file_name=fname,
                                key=f"dl_{q.quote_id}_{li_idx}_{a_idx}",
                                use_container_width=True,
                            )
                    with fcols[2]:
                        if st.button("✕", key=f"rm_{q.quote_id}_{li_idx}_{a_idx}",
                                     use_container_width=True, help="Remove from list"):
                            li.attachments.pop(a_idx)
                            try:
                                if full.exists():
                                    full.unlink()
                            except Exception:
                                pass
                            edited = True
                            st.rerun()

            # Project actions at bottom
            st.markdown("&nbsp;", unsafe_allow_html=True)
            pa1, pa2 = st.columns([3, 1])
            with pa1:
                new_label = st.text_input(
                    "Rename project", value=li.label, key=f"rename_{li_idx}",
                    label_visibility="collapsed",
                )
                if new_label != li.label:
                    li.label = new_label
                    edited = True
            with pa2:
                if st.button("🗑 Delete project", key=f"del_proj_{li_idx}", use_container_width=True):
                    q.line_items.pop(li_idx)
                    save_quote(q)
                    log_event(q.quote_id, "project_deleted", {"label": li.label})
                    st.rerun()

    if edited:
        save_quote(q)
        log_event(q.quote_id, "takeoff_edited")
        st.rerun()


# ---- Material Takeoff (read-only, table format) -----------------------

def _render_entry_rows(entries):
    """Render a list of LineItemEntry as a table-style markdown."""
    if not entries:
        st.caption("(none)")
        return
    head = st.columns([4, 2, 2])
    head[0].markdown('<div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:0.06em;">Item</div>', unsafe_allow_html=True)
    head[1].markdown('<div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:0.06em;text-align:right;">Math</div>', unsafe_allow_html=True)
    head[2].markdown('<div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:0.06em;text-align:right;">Cost</div>', unsafe_allow_html=True)
    st.markdown('<div style="border-top:1px solid #1e293b;margin:4px 0 6px;"></div>', unsafe_allow_html=True)
    subtotal = 0.0
    for e in entries:
        c = st.columns([4, 2, 2])
        c[0].markdown(f'<div style="color:#cbd5e1;font-size:13px;padding:4px 0;">{e.description}</div>', unsafe_allow_html=True)
        c[1].markdown(f'<div style="color:#94a3b8;font-size:12px;padding:4px 0;text-align:right;">{e.quantity:g} {e.unit} × {fmt_money(e.unit_cost)}</div>', unsafe_allow_html=True)
        c[2].markdown(f'<div style="color:#f1f5f9;font-size:13px;font-weight:700;padding:4px 0;text-align:right;">{fmt_money(e.total_cost)}</div>', unsafe_allow_html=True)
        subtotal += e.total_cost
    st.markdown('<div style="border-top:1px solid #1e293b;margin:6px 0 4px;"></div>', unsafe_allow_html=True)
    sf = st.columns([4, 2, 2])
    sf[0].markdown('<div style="color:#94a3b8;font-size:12px;padding:4px 0;">Subtotal</div>', unsafe_allow_html=True)
    sf[2].markdown(f'<div style="color:#f1f5f9;font-size:14px;font-weight:700;padding:4px 0;text-align:right;">{fmt_money(subtotal)}</div>', unsafe_allow_html=True)


with tab_sheet:
    import pandas as pd

    st.markdown("### 📊 Spreadsheet — every line item, all 5 buckets")
    st.caption(
        "Sortable, searchable, exportable. Click any column header to sort. "
        "Click the ⬇ icon (top-right of the table) to download as CSV. "
        "Order: Equipment → Materials → Labour → Trucking → Spoil."
    )

    if not q.line_items:
        st.info("No projects yet.")
    else:
        # Bucket display order per Michael's preference (Equipment first).
        BUCKET_ORDER = [
            CostBucket.EQUIPMENT,
            CostBucket.MATERIALS,
            CostBucket.LABOUR,
            CostBucket.TRUCKING,
            CostBucket.SPOIL,
        ]
        bucket_label = {
            CostBucket.EQUIPMENT: "Equipment",
            CostBucket.MATERIALS: "Materials",
            CostBucket.LABOUR: "Labour",
            CostBucket.TRUCKING: "Trucking",
            CostBucket.SPOIL: "Spoil",
        }

        rows = []
        for li in q.line_items:
            for bucket in BUCKET_ORDER:
                for e in li.entries:
                    if e.bucket != bucket:
                        continue
                    rows.append({
                        "Bucket": bucket_label[bucket],
                        "Project": li.label,
                        "Description": e.description,
                        "Qty": e.quantity,
                        "Unit": e.unit,
                        "Unit Cost": e.unit_cost,
                        "Line Total": e.total_cost,
                        "Insurance": "✓" if (e.bucket == CostBucket.EQUIPMENT and e.rental_insurance_eligible) else "",
                    })

        if not rows:
            st.info("No line items yet. Use the ✏ Edit Quote tab to add them.")
        else:
            df = pd.DataFrame(rows)
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Qty": st.column_config.NumberColumn(format="%.2f"),
                    "Unit Cost": st.column_config.NumberColumn(format="$%.2f"),
                    "Line Total": st.column_config.NumberColumn(format="$%.2f"),
                    "Insurance": st.column_config.TextColumn(
                        help="Equipment line is eligible for the rental-insurance surcharge",
                        width="small",
                    ),
                },
            )

            # Per-bucket subtotals strip
            st.markdown("&nbsp;", unsafe_allow_html=True)
            st.markdown(
                '<div style="color:#64748b;font-size:11px;font-weight:700;'
                'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Bucket subtotals</div>',
                unsafe_allow_html=True,
            )
            sub_cols = st.columns(5)
            for col, bucket in zip(sub_cols, BUCKET_ORDER):
                col.metric(bucket_label[bucket], fmt_money(q.bucket_total(bucket)))

        # ---- Pricing chain (the "Total" Michael wants at the end) ----
        st.markdown("&nbsp;", unsafe_allow_html=True)
        st.markdown(
            '<div style="color:#64748b;font-size:11px;font-weight:700;'
            'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">'
            "Quote total (full pricing chain)</div>",
            unsafe_allow_html=True,
        )
        chain_rows = [
            ("Raw entries (sum of all line totals)", q.raw_entries_total),
            (f"+ Rental insurance ({q.rental_insurance_pct:g}% on eligible equipment)",
             q.rental_insurance_amount),
            ("= Internal cost (BMDW total)", q.internal_cost),
            (f"+ Markup ({q.markup.overall_pct:g}%)", q.markup_amount),
            ("= Subtotal pre-discount", q.subtotal_pre_discount),
        ]
        if q.discount_amount > 0:
            chain_rows.append((f"− Discount ({q.discount_pct:g}%"
                               + (f" + ${q.discount_flat:g} flat" if q.discount_flat else "")
                               + ")", -q.discount_amount))
        chain_rows.append(("= Subtotal", q.subtotal))
        chain_rows.append((f"+ GST + PST ({q.tax_pct:g}%)", q.tax_amount))
        chain_rows.append(("= CUSTOMER TOTAL", q.customer_total))

        for label, amount in chain_rows:
            is_total = label.startswith("= CUSTOMER TOTAL")
            is_subtotal = label.startswith("=") and not is_total
            color = "#f1f5f9" if (is_total or is_subtotal) else "#cbd5e1"
            weight = "700" if is_total else ("600" if is_subtotal else "400")
            size = "16px" if is_total else "13px"
            border = ("border-top:1px solid #1e293b;margin-top:6px;padding-top:8px;"
                      if is_subtotal or is_total else "")
            row = st.columns([6, 2])
            row[0].markdown(
                f'<div style="color:{color};font-size:{size};font-weight:{weight};'
                f'padding:4px 0;{border}">{label}</div>',
                unsafe_allow_html=True,
            )
            row[1].markdown(
                f'<div style="color:{color};font-size:{size};font-weight:{weight};'
                f'padding:4px 0;text-align:right;{border}">{fmt_money(amount)}</div>',
                unsafe_allow_html=True,
            )


with tab_math:
    st.markdown("### Math Breakdown")
    st.caption("Line-by-line cost math, per project, per bucket. Internal view only.")

    bucket_label_color = {
        CostBucket.MATERIALS: "#22c55e",
        CostBucket.LABOUR: "#3b82f6",
        CostBucket.EQUIPMENT: "#f59e0b",
        CostBucket.TRUCKING: "#8b5cf6",
        CostBucket.SPOIL: "#ef4444",
    }
    if not q.line_items:
        st.info("No projects yet.")
    for li in q.line_items:
        st.markdown(f"#### ◆ {li.label}  ·  {fmt_money(li.internal_cost)}")
        for bucket in CostBucket:
            entries = [e for e in li.entries if e.bucket == bucket]
            if not entries:
                continue
            color = bucket_label_color[bucket]
            st.markdown(
                f'<div style="color:{color};font-size:12px;font-weight:700;'
                f'text-transform:uppercase;letter-spacing:0.06em;'
                f'margin-top:14px;margin-bottom:6px;">{bucket.value}</div>',
                unsafe_allow_html=True,
            )
            _render_entry_rows(entries)
        st.markdown("---")

    # Pricing chain at the bottom in same table format
    st.markdown("#### Pricing Chain")
    chain = [
        ("Raw entries (5 buckets)", "", q.raw_entries_total),
    ]
    if q.rental_insurance_amount > 0:
        chain.append(("+ Rental insurance",
                      f"{q.rental_insurance_pct:g}% × {fmt_money(q.rental_insurance_subtotal)} eligible",
                      q.rental_insurance_amount))
    chain.append(("Internal cost", "", q.internal_cost))
    chain.append(("+ Markup", f"{q.markup.overall_pct:g}% × {fmt_money(q.internal_cost)}", q.markup_amount))
    chain.append(("Subtotal pre-discount", "", q.subtotal_pre_discount))
    if q.discount_amount > 0:
        chain.append(("− Discount", f"{q.discount_pct:g}% (+ ${q.discount_flat:g} flat)" if q.discount_flat else f"{q.discount_pct:g}%", -q.discount_amount))
    chain.append(("Subtotal", "", q.subtotal))
    chain.append(("+ GST + PST", f"{q.tax_pct:g}% × {fmt_money(q.subtotal)}", q.tax_amount))
    chain.append(("CUSTOMER TOTAL", "", q.customer_total))

    head = st.columns([4, 3, 2])
    head[0].markdown('<div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:0.06em;">Step</div>', unsafe_allow_html=True)
    head[1].markdown('<div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:0.06em;">Math</div>', unsafe_allow_html=True)
    head[2].markdown('<div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:0.06em;text-align:right;">Amount</div>', unsafe_allow_html=True)
    for i, (label, math, amount) in enumerate(chain):
        is_total = label == "CUSTOMER TOTAL"
        is_subtotal = label.startswith("Subtotal") or label == "Internal cost"
        weight = "700" if is_total else ("600" if is_subtotal else "400")
        size = "16px" if is_total else "13px"
        color = "#f1f5f9" if (is_total or is_subtotal) else "#cbd5e1"
        amt_color = "#f1f5f9" if (is_total or is_subtotal) else "#cbd5e1"
        if str(amount).startswith("-") or amount < 0:
            amt_color = "#22c55e"
        c = st.columns([4, 3, 2])
        c[0].markdown(f'<div style="color:{color};font-size:{size};font-weight:{weight};padding:4px 0;">{label}</div>', unsafe_allow_html=True)
        c[1].markdown(f'<div style="color:#94a3b8;font-size:12px;padding:4px 0;">{math}</div>', unsafe_allow_html=True)
        c[2].markdown(f'<div style="color:{amt_color};font-size:{size};font-weight:{weight};padding:4px 0;text-align:right;">{fmt_money(amount)}</div>', unsafe_allow_html=True)


with tab_contract:
    st.markdown("### Contract")
    is_custom = q.contract_text is not None and q.contract_text.strip() != ""
    initial = q.contract_text if is_custom else draft_contract_text(q, COMPANY)

    badge = ("✓ Custom (saved)" if is_custom else "○ Auto-drafted (not yet edited)")
    badge_color = "#22c55e" if is_custom else "#64748b"
    st.markdown(
        f'<div style="color:{badge_color};font-size:11px;font-weight:700;'
        f'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">{badge}</div>',
        unsafe_allow_html=True,
    )

    # AI regenerate is outside the form so it can run + save without
    # requiring the user to also press Save.
    ai_help = ("Use Anthropic to rewrite the SCOPE OF WORK in plain customer "
               "language. Price, payment terms, and parties stay deterministic.")
    if not contract_ai_configured():
        ai_help += " (Set ANTHROPIC_API_KEY in .env to enable.)"
    if st.button("✨ Regenerate scope with AI", help=ai_help,
                 disabled=not contract_ai_configured()):
        with st.spinner("Narrating scope of work..."):
            ai_text = draft_contract_text_ai(q, COMPANY)
        q.contract_text = ai_text
        save_quote(q)
        log_event(q.quote_id, "contract_ai_regenerated", {"chars": len(ai_text)})
        st.success("Contract regenerated with AI narration.")
        st.rerun()

    with st.form("contract_form"):
        edited_text = st.text_area(
            "Contract", value=initial, height=460, label_visibility="collapsed",
            key=f"contract_text_{q.quote_id}",
        )
        cf1, cf2, cf3 = st.columns([1, 1, 4])
        with cf1:
            save_clicked = st.form_submit_button("Save changes", type="primary",
                                                 use_container_width=True)
        with cf2:
            reset_clicked = st.form_submit_button("Reset to auto-draft",
                                                  use_container_width=True)

        if save_clicked:
            q.contract_text = edited_text
            save_quote(q)
            log_event(q.quote_id, "contract_edited", {"chars": len(edited_text)})
            st.success("Contract saved.")
            st.rerun()
        if reset_clicked:
            q.contract_text = None
            save_quote(q)
            log_event(q.quote_id, "contract_reset")
            st.rerun()

    st.caption(
        "Customer accepts by reply email or first payment. "
        "Insurance/WCB papers attach automatically when sent."
    )


with tab_plan:
    st.markdown("### Project plan")

    # ---- Standard pre-work milestones (always shown, every project) ----
    st.markdown(
        '<div style="color:#64748b;font-size:11px;text-transform:uppercase;'
        'letter-spacing:0.1em;margin-bottom:8px;">Pre-work milestones (every project)</div>',
        unsafe_allow_html=True,
    )
    deposit_label = "Receive 50% deposit"
    if q.customer_total > 50000:
        deposit_label = "Receive Phase 1 payment (project > $50K, phase plan applies)"
    else:
        deposit_label += f"  (~{fmt_money(q.customer_total / 2)})"
    pre_work = [
        "Receive approval (customer accepts the quote)",
        "Call BC One Call to locate underground utilities",
        deposit_label,
        "Mobilize equipment to site",
    ]
    for i, step in enumerate(pre_work, start=1):
        st.markdown(
            f'<div style="background:#111827;border:1px solid #1e293b;'
            f'border-left:3px solid #3b82f6;border-radius:8px;'
            f'padding:8px 14px;margin-bottom:6px;color:#cbd5e1;font-size:13px;">'
            f"<strong style='color:#f1f5f9;'>Step {i}.</strong> {step}</div>",
            unsafe_allow_html=True,
        )

    # ---- Project work days (per JobLineItem from AI parser, then quote-level) ----
    st.markdown("&nbsp;", unsafe_allow_html=True)
    st.markdown(
        '<div style="color:#64748b;font-size:11px;text-transform:uppercase;'
        'letter-spacing:0.1em;margin-bottom:8px;">Project work schedule</div>',
        unsafe_allow_html=True,
    )

    # Try per-line-item plans first
    any_plan = False
    for li in q.line_items:
        plan = li.inputs.get("project_plan") or []
        if plan:
            any_plan = True
            st.markdown(f"**{li.label}**")
            for d in plan:
                st.markdown(f"- Day {d.get('day', '?')} — {d.get('description', '')}")
    if not any_plan and q.project_plan:
        any_plan = True
        for d in q.project_plan:
            st.markdown(f"- Day {d.day} — {d.description}")
    if not any_plan:
        st.info("No work-day schedule yet — generate from notes via the Quoting page, or fill manually.")

    if q.start_date:
        st.markdown(f"**Start date:** {q.start_date.isoformat()}")


with tab_events:
    st.markdown("### Event log (audit trail)")
    events = load_events(quote_id)
    if not events:
        st.info("No events logged yet.")
    else:
        for ev in events:
            st.markdown(
                f'- `{ev["occurred_at"]}` — **{ev["event_type"]}**'
                + (f" — {ev['payload_json']}" if ev["payload_json"] else "")
            )


with tab_attach:
    st.markdown("### Attachments (sent with every contract)")
    items = [
        ("Insurance certificate", COMPANY.get("insurance_certificate_path", "TBD")),
        ("WorkSafeBC clearance", "TBD"),
        ("Business license", "TBD"),
    ]
    for label, path in items:
        exists = Path(path).exists() if path != "TBD" else False
        marker = "✓" if exists else "○"
        color = RAG_GREEN if exists else "#64748b"
        st.markdown(
            f'<div style="color:{color};font-size:13px;margin-bottom:6px;">'
            f"{marker} {label} — <code>{path}</code></div>",
            unsafe_allow_html=True,
        )


# ---- Action bar ---------------------------------------------------------

st.markdown("---")
section_header("Actions")

def _collect_attachments(*candidate_paths) -> list:
    """Filter to existing files only. Used for both quote and contract sends."""
    return [Path(p) for p in candidate_paths if p and Path(p).exists()]


a1, a2, a3 = st.columns(3)
with a1:
    if st.button("Send Quote", use_container_width=True):
        # Auto-render the PDF if WeasyPrint is available, then attach.
        quote_pdf_path = None
        if pdf_configured():
            quote_pdf_path, _ = render_quote_pdf(q, COMPANY)
        attachments = _collect_attachments(quote_pdf_path)
        result = send_email(
            to=q.customer.email or "",
            subject=f"Quote {q.quote_id} — {COMPANY.get('legal_name', 'BMDW')}",
            body_text=(
                f"Hi {q.customer.name},\n\n"
                f"Please find your quote ({q.quote_id}) attached. "
                f"Total: ${q.customer_total:,.2f} CAD (incl. tax).\n\n"
                f"Quote is valid for {COMPANY.get('quote_validity_days', 30)} days. "
                f"Reply to confirm or with any questions.\n\n"
                f"Thanks,\n{COMPANY.get('legal_name', 'BMDW')}"
            ),
            attachments=attachments,
        )
        log_event(q.quote_id, "quote_sent",
                  {"to": q.customer.email, "ok": result.get("ok"),
                   "attachments": [str(p) for p in attachments]})
        if q.status == QuoteStatus.DRAFT and result.get("ok"):
            mark_status(q.quote_id, QuoteStatus.SENT)
        if not result["ok"]:
            st.warning(result["reason"])
        else:
            st.success(f"Quote sent to {q.customer.email}.")
        st.rerun()
with a2:
    if st.button("Send Contract + Docs", use_container_width=True):
        contract_body = q.contract_text or draft_contract_text(q, COMPANY)
        contract_pdf_path = None
        if pdf_configured():
            contract_pdf_path, _ = render_contract_pdf(q, COMPANY, body_text=contract_body)
        # Always attach insurance cert if present
        insurance_cert = COMPANY.get("insurance_certificate_path")
        attachments = _collect_attachments(
            contract_pdf_path,
            insurance_cert and (Path(__file__).resolve().parents[1] / insurance_cert),
        )
        result = send_email(
            to=q.customer.email or "",
            subject=f"Contract {q.quote_id} — {COMPANY.get('legal_name', 'BMDW')}",
            body_text=(
                f"Hi {q.customer.name},\n\n"
                f"Please find the contract for {q.quote_id} attached, along with our "
                f"insurance certificate. You can accept by reply email or by making the "
                f"deposit payment.\n\n"
                f"Thanks,\n{COMPANY.get('legal_name', 'BMDW')}"
            ),
            attachments=attachments,
        )
        log_event(q.quote_id, "contract_sent",
                  {"to": q.customer.email, "ok": result.get("ok"),
                   "attachments": [str(p) for p in attachments]})
        if not result["ok"]:
            st.warning(result["reason"])
        else:
            st.success(f"Contract + attachments sent to {q.customer.email}.")
        st.rerun()
with a3:
    if st.button("Sync Sheets now", use_container_width=True):
        result = push_full_sync()
        if result["ok"]:
            st.success(f"Pushed {result['quotes_pushed']} quotes, {result['customers_pushed']} customers.")
            log_event(q.quote_id, "sheets_synced", result)
        else:
            st.warning(result["reason"])

p1, p2 = st.columns(2)
with p1:
    if st.button("Generate Quote PDF", use_container_width=True, disabled=not pdf_configured(),
                 help="Render the customer-facing quote as a PDF in data/pdfs/."):
        path, err = render_quote_pdf(q, COMPANY)
        if err:
            st.warning(err)
        else:
            log_event(q.quote_id, "quote_pdf_rendered", {"path": str(path)})
            st.success(f"Quote PDF saved → {path}")
            with open(path, "rb") as f:
                st.download_button("⬇ Download quote.pdf", data=f.read(),
                                   file_name=f"{q.quote_id}-quote.pdf",
                                   mime="application/pdf", use_container_width=True)
with p2:
    if st.button("Generate Contract PDF", use_container_width=True, disabled=not pdf_configured(),
                 help="Render the (saved or auto-drafted) contract as a PDF."):
        path, err = render_contract_pdf(q, COMPANY)
        if err:
            st.warning(err)
        else:
            log_event(q.quote_id, "contract_pdf_rendered", {"path": str(path)})
            st.success(f"Contract PDF saved → {path}")
            with open(path, "rb") as f:
                st.download_button("⬇ Download contract.pdf", data=f.read(),
                                   file_name=f"{q.quote_id}-contract.pdf",
                                   mime="application/pdf", use_container_width=True)


s1, s2, s3 = st.columns(3)
with s1:
    if st.button("Mark Won", use_container_width=True, disabled=q.status != QuoteStatus.SENT):
        mark_status(q.quote_id, QuoteStatus.WON)
        st.rerun()
with s2:
    if st.button("Mark Lost", use_container_width=True, disabled=q.status not in (QuoteStatus.SENT, QuoteStatus.DRAFT)):
        mark_status(q.quote_id, QuoteStatus.LOST)
        st.rerun()
with s3:
    final = st.number_input("Final invoiced $", min_value=0.0, value=q.customer_total, step=100.0)
    if st.button("Mark Complete + Invoice", use_container_width=True):
        mark_status(q.quote_id, QuoteStatus.WON, final_invoiced=final)
        st.rerun()


# ---- Footer status ------------------------------------------------------

st.markdown("---")
sf1, sf2, sf3 = st.columns(3)
with sf1:
    state = "✓ ready" if sheets_configured() else "○ not configured"
    st.caption(f"Google Sheets sync: {state}")
with sf2:
    state = "✓ ready" if email_configured() else "○ not configured"
    st.caption(f"Gmail send: {state}")
with sf3:
    state = "✓ ready" if pdf_configured() else "○ WeasyPrint not installed"
    st.caption(f"PDF generator: {state}")
