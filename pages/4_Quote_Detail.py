"""Quote Detail — everything about a single job in one place.

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
from tools.outputs.email_sender import (
    configured_method as email_configured_method,
    send_email,
)
from tools.outputs.pdf_generator import is_configured as pdf_configured
from tools.outputs.pdf_generator import (
    render_contract_pdf,
    render_equipment_list_pdf,
    render_invoice_pdf,
    render_material_takeoff_pdf,
    render_quote_pdf,
    render_receipt_pdf,
)
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

st.set_page_config(page_title="Black Mountain Dirt Works · Quote Detail", page_icon="", layout="wide")
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
        if item.get("hourly_rate"):
            unit, cost = "hour", float(item["hourly_rate"])
        else:
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

hb1, hb2, _ = st.columns([1.2, 1.6, 3])
with hb1:
    if st.button("← Back to customer", use_container_width=True):
        st.query_params.clear()
        st.switch_page("pages/3_Customers.py")
with hb2:
    if st.button("Edit quote with voice", use_container_width=True,
                 type="primary",
                 help="Go back to Phase 2 (clarifying questions) with this quote loaded — "
                      "dictate what to change and regenerate."):
        # Hand off to Quoting.py: load this quote as a draft AND jump straight
        # to Phase 2 in edit mode.
        st.session_state["_pending_quote_id"] = q.quote_id
        st.session_state["_voice_edit_mode"] = True
        st.query_params["quote_id"] = q.quote_id
        st.switch_page("Quoting.py")

if q.name:
    st.markdown(f"## {q.name}")
    st.markdown(
        f'<div style="color:#64748b;font-size:13px;margin-top:-12px;margin-bottom:6px;">'
        f"{q.customer.name} · Quote #{q.quote_id}</div>",
        unsafe_allow_html=True,
    )
else:
    st.markdown(f"## {q.customer.name} — #{q.quote_id}")

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
    f"Edit customer info →</a>"
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
        f'<div style="background:#ffffff;border:1px solid #e2e8f0;'
        f'border-left:4px solid #3b82f6;border-radius:12px;'
        f'padding:14px 18px;margin-top:14px;">'
        f'<div style="color:#64748b;font-size:11px;text-transform:uppercase;'
        f'letter-spacing:0.1em;margin-bottom:6px;">Quick notes (on-site)</div>'
        f'<div style="color:#334155;font-size:13px;white-space:pre-wrap;">{q.quick_notes}</div>'
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
            f'<div style="display:flex;justify-content:space-between;color:#475569;font-size:13px;padding:4px 0;">'
            f"<span>+ Rental insurance ({q.rental_insurance_pct:g}% on eligible equipment)</span>"
            f"<span style='color:#334155;'>{fmt_money(q.rental_insurance_amount)}</span>"
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
        f'<div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:18px 20px;">'
        f'<div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:10px;">Pricing chain</div>'
        f'<div style="display:flex;justify-content:space-between;color:#334155;font-size:13px;padding:4px 0;">'
        f"<span>Raw entries (5 buckets)</span><span>{fmt_money(q.raw_entries_total)}</span></div>"
        f"{insurance_line}"
        f'<div style="display:flex;justify-content:space-between;color:#334155;font-size:13px;padding:4px 0;border-top:1px solid #e2e8f0;margin-top:6px;padding-top:8px;">'
        f"<span><strong>Internal cost</strong></span><span><strong>{fmt_money(q.internal_cost)}</strong></span></div>"
        f'<div style="display:flex;justify-content:space-between;color:#475569;font-size:13px;padding:4px 0;">'
        f"<span>+ Markup ({q.markup.overall_pct:g}%)</span><span style='color:#334155;'>{fmt_money(q.markup_amount)}</span></div>"
        f'<div style="display:flex;justify-content:space-between;color:#334155;font-size:13px;padding:4px 0;border-top:1px solid #e2e8f0;margin-top:6px;padding-top:8px;">'
        f"<span>Subtotal pre-discount</span><span>{fmt_money(q.subtotal_pre_discount)}</span></div>"
        f"{discount_line}"
        f'<div style="display:flex;justify-content:space-between;color:#334155;font-size:13px;padding:4px 0;border-top:1px solid #e2e8f0;margin-top:6px;padding-top:8px;">'
        f"<span>Subtotal</span><span>{fmt_money(q.subtotal)}</span></div>"
        f'<div style="display:flex;justify-content:space-between;color:#475569;font-size:13px;padding:4px 0;">'
        f"<span>+ GST + PST ({q.tax_pct:g}%)</span><span style='color:#334155;'>{fmt_money(q.tax_amount)}</span></div>"
        f'<div style="display:flex;justify-content:space-between;color:#0f172a;font-size:16px;font-weight:700;padding:8px 0 0;border-top:1px solid #e2e8f0;margin-top:6px;">'
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

tab_desc, tab_sheet, tab_math, tab_contract, tab_plan = st.tabs(
    ["Description", "Spreadsheet (editable)", "Math Breakdown", "Contract", "Plan"]
)

with tab_desc:
    st.markdown("### Project description")
    bullets = "\n".join(f"- **{li.label}**" for li in q.line_items)
    st.markdown(bullets if bullets else "_No projects yet._")
    if q.notes:
        st.markdown(f"_Notes: {q.notes}_")



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
    st.markdown('<div style="border-top:1px solid #e2e8f0;margin:4px 0 6px;"></div>', unsafe_allow_html=True)
    subtotal = 0.0
    for e in entries:
        c = st.columns([4, 2, 2])
        c[0].markdown(f'<div style="color:#334155;font-size:13px;padding:4px 0;">{e.description}</div>', unsafe_allow_html=True)
        c[1].markdown(f'<div style="color:#475569;font-size:12px;padding:4px 0;text-align:right;">{e.quantity:g} {e.unit} × {fmt_money(e.unit_cost)}</div>', unsafe_allow_html=True)
        c[2].markdown(f'<div style="color:#0f172a;font-size:13px;font-weight:700;padding:4px 0;text-align:right;">{fmt_money(e.total_cost)}</div>', unsafe_allow_html=True)
        subtotal += e.total_cost
    st.markdown('<div style="border-top:1px solid #e2e8f0;margin:6px 0 4px;"></div>', unsafe_allow_html=True)
    sf = st.columns([4, 2, 2])
    sf[0].markdown('<div style="color:#475569;font-size:12px;padding:4px 0;">Subtotal</div>', unsafe_allow_html=True)
    sf[2].markdown(f'<div style="color:#0f172a;font-size:14px;font-weight:700;padding:4px 0;text-align:right;">{fmt_money(subtotal)}</div>', unsafe_allow_html=True)


with tab_sheet:
    import pandas as pd
    from server.schemas import LineItemEntry as _LIE

    # ---- Voice-edit at the top of the spreadsheet tab ----
    st.markdown("### Edit with voice")
    st.caption(
        "Dictate any change — e.g. 'change blue chip from 6 to 7 yd³', 'add a "
        "second 9-ton excavator day', 'drop the buggy dumper'. The AI rewrites the "
        "line items below. Manual cell edits in the spreadsheet are also saved on Save changes."
    )
    qd_voice_key = f"qd_voice_{q.quote_id}"
    voice_text = st.text_area(
        "Voice edit", value=st.session_state.get(qd_voice_key, ""),
        height=110, label_visibility="collapsed", key=qd_voice_key,
        placeholder="Tap the iPhone keyboard mic and dictate the change you want.",
    )

    st.markdown("### Spreadsheet")
    st.caption(
        "Tap any cell to edit. Add a row at the bottom. Bucket order: "
        "Equipment → Materials → Trucking → Spoil → Labour."
    )

    if not q.line_items:
        st.info("No projects yet.")
    else:
        BUCKET_ORDER = [
            CostBucket.EQUIPMENT,
            CostBucket.MATERIALS,
            CostBucket.TRUCKING,
            CostBucket.SPOIL,
            CostBucket.LABOUR,
        ]
        BUCKET_LABELS = {
            CostBucket.EQUIPMENT: "Equipment",
            CostBucket.MATERIALS: "Materials",
            CostBucket.LABOUR: "Labour",
            CostBucket.TRUCKING: "Trucking",
            CostBucket.SPOIL: "Spoil",
        }
        BUCKET_BY_LABEL = {v: k for k, v in BUCKET_LABELS.items()}

        # Single-project quotes: one editor for the project's entries.
        # Multi-project: render one editor per project so each section has
        # context (project name + a clean spreadsheet for just that project).
        for li_idx, li in enumerate(q.line_items):
            if len(q.line_items) > 1:
                st.markdown(
                    f'<div style="color:#0f172a;font-size:14px;font-weight:600;'
                    f'margin-top:14px;margin-bottom:6px;">{li.label}</div>',
                    unsafe_allow_html=True,
                )

            rows = []
            for bucket in BUCKET_ORDER:
                for e in li.entries:
                    if e.bucket != bucket:
                        continue
                    rows.append({
                        "Bucket": BUCKET_LABELS[bucket],
                        "Description": e.description,
                        "Qty": float(e.quantity),
                        "Unit": e.unit,
                        "Unit Cost": float(e.unit_cost),
                        "Line Total": float(e.total_cost),
                    })

            if not rows:
                df = pd.DataFrame(columns=["Bucket", "Description", "Qty", "Unit", "Unit Cost", "Line Total"])
            else:
                df = pd.DataFrame(rows)

            edited = st.data_editor(
                df,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                column_config={
                    "Bucket": st.column_config.SelectboxColumn(
                        options=list(BUCKET_LABELS.values()),
                        required=True, width="small",
                    ),
                    "Description": st.column_config.TextColumn(width="medium"),
                    "Qty": st.column_config.NumberColumn(format="%.2f", width="small"),
                    "Unit": st.column_config.TextColumn(width="small"),
                    "Unit Cost": st.column_config.NumberColumn(format="$%.2f", width="small"),
                    "Line Total": st.column_config.NumberColumn(
                        format="$%.2f", disabled=True, width="small",
                        help="Auto: Qty × Unit Cost",
                    ),
                },
                key=f"qd_editor_{q.quote_id}_{li_idx}",
            )

            # Sync edits back to li.entries — rebuild from the edited DataFrame
            new_entries = []
            changed = False
            for _, row in edited.iterrows():
                bucket_str = str(row.get("Bucket") or "Materials")
                bucket = BUCKET_BY_LABEL.get(bucket_str, CostBucket.MATERIALS)
                desc = str(row.get("Description") or "").strip()
                if not desc:
                    continue
                try:
                    qty = float(row.get("Qty") or 0)
                except Exception:
                    qty = 0.0
                try:
                    cost = float(row.get("Unit Cost") or 0)
                except Exception:
                    cost = 0.0
                new_entries.append(_LIE(
                    bucket=bucket, description=desc, quantity=qty,
                    unit=str(row.get("Unit") or "each"), unit_cost=cost,
                    rental_insurance_eligible=(bucket == CostBucket.EQUIPMENT),
                ))

            # Detect actual change vs current entries (by serialized comparison)
            old_serial = [(e.bucket.value, e.description, e.quantity, e.unit, e.unit_cost) for e in li.entries]
            new_serial = [(e.bucket.value, e.description, e.quantity, e.unit, e.unit_cost) for e in new_entries]
            if old_serial != new_serial:
                # Apply edits to in-memory line items so bucket subtotals reflect
                # them — but DON'T persist until Save changes is clicked.
                li.entries = new_entries

        # ---- Save changes button — applies voice + spreadsheet edits ----
        st.markdown("&nbsp;", unsafe_allow_html=True)
        save_col1, save_col2 = st.columns([3, 1])
        with save_col2:
            if st.button("Save changes", type="primary", use_container_width=True,
                         key=f"qd_save_changes_{q.quote_id}",
                         help="Save spreadsheet edits to the quote. If you've dictated "
                              "voice changes above, the AI applies those first."):
                from tools.parser.notes_to_line_items import (
                    parse_notes_to_structure as _reparse,
                    is_configured as _parser_ok,
                    hydrate_to_line_items as _hydrate,
                )

                # Step 1 — apply voice via AI if non-empty
                voice_block = (st.session_state.get(qd_voice_key) or "").strip()
                if voice_block and _parser_ok():
                    # Build "current quote" context so AI can apply changes on top
                    quote_ctx_lines = []
                    for li2 in q.line_items:
                        quote_ctx_lines.append(f"\nProject: {li2.label}  ({li2.job_type})")
                        for b in CostBucket:
                            es = [e for e in li2.entries if e.bucket == b]
                            if not es: continue
                            quote_ctx_lines.append(f"  {b.value.upper()}:")
                            for e in es:
                                quote_ctx_lines.append(
                                    f"    - {e.description}: {e.quantity:g} {e.unit} "
                                    f"× ${e.unit_cost:.2f} = ${e.total_cost:.2f}"
                                )
                    context_str = "\n".join(quote_ctx_lines)
                    notes = (
                        "EXISTING QUOTE — current state of the line items:\n"
                        f"{context_str}\n\n"
                        "CHANGES THE CONTRACTOR WANTS APPLIED (dictated):\n"
                        f"{voice_block}\n\n"
                        "REGENERATION RULES:\n"
                        "1. Apply EVERY change item from the dictation.\n"
                        "2. KEEP every existing line the contractor didn't mention.\n"
                        "3. When a specific bucket is named, put new lines there.\n"
                        "4. Re-emit the FULL updated quote (not a diff)."
                    )
                    try:
                        with st.spinner("Applying voice changes (10-15s)..."):
                            parsed = _reparse(notes)
                        if parsed and parsed.projects:
                            q.line_items = _hydrate(parsed)
                    except Exception as exc:
                        st.error(f"Voice update failed: {exc}\n\nSpreadsheet edits will still be saved.")

                # Step 2 — persist (always, since spreadsheet edits are in-memory)
                save_quote(q)
                log_event(q.quote_id, "qd_save_changes", {
                    "had_voice": bool(voice_block),
                    "line_items": sum(len(li2.entries) for li2 in q.line_items),
                })
                # Clear the voice box so it doesn't re-apply on next save
                st.session_state[qd_voice_key] = ""
                st.success("Saved. Contract / PDF / project plan all reflect the new quote.")
                st.rerun()

        # Per-bucket subtotals — across the whole quote
        st.markdown("&nbsp;", unsafe_allow_html=True)
        st.markdown(
            '<div style="color:#64748b;font-size:11px;font-weight:700;'
            'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Bucket subtotals</div>',
            unsafe_allow_html=True,
        )
        sub_cols = st.columns(5)
        for col, bucket in zip(sub_cols, BUCKET_ORDER):
            col.metric(BUCKET_LABELS[bucket], fmt_money(q.bucket_total(bucket)))

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
            ("= Internal cost (Black Mountain Dirt Works total)", q.internal_cost),
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
            color = "#0f172a" if (is_total or is_subtotal) else "#334155"
            weight = "700" if is_total else ("600" if is_subtotal else "400")
            size = "16px" if is_total else "13px"
            border = ("border-top:1px solid #e2e8f0;margin-top:6px;padding-top:8px;"
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
        st.markdown(f"#### {li.label}  ·  {fmt_money(li.internal_cost)}")
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
        color = "#0f172a" if (is_total or is_subtotal) else "#334155"
        amt_color = "#0f172a" if (is_total or is_subtotal) else "#334155"
        if str(amount).startswith("-") or amount < 0:
            amt_color = "#22c55e"
        c = st.columns([4, 3, 2])
        c[0].markdown(f'<div style="color:{color};font-size:{size};font-weight:{weight};padding:4px 0;">{label}</div>', unsafe_allow_html=True)
        c[1].markdown(f'<div style="color:#475569;font-size:12px;padding:4px 0;">{math}</div>', unsafe_allow_html=True)
        c[2].markdown(f'<div style="color:{amt_color};font-size:{size};font-weight:{weight};padding:4px 0;text-align:right;">{fmt_money(amount)}</div>', unsafe_allow_html=True)


with tab_contract:
    st.markdown("### Contract")
    is_custom = q.contract_text is not None and q.contract_text.strip() != ""
    initial = q.contract_text if is_custom else draft_contract_text(q, COMPANY)

    badge = ("Custom (saved)" if is_custom else "Auto-drafted (not yet edited)")
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
            f'<div style="background:#ffffff;border:1px solid #e2e8f0;'
            f'border-left:3px solid #3b82f6;border-radius:8px;'
            f'padding:8px 14px;margin-bottom:6px;color:#334155;font-size:13px;">'
            f"<strong style='color:#0f172a;'>Step {i}.</strong> {step}</div>",
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



# ---- Save (explicit, for the cautious) ----------------------------------
# Most edits auto-save inline (Edit Quote tab, contract editor, pricing form,
# etc.) but having a big visible Save button gives peace of mind and acts as
# a belt-and-suspenders backstop.
st.markdown("---")
sv1, sv2 = st.columns([1, 4])
with sv1:
    if st.button("💾 Save changes", use_container_width=True, type="primary",
                 help="Force-save the current quote state to the database. "
                      "Most edits already auto-save — this is for peace of mind."):
        save_quote(q)
        log_event(q.quote_id, "manual_save", {"customer_total": q.customer_total})
        st.success("Saved.")
with sv2:
    st.caption(
        "Quote Detail edits auto-save as you make them. This button force-saves "
        "the current state — useful after a batch of changes or if you're not sure."
    )


# ---- Action bar ---------------------------------------------------------

st.markdown("---")
section_header("Actions")

def _collect_attachments(*candidate_paths) -> list:
    """Filter to existing files only. Used for both quote and contract sends."""
    return [Path(p) for p in candidate_paths if p and Path(p).exists()]


a1, a2, a3 = st.columns(3)
with a1:
    _email_ready = email_configured_method() != "none"
    if st.button("Send Quote", use_container_width=True, disabled=not _email_ready,
                 help=("Sends the quote PDF to the customer's email. "
                       + ("" if _email_ready else
                          "Disabled — email isn't configured. See Settings or workflows/setup_gmail.md."))):
        # Auto-render the PDF if WeasyPrint is available, then attach.
        quote_pdf_path = None
        if pdf_configured():
            quote_pdf_path, _ = render_quote_pdf(q, COMPANY)
        attachments = _collect_attachments(quote_pdf_path)
        result = send_email(
            to=q.customer.email or "",
            subject=f"Quote {q.quote_id} — {COMPANY.get('legal_name', 'Black Mountain Dirt Works')}",
            body_text=(
                f"Hi {q.customer.name},\n\n"
                f"Please find your quote ({q.quote_id}) attached. "
                f"Total: ${q.customer_total:,.2f} CAD (incl. tax).\n\n"
                f"Quote is valid for {COMPANY.get('quote_validity_days', 30)} days. "
                f"Reply to confirm or with any questions.\n\n"
                f"Thanks,\n{COMPANY.get('legal_name', 'Black Mountain Dirt Works')}"
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
    if st.button("Send Contract + Docs", use_container_width=True, disabled=not _email_ready,
                 help=("Sends the contract PDF (and insurance cert if present) to the customer. "
                       + ("" if _email_ready else
                          "Disabled — email isn't configured. See Settings or workflows/setup_gmail.md."))):
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
            subject=f"Contract {q.quote_id} — {COMPANY.get('legal_name', 'Black Mountain Dirt Works')}",
            body_text=(
                f"Hi {q.customer.name},\n\n"
                f"Please find the contract for {q.quote_id} attached, along with our "
                f"insurance certificate. You can accept by reply email or by making the "
                f"deposit payment.\n\n"
                f"Thanks,\n{COMPANY.get('legal_name', 'Black Mountain Dirt Works')}"
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

# Payment-info defaults for Invoice + Receipt PDFs. Defaults to 50/50 split.
# (Removed the explicit expander UI — was clutter. If a job ever needs
# different amounts, edit the PDF after generation or wire a per-quote field
# later.)
import datetime as _dt
deposit_amt = q.customer_total * 0.5
deposit_dt = _dt.date.today()
final_amt = q.customer_total - deposit_amt

p1, p2 = st.columns(2)
with p1:
    if st.button("📄 Generate Quote PDF", use_container_width=True, disabled=not pdf_configured(),
                 help="Render the customer-facing quote as a PDF."):
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
    if st.button("📑 Generate Contract PDF", use_container_width=True, disabled=not pdf_configured(),
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

p3, p4, p5 = st.columns(3)
with p3:
    if st.button("🧾 Generate Invoice PDF", use_container_width=True, disabled=not pdf_configured(),
                 help="Final invoice showing total, deposit received, and outstanding balance."):
        path, err = render_invoice_pdf(q, COMPANY,
                                       deposit_received=float(deposit_amt),
                                       deposit_received_date=deposit_dt)
        if err:
            st.warning(err)
        else:
            log_event(q.quote_id, "invoice_pdf_rendered",
                      {"path": str(path), "deposit_received": deposit_amt})
            st.success(f"Invoice PDF saved → {path}")
            with open(path, "rb") as f:
                st.download_button("⬇ Download invoice.pdf", data=f.read(),
                                   file_name=f"{q.quote_id}-invoice.pdf",
                                   mime="application/pdf", use_container_width=True)
with p4:
    if st.button("✅ Generate Deposit Receipt", use_container_width=True, disabled=not pdf_configured(),
                 help="Receipt for the 50% deposit you just received."):
        path, err = render_receipt_pdf(q, COMPANY,
                                       amount_received=float(deposit_amt),
                                       receipt_kind="deposit",
                                       received_date=deposit_dt)
        if err:
            st.warning(err)
        else:
            log_event(q.quote_id, "receipt_deposit_rendered",
                      {"path": str(path), "amount": deposit_amt})
            st.success(f"Deposit receipt saved → {path}")
            with open(path, "rb") as f:
                st.download_button("⬇ Download deposit-receipt.pdf", data=f.read(),
                                   file_name=f"{q.quote_id}-deposit-receipt.pdf",
                                   mime="application/pdf", use_container_width=True)
with p5:
    if st.button("✅ Generate Final Receipt", use_container_width=True, disabled=not pdf_configured(),
                 help="Receipt for the final payment + project complete."):
        path, err = render_receipt_pdf(q, COMPANY,
                                       amount_received=float(final_amt),
                                       receipt_kind="final",
                                       received_date=_dt.date.today())
        if err:
            st.warning(err)
        else:
            log_event(q.quote_id, "receipt_final_rendered",
                      {"path": str(path), "amount": final_amt})
            st.success(f"Final receipt saved → {path}")
            with open(path, "rb") as f:
                st.download_button("⬇ Download final-receipt.pdf", data=f.read(),
                                   file_name=f"{q.quote_id}-final-receipt.pdf",
                                   mime="application/pdf", use_container_width=True)


# ---- Internal-only PDFs (NOT customer-facing) ---------------------------
st.markdown(
    '<div style="color:#475569;font-size:11px;font-weight:700;'
    'text-transform:uppercase;letter-spacing:0.06em;margin-top:14px;margin-bottom:6px;">'
    "🔒 Internal-only — for your phone, not the customer</div>",
    unsafe_allow_html=True,
)
i1, i2, _ = st.columns(3)
with i1:
    if st.button("Material Takeoff PDF", use_container_width=True, disabled=not pdf_configured(),
                 help="Sourcing list — every material on this quote with SKUs and totals."):
        path, err = render_material_takeoff_pdf(q, COMPANY)
        if err:
            st.warning(err)
        else:
            log_event(q.quote_id, "material_takeoff_pdf_rendered", {"path": str(path)})
            st.success(f"Material takeoff saved → {path}")
            with open(path, "rb") as f:
                st.download_button("⬇ Download material-takeoff.pdf", data=f.read(),
                                   file_name=f"{q.quote_id}-material-takeoff.pdf",
                                   mime="application/pdf", use_container_width=True)
with i2:
    if st.button("🛠 Equipment List PDF", use_container_width=True, disabled=not pdf_configured(),
                 help="Mobilization checklist — every piece of equipment on this quote."):
        path, err = render_equipment_list_pdf(q, COMPANY)
        if err:
            st.warning(err)
        else:
            log_event(q.quote_id, "equipment_list_pdf_rendered", {"path": str(path)})
            st.success(f"Equipment list saved → {path}")
            with open(path, "rb") as f:
                st.download_button("⬇ Download equipment-list.pdf", data=f.read(),
                                   file_name=f"{q.quote_id}-equipment-list.pdf",
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
    state = "ready" if sheets_configured() else "not configured"
    st.caption(f"Google Sheets sync: {state}")
with sf2:
    method = email_configured_method()
    if method == "smtp":
        st.caption("Gmail send: ready (SMTP App Password — works on Render)")
    elif method == "oauth":
        st.caption("Gmail send: ready (OAuth — local Mac only)")
    else:
        st.caption("Gmail send: not configured (see workflows/setup_gmail.md)")
with sf3:
    state = "ready" if pdf_configured() else "WeasyPrint not installed"
    st.caption(f"PDF generator: {state}")
