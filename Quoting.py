"""BMDW Estimator — Streamlit entry point.

Single-page mobile-first capture flow. Everything lives on this page:
customer info, quick notes, AI parser, project + 5-bucket takeoff, running
pricing summary, save/review.
"""

from typing import Optional

import streamlit as st

from server.schemas import (
    CostBucket,
    Customer,
    Markup,
    Quote,
    Urgency,
)
from tools.calculator import JOB_TYPES
from tools.parser.checklist import JOB_TYPE_QUESTIONS, UNIVERSAL_QUESTIONS
from tools.parser.notes_to_line_items import (
    generate_clarifying_questions,
    generate_review_questions,
    hydrate_to_line_items,
    is_configured as parser_configured,
    parse_notes_to_structure,
    synthesize_brief,
)
from tools.shared import (
    apply_theme,
    fmt_money,
    render_project_takeoff,
    require_auth,
    section_header,
)
from tools.storage import (
    delete_quote,
    init_db,
    list_recent_quotes,
    load_quote,
    log_event,
    save_quote,
)

st.set_page_config(
    page_title="BMDW Estimator",
    page_icon="",
    layout="centered",
    initial_sidebar_state="collapsed",
)
apply_theme()
require_auth()
init_db()


# ---- Session state -------------------------------------------------------

defaults = {
    "draft_quick_notes": "",
    "draft_project_name": "",
    "draft_line_items": [],
    "draft_customer": Customer(name="", address=""),
    "draft_phone": "",
    "draft_site_address": "",
    "draft_urgency": Urgency.MODERATE.value,
    "draft_markup_pct": 40.0,
    "draft_discount_pct": 0.0,
    "draft_tax_pct": 12.0,
    "draft_insurance_pct": 16.0,
    "parsed_preview": None,
    "current_editing_id": None,
    "loaded_quote_id": None,
    "delete_confirm_id": None,
    # Phased capture flow
    "quote_phase": 1,                 # 1 = input, 2 = clarify, 3 = quote
    "clarifying_questions": [],       # Phase 2 list[str] from AI
    "clarifying_answers": "",         # Phase 2 voice answers from Michael
    "clarifier_error": None,          # last clarifier failure reason
    "review_questions": [],           # Phase 3 follow-up list[str] from AI (reviewing the generated quote)
    "review_answers": "",             # Phase 3 follow-up answers for regenerate
    "voice_edit_mode": False,         # True when editing an existing quote via voice
    # Phase 3 editable line items — hydrated once from parsed_preview, then
    # mutated by the data_editor. Lock-in uses these (not re-hydrated parsed_preview).
    "phase3_line_items": None,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


def _reset_draft_state(reset_id: bool = True) -> None:
    st.session_state.draft_quick_notes = ""
    st.session_state.draft_project_name = ""
    st.session_state.draft_line_items = []
    st.session_state.draft_customer = Customer(name="", address="")
    st.session_state.draft_phone = ""
    st.session_state.draft_site_address = ""
    st.session_state.draft_urgency = Urgency.MODERATE.value
    st.session_state.draft_markup_pct = 40.0
    st.session_state.draft_discount_pct = 0.0
    st.session_state.draft_tax_pct = 12.0
    st.session_state.draft_insurance_pct = 16.0
    st.session_state.parsed_preview = None
    st.session_state.quote_phase = 1
    st.session_state.clarifying_questions = []
    st.session_state.clarifying_answers = ""
    st.session_state.clarifier_error = None
    st.session_state.review_questions = []
    st.session_state.review_answers = ""
    st.session_state.voice_edit_mode = False
    st.session_state.phase3_line_items = None
    if reset_id:
        st.session_state.current_editing_id = None
        st.session_state.loaded_quote_id = None


def _format_existing_quote_as_context() -> str:
    """Compact text rendering of the current draft's line items, for AI context
    in voice-edit mode."""
    lines = []
    for li in st.session_state.draft_line_items:
        lines.append(f"\nProject: {li.label}  ({li.job_type})")
        for bucket in CostBucket:
            entries = [e for e in li.entries if e.bucket == bucket]
            if not entries:
                continue
            lines.append(f"  {bucket.value.upper()}:")
            for e in entries:
                cat_ref = f" [{e.catalogue_sku}]" if e.catalogue_sku else ""
                lines.append(
                    f"    - {e.description}: {e.quantity:g} {e.unit} × ${e.unit_cost:.2f} = ${e.total_cost:.2f}{cat_ref}"
                )
    return "\n".join(lines) if lines else "(no line items yet)"


def _combined_notes_for_parser() -> str:
    """Combine inputs into a single string for the parser.

    Two modes:
    - Normal (Phase 1 brief + Phase 2 answers)
    - Voice-edit (existing quote as context + change instructions)
    """
    answers = st.session_state.clarifying_answers.strip()

    if st.session_state.voice_edit_mode:
        existing = _format_existing_quote_as_context()
        return (
            "EXISTING QUOTE — current state of the line items:\n"
            f"{existing}\n\n"
            "CHANGES THE CONTRACTOR WANTS APPLIED (dictated):\n"
            f"{answers if answers else '(no changes dictated yet)'}\n\n"
            "REGENERATION RULES — read carefully:\n"
            "1. Apply EVERY change item from the contractor's dictation. Don't skip any.\n"
            "2. The contractor often combines multiple changes in one block — parse them "
            "out and apply each one. Look for verbs like add, remove, change, increase, "
            "decrease, swap, replace, drop, bump.\n"
            "3. KEEP every existing line the contractor didn't mention or ask to change.\n"
            "4. When the contractor names a specific bucket (trucking, labour, materials, "
            "spoil, equipment), put new lines in that bucket — don't reassign.\n"
            "5. Re-emit the FULL updated quote (not a diff). The system replaces the old "
            "quote wholesale with what you return."
        )

    base = st.session_state.draft_quick_notes.strip()
    questions = st.session_state.clarifying_questions
    review_qs = st.session_state.review_questions
    review_ans = st.session_state.review_answers.strip()

    parts = [f"INITIAL BRIEF:\n{base}"]
    if questions and answers:
        qblock = "\n".join(f"  {i + 1}. {q}" for i, q in enumerate(questions))
        parts.append(f"PHASE 2 QUESTIONS:\n{qblock}\n\nPHASE 2 ANSWERS:\n{answers}")
    elif answers:
        parts.append(f"CONTRACTOR'S ANSWERS:\n{answers}")
    if review_qs and review_ans:
        rqblock = "\n".join(f"  {i + 1}. {q}" for i, q in enumerate(review_qs))
        parts.append(f"PHASE 3 REVIEW QUESTIONS:\n{rqblock}\n\nPHASE 3 REVIEW ANSWERS:\n{review_ans}")
    return "\n\n".join(parts)


def _hydrate_customer_from_parsed(parsed) -> None:
    """Fill empty customer fields from the parser's voice extraction.
    User-typed values always win — AI only fills blanks."""
    if parsed is None:
        return
    if parsed.suggested_quote_label and not st.session_state.draft_project_name:
        st.session_state.draft_project_name = parsed.suggested_quote_label
    pc = getattr(parsed, "parsed_customer", None)
    if pc is None:
        return
    cust = st.session_state.draft_customer
    # User-typed values take precedence; AI only fills blanks
    new_name = (cust.name or "").strip() or (pc.name or "")
    new_addr = (cust.address or "").strip() or (pc.address or "")
    new_phone = cust.phone or pc.phone
    new_email = cust.email or pc.email
    st.session_state.draft_customer = Customer(
        name=new_name,
        address=new_addr,
        email=new_email or None,
        phone=new_phone or None,
    )
    if not (st.session_state.draft_site_address or "").strip() and pc.address:
        st.session_state.draft_site_address = pc.address
    if not (st.session_state.draft_phone or "").strip() and pc.phone:
        st.session_state.draft_phone = pc.phone
    if pc.urgency and pc.urgency in {u.value for u in Urgency}:
        st.session_state.draft_urgency = pc.urgency


def _parsed_quote_summary_for_review(parsed) -> str:
    """Compact text summary of a ParsedNotesOutput, for the Phase 3 review clarifier."""
    if parsed is None or not parsed.projects:
        return ""
    lines = []
    if parsed.summary:
        lines.append(f"Summary: {parsed.summary}")
    if parsed.warnings:
        lines.append("Warnings:")
        for w in parsed.warnings:
            lines.append(f"  - {w}")
    for p in parsed.projects:
        lines.append(f"\nProject: {p.label}  ({p.job_type})")
        for e in p.line_entries:
            cat_ref = f" [{e.catalogue_type}/{e.catalogue_key}]" if e.catalogue_key else ""
            lines.append(
                f"  - {e.bucket.value.upper()}: {e.description} — "
                f"{e.quantity:g} {e.unit}{cat_ref}"
            )
    return "\n".join(lines)


def _load_draft_into_session(quote_id: str) -> bool:
    """Load an existing quote into session state for editing. Returns True on success.
    Restores phase + clarifying-state fields too so user can resume mid-flow."""
    q = load_quote(quote_id)
    if q is None:
        return False
    st.session_state.draft_quick_notes = q.quick_notes or ""
    st.session_state.draft_project_name = q.name or ""
    st.session_state.draft_line_items = list(q.line_items)
    st.session_state.draft_customer = q.customer
    st.session_state.draft_phone = q.customer.phone or ""
    st.session_state.draft_site_address = q.site_address or q.customer.address or ""
    st.session_state.draft_urgency = q.urgency.value
    st.session_state.draft_markup_pct = q.markup.overall_pct
    st.session_state.draft_discount_pct = q.discount_pct
    st.session_state.draft_tax_pct = q.tax_pct
    st.session_state.draft_insurance_pct = q.rental_insurance_pct
    st.session_state.current_editing_id = quote_id
    st.session_state.loaded_quote_id = quote_id
    # Resume phase + Q&A state if it was saved on a previous autosave
    st.session_state.quote_phase = int(getattr(q, "quote_phase", 1) or 1)
    st.session_state.clarifying_questions = list(getattr(q, "clarifying_questions", []) or [])
    st.session_state.clarifying_answers = getattr(q, "clarifying_answers", "") or ""
    st.session_state.review_questions = list(getattr(q, "review_questions", []) or [])
    st.session_state.review_answers = getattr(q, "review_answers", "") or ""
    return True


def _autosave_draft() -> Optional[str]:
    """Save current session-state as a draft quote. Called at every phase
    transition so a refresh / app-switch never loses progress.

    Returns the saved quote_id, or None on failure (silent — never blocks).
    """
    try:
        # Skip autosave if we have literally nothing yet (no name, no notes,
        # no customer name) — avoid cluttering drafts list with blank rows.
        cust_name = (st.session_state.draft_customer.name or "").strip()
        notes = (st.session_state.draft_quick_notes or "").strip()
        proj_name = (st.session_state.draft_project_name or "").strip()
        if not (cust_name or notes or proj_name):
            return None

        draft = _draft_quote()
        saved_id = save_quote(draft)
        st.session_state.current_editing_id = saved_id
        st.session_state.loaded_quote_id = saved_id
        # Don't update query_params here — it could trigger a rerun. Just
        # stash so anything reading session_state can find the active quote.
        return saved_id
    except Exception:
        return None


# Honor ?quote_id=X — load that quote into the draft for editing.
incoming_quote_id = st.query_params.get("quote_id")
if incoming_quote_id and incoming_quote_id != st.session_state.loaded_quote_id:
    if _load_draft_into_session(incoming_quote_id):
        st.session_state.loaded_quote_id = incoming_quote_id

# Voice-edit handoff from Quote Detail page — jump straight to Phase 2 with
# this quote loaded as context, ready for the contractor to dictate changes.
if st.session_state.pop("_voice_edit_mode", False):
    st.session_state.voice_edit_mode = True
    st.session_state.quote_phase = 2
    st.session_state.clarifying_answers = ""
    st.session_state.clarifying_questions = []
    st.session_state.parsed_preview = None


def _draft_quote() -> Quote:
    """Build a Quote from current session-state. Uses current_editing_id if set,
    so saves overwrite the existing quote instead of creating a new one."""
    qid = st.session_state.current_editing_id or "DRAFT"
    return Quote(
        quote_id=qid,
        name=st.session_state.draft_project_name or None,
        customer=st.session_state.draft_customer,
        site_address=st.session_state.draft_site_address or None,
        urgency=Urgency(st.session_state.draft_urgency),
        line_items=st.session_state.draft_line_items,
        markup=Markup(overall_pct=st.session_state.draft_markup_pct),
        discount_pct=st.session_state.draft_discount_pct,
        tax_pct=st.session_state.draft_tax_pct,
        rental_insurance_pct=st.session_state.draft_insurance_pct,
        quick_notes=st.session_state.draft_quick_notes or None,
        # Phase state — captured so autosave can resume mid-flow
        quote_phase=int(st.session_state.quote_phase),
        clarifying_questions=list(st.session_state.clarifying_questions),
        clarifying_answers=st.session_state.clarifying_answers,
        review_questions=list(st.session_state.review_questions),
        review_answers=st.session_state.review_answers,
    )


# ---- Header --------------------------------------------------------------

st.markdown(
    '<div style="display:flex;align-items:center;justify-content:space-between;'
    'margin-bottom:8px">'
    '<div style="font-size:14px;color:#475569;letter-spacing:0.06em;'
    'text-transform:uppercase;">BMDW Estimator</div>'
    '<div style="font-size:11px;color:#64748b;">v0.5</div>'
    "</div>",
    unsafe_allow_html=True,
)


# ---- Persistence indicator (top of capture screen) ----------------------
# Compact one-line status — green = safe to save, red = data won't survive deploy.
from tools.storage.paths import db_path as _dbpath, is_persistent as _is_pers

_p_persistent = _is_pers()
_p_db = _dbpath()
_p_db_size = _p_db.stat().st_size if _p_db.exists() else 0

if _p_persistent:
    _p_color = "#22c55e"
    _p_icon = ""
    _p_label = "Persistent — safe to save"
else:
    _p_color = "#ef4444"
    _p_icon = ""
    _p_label = "EPHEMERAL — saves WILL be wiped on next deploy"

st.markdown(
    f'<div style="background:#f1f5f9;border:1px solid #e2e8f0;'
    f'border-left:4px solid {_p_color};border-radius:6px;'
    f'padding:6px 10px;margin-bottom:10px;color:#334155;font-size:11px;'
    f'display:flex;justify-content:space-between;align-items:center;">'
    f'<span><strong style="color:{_p_color};">{_p_icon} {_p_label}</strong></span>'
    f'<span style="color:#64748b;">DB {_p_db_size / 1024:,.1f} KB · '
    f'<a href="/Settings" target="_self" style="color:#3b82f6;text-decoration:none;">Backup →</a></span>'
    "</div>",
    unsafe_allow_html=True,
)


# ---- Editing-mode banner + drafts in progress ---------------------------

if st.session_state.current_editing_id:
    eb1, eb2 = st.columns([4, 1])
    with eb1:
        st.markdown(
            f'<div style="background:#ffffff;border:1px solid #e2e8f0;'
            f'border-left:4px solid #3b82f6;border-radius:8px;'
            f'padding:10px 14px;color:#334155;font-size:13px;margin-bottom:8px;">'
            f"Editing <strong>{st.session_state.current_editing_id}</strong> — "
            f"Save Draft will overwrite this quote.</div>",
            unsafe_allow_html=True,
        )
    with eb2:
        if st.button("Start fresh", use_container_width=True):
            _reset_draft_state(reset_id=True)
            st.query_params.clear()
            st.rerun()
else:
    # Show any in-progress drafts as a quick-edit picker
    drafts = [q for q in list_recent_quotes(limit=20) if q["status"] == "draft"]
    if drafts:
        with st.expander(f"{len(drafts)} draft{'s' if len(drafts) != 1 else ''} in progress — click to edit", expanded=False):
            for q in drafts[:10]:
                qid = q["quote_id"]
                pending_delete = st.session_state.delete_confirm_id == qid
                bcol_a, bcol_b, bcol_c = st.columns([4, 1, 1])
                with bcol_a:
                    st.markdown(
                        f'<div style="color:#334155;font-size:13px;padding:6px 0;">'
                        f"<strong style='color:#0f172a;'>{qid}</strong> — "
                        f"{q['customer_name']} · {q['updated_at'][:10]}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                with bcol_b:
                    if st.button("Edit", key=f"edit_draft_{qid}", use_container_width=True):
                        st.session_state.delete_confirm_id = None
                        st.query_params["quote_id"] = qid
                        st.rerun()
                with bcol_c:
                    if pending_delete:
                        if st.button("Confirm", key=f"confirm_del_{qid}", type="primary", use_container_width=True):
                            delete_quote(qid)
                            st.session_state.delete_confirm_id = None
                            if st.session_state.current_editing_id == qid:
                                _reset_draft_state(reset_id=True)
                                st.query_params.clear()
                            st.rerun()
                    else:
                        if st.button("Delete", key=f"del_draft_{qid}", use_container_width=True,
                                     help="Delete this draft permanently"):
                            st.session_state.delete_confirm_id = qid
                            st.rerun()
                if pending_delete:
                    cc1, cc2 = st.columns([5, 1])
                    with cc1:
                        st.caption(f"Delete {qid} permanently? Tap Confirm to delete or Cancel to keep.")
                    with cc2:
                        if st.button("Cancel", key=f"cancel_del_{qid}", use_container_width=True):
                            st.session_state.delete_confirm_id = None
                            st.rerun()


section_header("Customer")
c1, c2 = st.columns(2)
with c1:
    cust_name = st.text_input("Name", value=st.session_state.draft_customer.name,
                              placeholder="John Smith", key="cust_name_input")
with c2:
    cust_phone = st.text_input("Phone", value=st.session_state.draft_phone,
                               placeholder="(250) 555-1234", key="cust_phone_input")

c3, c4 = st.columns(2)
with c3:
    cust_email = st.text_input("Email", value=st.session_state.draft_customer.email or "",
                               placeholder="john@example.com", key="cust_email_input")
with c4:
    site_address = st.text_input("Job site address", value=st.session_state.draft_site_address,
                                 placeholder="1234 Smith Rd, Duncan, BC", key="site_addr_input")

st.session_state.draft_customer = Customer(
    name=cust_name,
    address=site_address,
    email=cust_email or None,
    phone=cust_phone or None,
)
st.session_state.draft_phone = cust_phone
st.session_state.draft_site_address = site_address

# ---- Phased capture flow (Phase 1 → 2 → 3) ------------------------------
# Phase 1: input voice details
# Phase 2: AI clarifying questions, you dictate answers
# Phase 3: generated quote preview, lock in
# Phases run only when starting fresh (not in edit mode)

is_editing = bool(st.session_state.current_editing_id)
phase = st.session_state.quote_phase

PHASE_LABELS = {1: "Input Details", 2: "Clarifying Questions", 3: "Generated Quote"}


def _phase_pill(current: int) -> None:
    bits = []
    for i in range(1, 4):
        active = i == current
        done = i < current
        bg = "#3b82f6" if active else ("#e2e8f0" if done else "#f1f5f9")
        color = "white" if active else ("#475569" if done else "#475569")
        weight = "700" if active else "500"
        marker = "●" if active else ("" if done else "○")
        bits.append(
            f'<span style="display:inline-block;background:{bg};color:{color};'
            f'font-size:11px;font-weight:{weight};padding:6px 12px;border-radius:14px;'
            f'margin-right:6px;letter-spacing:0.04em;">{marker} Phase {i} · {PHASE_LABELS[i]}</span>'
        )
    st.markdown(
        f'<div style="margin:4px 0 12px;">{" ".join(bits)}</div>',
        unsafe_allow_html=True,
    )


bucket_color = {
    CostBucket.LABOUR: "#3b82f6",
    CostBucket.MATERIALS: "#22c55e",
    CostBucket.EQUIPMENT: "#f59e0b",
    CostBucket.TRUCKING: "#8b5cf6",
    CostBucket.SPOIL: "#ef4444",
}


# Bind quick_notes for use in the bottom save bar regardless of phase.
quick_notes = st.session_state.draft_quick_notes


# Phases ALWAYS render when phase is set (1/2/3) — even when editing an existing
# draft. The phased flow is the primary capture path for both new and resumed quotes.
# voice_edit_mode is a special Phase-2 variant; included for safety.
if st.session_state.voice_edit_mode or phase in (1, 2, 3):
    if st.session_state.voice_edit_mode:
        st.markdown(
            f'<div style="background:#ffffff;border:1px solid #e2e8f0;'
            f'border-left:4px solid #f59e0b;border-radius:8px;'
            f'padding:10px 14px;color:#334155;font-size:13px;margin-bottom:8px;">'
            f"<strong>Voice-editing {st.session_state.current_editing_id}</strong> — "
            f"dictate what to change in Phase 2 below. Lock-in will overwrite this quote."
            f"</div>",
            unsafe_allow_html=True,
        )
    _phase_pill(phase)

    # ===== PHASE 1 — INPUT DETAILS =====
    if phase == 1:
        section_header("Phase 1 — Input Details")
        quick_notes = st.text_area(
            "Quick notes",
            value=st.session_state.draft_quick_notes,
            height=200,
            placeholder=(
                "Front section 30x40 dig down 6 inches.\n"
                "Retaining wall 20 ft long 6 ft high, lock blocks.\n"
                "Bark mulch ~67 yards on top.\n"
                "9-ton excavator, 4 days. Spoil offsite."
            ),
            label_visibility="collapsed",
            key="phase1_notes",
        )
        st.session_state.draft_quick_notes = quick_notes

        clarify_help = (
            "AI reads your brief and returns 3–7 specific questions you should answer "
            "before generating the quote. Focuses on price-sensitive unknowns "
            "(location/round-trip, missing dimensions, material choice, equipment access)."
        )
        if not parser_configured():
            clarify_help += "  (Set ANTHROPIC_API_KEY in .env to enable.)"

        if st.button("Ask me clarifying questions →", use_container_width=True,
                     type="primary", disabled=not quick_notes.strip(),
                     help=clarify_help):
            try:
                with st.spinner("Reading the brief and figuring out what I need to know..."):
                    result = generate_clarifying_questions(quick_notes)
            except Exception as exc:
                st.error(f"Clarifier crashed: {exc}\n\nYour notes are still here. Try again.")
                st.stop()

            if result.get("ok"):
                # Empty questions list is valid — go to Phase 2 with the empty-state
                # message; user can dictate any extras and hit Generate.
                st.session_state.clarifying_questions = result.get("questions", []) or []
                st.session_state.clarifier_error = None
                st.session_state.quote_phase = 2
                _autosave_draft()  # save before rerun so app-close survives
                st.success("Phase 2 ready — scroll down to answer.")
                st.rerun()
            else:
                reason = result.get("reason") or "Unknown failure."
                st.session_state.clarifier_error = reason
                st.error(
                    f"AI clarifier failed: {reason}\n\n"
                    "Your notes are still here. Common fixes:\n"
                    "• If the ANTHROPIC_API_KEY is missing or wrong, set it in Render Environment.\n"
                    "• If it's a network/timeout, wait 10 seconds and click again.\n"
                    "• If it keeps failing, dictate everything into Quick Notes and skip Phase 2 — "
                    "go straight to a manual project from the bottom."
                )

    # ===== PHASE 2 — CLARIFYING QUESTIONS (or VOICE-EDIT) =====
    elif phase == 2:
        if st.session_state.voice_edit_mode:
            section_header("Phase 2 — Edit existing quote")

            # Current quote summary (compact)
            with st.expander(f"Current quote — {len(st.session_state.draft_line_items)} project(s)",
                             expanded=True):
                for li in st.session_state.draft_line_items:
                    st.markdown(
                        f'<div style="color:#0f172a;font-size:13px;font-weight:600;'
                        f'margin-top:8px;margin-bottom:4px;">{li.label}</div>',
                        unsafe_allow_html=True,
                    )
                    for bucket in CostBucket:
                        entries = [e for e in li.entries if e.bucket == bucket]
                        if not entries:
                            continue
                        st.markdown(
                            f'<div style="color:{bucket_color[bucket]};font-size:10px;'
                            f'font-weight:700;text-transform:uppercase;letter-spacing:0.06em;'
                            f'margin-top:4px;">{bucket.value}</div>',
                            unsafe_allow_html=True,
                        )
                        for e in entries:
                            st.markdown(
                                f'<div style="color:#334155;font-size:12px;padding:1px 0;">'
                                f"• {e.description} — {e.quantity:g} {e.unit} × ${e.unit_cost:.2f} "
                                f'<span style="color:#475569;">= ${e.total_cost:.2f}</span></div>',
                                unsafe_allow_html=True,
                            )

            st.markdown("&nbsp;", unsafe_allow_html=True)
            section_header("What needs to change? (voice or type)")
            clarifying_answers = st.text_area(
                "Changes",
                value=st.session_state.clarifying_answers,
                height=180,
                placeholder=(
                    "Examples:\n"
                    "• Change wall length from 30 ft to 35 ft.\n"
                    "• Add a magnum stone option for the back section, 4 ft × 8 ft.\n"
                    "• Job is actually in Cumberland — switch supplier to Browns River.\n"
                    "• Remove the buggy dumper, customer doesn't need it.\n"
                    "• Bump fuel estimate to $400."
                ),
                label_visibility="collapsed",
                key="phase2_voice_edit",
            )
            st.session_state.clarifying_answers = clarifying_answers

            b1, b2 = st.columns(2)
            with b1:
                if st.button("← Cancel (back to Quote Detail)", use_container_width=True):
                    qid = st.session_state.current_editing_id
                    _reset_draft_state(reset_id=True)
                    st.session_state["_pending_quote_id"] = qid
                    st.query_params["quote_id"] = qid
                    st.switch_page("pages/4_Quote_Detail.py")
            with b2:
                if st.button("Generate Updated Quote →", use_container_width=True,
                             type="primary",
                             disabled=not (parser_configured() and clarifying_answers.strip()),
                             help="AI applies your changes to the existing quote and "
                                  "produces a full updated version."):
                    try:
                        with st.spinner("Applying changes (10-15s)..."):
                            result = parse_notes_to_structure(_combined_notes_for_parser())
                        st.session_state.parsed_preview = result
                        _hydrate_customer_from_parsed(result)
                        st.session_state.phase3_line_items = None  # force re-hydrate
                        st.session_state.quote_phase = 3
                        _autosave_draft()
                        st.rerun()
                    except Exception as exc:
                        st.error(
                            f"Update failed: {exc}\n\n"
                            "Your change instructions are still here — try Generate again, "
                            "or hit Cancel to back out without changes."
                        )
        else:
            # ---- Standard new-quote Phase 2 (clarifying questions from AI) ----
            section_header("Phase 2 — Clarifying Questions")
            questions = st.session_state.clarifying_questions

            if not questions:
                st.markdown(
                    '<div style="background:#ffffff;border:1px solid #e2e8f0;'
                    'border-left:4px solid #16a34a;border-radius:8px;'
                    'padding:12px 16px;margin-bottom:12px;color:#334155;font-size:13px;">'
                    "No clarifying questions — your brief was complete. "
                    "Add anything you forgot below, or skip to generation."
                    "</div>",
                    unsafe_allow_html=True,
                )
            else:
                for i, q in enumerate(questions, start=1):
                    st.markdown(
                        f'<div style="background:#ffffff;border:1px solid #e2e8f0;'
                        f'border-left:4px solid #3b82f6;border-radius:8px;'
                        f'padding:10px 14px;margin-bottom:6px;color:#334155;font-size:13px;">'
                        f'<strong style="color:#0f172a;">Q{i}.</strong> {q}</div>',
                        unsafe_allow_html=True,
                    )

            st.markdown("&nbsp;", unsafe_allow_html=True)
            section_header("Your answers (voice or type)")
            clarifying_answers = st.text_area(
                "Answers",
                value=st.session_state.clarifying_answers,
                height=180,
                placeholder=(
                    "Q1: Job is in Cobble Hill. Close pit, 1 hr round trip.\n"
                    "Q2: Spoil stays on site, no dump trip.\n"
                    "Q3: Wall is straight, no curves.\n"
                    "Q4: 4-day duration, fuel about $300."
                ),
                label_visibility="collapsed",
                key="phase2_answers",
            )
            st.session_state.clarifying_answers = clarifying_answers

            b1, b2 = st.columns(2)
            with b1:
                if st.button("← Back to input", use_container_width=True):
                    st.session_state.quote_phase = 1
                    st.rerun()
            with b2:
                if st.button("Generate Quote →", use_container_width=True, type="primary",
                             disabled=not parser_configured(),
                             help="AI uses your brief + answers to produce the line items, "
                                  "then asks any final review questions."):
                    try:
                        with st.spinner("Generating quote (this can take 10-15s for big jobs)..."):
                            result = parse_notes_to_structure(_combined_notes_for_parser())
                        st.session_state.parsed_preview = result
                        _hydrate_customer_from_parsed(result)
                        # Reset Phase 3 review state for the new generation
                        st.session_state.review_questions = []
                        st.session_state.review_answers = ""
                        # Kick off Phase 3 second-round clarifier in the background
                        if result and result.projects:
                            try:
                                with st.spinner("Reviewing the generated quote for follow-up questions..."):
                                    review = generate_review_questions(
                                        st.session_state.draft_quick_notes,
                                        st.session_state.clarifying_answers,
                                        _parsed_quote_summary_for_review(result),
                                    )
                                if review.get("ok"):
                                    st.session_state.review_questions = review["questions"]
                            except Exception:
                                # Non-fatal — Phase 3 still loads, just without review questions.
                                pass
                        st.session_state.quote_phase = 3
                        # AUTOSAVE — Phase 3 just generated, persist before rerun
                        # so the line items + review questions survive an app close.
                        _autosave_draft()
                        st.rerun()
                    except Exception as exc:
                        st.error(
                            f"Quote generation failed: {exc}\n\n"
                            "Your inputs are still here — try Generate again, or hit "
                            "Back to revise the answers."
                        )

    # ===== PHASE 3 — GENERATED QUOTE =====
    elif phase == 3:
        parsed = st.session_state.parsed_preview
        section_header("Phase 3 — Generated Quote")

        if parsed is None or not parsed.projects:
            st.error(
                "Generation failed or produced no projects. "
                + (parsed.warnings[0] if (parsed and parsed.warnings) else "")
            )
            if st.button("← Back to clarifying questions", use_container_width=True):
                st.session_state.quote_phase = 2
                st.rerun()
        else:
            if parsed.summary:
                st.markdown(f"**Summary:** {parsed.summary}")

            # Show customer info the AI pulled from the voice notes — so you
            # can spot if a name/phone got missed and edit on Quote Detail.
            cust = st.session_state.draft_customer
            cust_bits = []
            if cust.name: cust_bits.append(cust.name)
            if cust.phone: cust_bits.append(cust.phone)
            if cust.email: cust_bits.append(cust.email)
            if st.session_state.draft_site_address:
                cust_bits.append(st.session_state.draft_site_address)
            if st.session_state.draft_project_name:
                cust_bits.append(f"({st.session_state.draft_project_name})")
            if cust_bits:
                st.markdown(
                    f'<div style="background:#ffffff;border:1px solid #e2e8f0;'
                    f'border-left:4px solid #2563eb;border-radius:8px;'
                    f'padding:10px 14px;margin-bottom:10px;color:#334155;font-size:13px;">'
                    f'<strong style="color:#0f172a;">Customer:</strong> '
                    f'{" · ".join(cust_bits)}</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div style="background:#ffffff;border:1px solid #e2e8f0;'
                    'border-left:4px solid #d97706;border-radius:8px;'
                    'padding:10px 14px;margin-bottom:10px;color:#334155;font-size:13px;">'
                    "No customer info extracted from voice — fill it in on Quote Detail "
                    "after lock-in."
                    "</div>",
                    unsafe_allow_html=True,
                )

            for w in parsed.warnings:
                st.markdown(
                    f'<div style="background:#ffffff;border:1px solid #e2e8f0;'
                    f'border-left:4px solid #f59e0b;border-radius:8px;'
                    f'padding:10px 14px;margin-bottom:6px;color:#334155;font-size:13px;">'
                    f"{w}</div>",
                    unsafe_allow_html=True,
                )

            # Hydrate parsed_preview into editable JobLineItems once;
            # re-hydrate only when parsed_preview changes (regen sets the flag).
            import pandas as _pd
            from server.schemas import LineItemEntry as _LIE

            if st.session_state.phase3_line_items is None:
                st.session_state.phase3_line_items = hydrate_to_line_items(parsed)
            phase3_items = st.session_state.phase3_line_items

            BUCKET_ORDER_PHASE3 = [
                CostBucket.EQUIPMENT,
                CostBucket.MATERIALS,
                CostBucket.SPOIL,
                CostBucket.TRUCKING,
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

            for li_idx, li in enumerate(phase3_items):
                section_header(f"{li.label}")

                rows = []
                for bucket in BUCKET_ORDER_PHASE3:
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

                df = _pd.DataFrame(rows or [{
                    "Bucket": "Materials", "Description": "", "Qty": 0.0,
                    "Unit": "", "Unit Cost": 0.0, "Line Total": 0.0,
                }]).head(0 if not rows else None)
                if not rows:
                    df = _pd.DataFrame(columns=["Bucket", "Description", "Qty", "Unit", "Unit Cost", "Line Total"])

                edited_df = st.data_editor(
                    df,
                    use_container_width=True,
                    hide_index=True,
                    num_rows="dynamic",
                    column_config={
                        "Bucket": st.column_config.SelectboxColumn(
                            options=list(BUCKET_LABELS.values()),
                            required=True,
                            width="small",
                        ),
                        "Description": st.column_config.TextColumn(width="medium"),
                        "Qty": st.column_config.NumberColumn(format="%.2f", width="small"),
                        "Unit": st.column_config.TextColumn(width="small"),
                        "Unit Cost": st.column_config.NumberColumn(format="$%.2f", width="small"),
                        "Line Total": st.column_config.NumberColumn(
                            format="$%.2f", disabled=True, width="small",
                            help="Auto-computed: Qty × Unit Cost",
                        ),
                    },
                    key=f"phase3_editor_{li_idx}",
                )

                # Sync edits back to li.entries
                new_entries = []
                for _, row in edited_df.iterrows():
                    bucket_str = str(row.get("Bucket") or "Materials")
                    bucket = BUCKET_BY_LABEL.get(bucket_str, CostBucket.MATERIALS)
                    desc = str(row.get("Description") or "").strip()
                    if not desc:
                        continue  # skip blank rows
                    try:
                        qty = float(row.get("Qty") or 0)
                    except Exception:
                        qty = 0.0
                    try:
                        cost = float(row.get("Unit Cost") or 0)
                    except Exception:
                        cost = 0.0
                    new_entries.append(_LIE(
                        bucket=bucket,
                        description=desc,
                        quantity=qty,
                        unit=str(row.get("Unit") or "each"),
                        unit_cost=cost,
                        rental_insurance_eligible=(bucket == CostBucket.EQUIPMENT),
                    ))
                li.entries = new_entries

                # Per-bucket subtotals reflect edits
                sub_cols = st.columns(5)
                for col, bucket in zip(sub_cols, BUCKET_ORDER_PHASE3):
                    col.metric(BUCKET_LABELS[bucket], fmt_money(li.bucket_total(bucket)))

                st.markdown(
                    f'<div style="color:#475569;font-size:12px;margin-top:6px;'
                    f'margin-bottom:16px;text-align:right;">'
                    f"Project internal cost (pre-markup, pre-tax): "
                    f'<strong style="color:#0f172a;">{fmt_money(li.internal_cost)}</strong></div>',
                    unsafe_allow_html=True,
                )

            # Quote-wide totals using the edited line items
            from server.schemas import Quote as _Q, Markup as _M
            tmp_q = _Q(
                quote_id="PREVIEW",
                customer=st.session_state.draft_customer,
                line_items=phase3_items,
                markup=_M(overall_pct=st.session_state.draft_markup_pct),
                discount_pct=st.session_state.draft_discount_pct,
                tax_pct=st.session_state.draft_tax_pct,
                rental_insurance_pct=st.session_state.draft_insurance_pct,
            )
            st.markdown("&nbsp;", unsafe_allow_html=True)
            section_header("Quote totals (preview)")
            t1, t2, t3 = st.columns(3)
            t1.metric("Internal cost", fmt_money(tmp_q.internal_cost))
            t2.metric("Customer total", fmt_money(tmp_q.customer_total))
            t3.metric("Margin", f"{tmp_q.margin_pct}%")

            # Optional reference checklist (collapsible) — secondary to the AI's
            # targeted questions in Phase 2, but still useful as a final scan.
            detected_job_types = []
            seen_jt = set()
            for proj in parsed.projects:
                if proj.job_type not in seen_jt:
                    detected_job_types.append(proj.job_type)
                    seen_jt.add(proj.job_type)

            with st.expander("Final review checklist (optional reference)", expanded=False):
                st.markdown(
                    '<div style="color:#3b82f6;font-size:11px;font-weight:700;'
                    'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Universal</div>',
                    unsafe_allow_html=True,
                )
                for q in UNIVERSAL_QUESTIONS:
                    st.markdown(
                        f'<div style="color:#334155;font-size:13px;padding:3px 0;">• {q}</div>',
                        unsafe_allow_html=True,
                    )
                for jt in detected_job_types:
                    qs = JOB_TYPE_QUESTIONS.get(jt, [])
                    if not qs:
                        continue
                    label = jt.replace("_", " ").title()
                    st.markdown(
                        f'<div style="color:#22c55e;font-size:11px;font-weight:700;'
                        f'text-transform:uppercase;letter-spacing:0.06em;'
                        f'margin-top:12px;margin-bottom:4px;">{label}</div>',
                        unsafe_allow_html=True,
                    )
                    for q in qs:
                        st.markdown(
                            f'<div style="color:#334155;font-size:13px;padding:3px 0;">• {q}</div>',
                            unsafe_allow_html=True,
                        )

            # ---- Phase 3 ITERATION — running conversation about the spreadsheet ----
            # Anything Michael wants to change after seeing the line items goes here:
            # add/remove/modify materials, equipment, trucking, labour, etc.
            # AI's review questions (if any) are surfaced as suggestions above the
            # textarea — but the textarea is freeform and accepts ANY iteration.
            st.markdown("&nbsp;", unsafe_allow_html=True)
            section_header("What would you change?")
            review_qs = st.session_state.review_questions
            if review_qs:
                st.markdown(
                    '<div style="color:#475569;font-size:11px;font-weight:700;'
                    'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">'
                    "AI suggestions — things worth confirming</div>",
                    unsafe_allow_html=True,
                )
                for i, rq in enumerate(review_qs, start=1):
                    st.markdown(
                        f'<div style="background:#ffffff;border:1px solid #e2e8f0;'
                        f'border-left:4px solid #f59e0b;border-radius:8px;'
                        f'padding:10px 14px;margin-bottom:6px;color:#334155;font-size:13px;">'
                        f'<strong style="color:#0f172a;">{i}.</strong> {rq}</div>',
                        unsafe_allow_html=True,
                    )
                st.markdown("&nbsp;", unsafe_allow_html=True)

            review_answers = st.text_area(
                "Iteration",
                value=st.session_state.review_answers,
                height=160,
                placeholder=(
                    "e.g.\n"
                    "• Add another tandem load of base material — bigger pad than I thought.\n"
                    "• Drop the dump trailer, customer doesn't need it.\n"
                    "• Bump fuel up to $400.\n"
                    "• Change the wall length to 35 ft.\n"
                    "• Pit round-trip is actually 2 hours not 1."
                ),
                label_visibility="collapsed",
                key="phase3_review_answers",
            )
            st.session_state.review_answers = review_answers

            st.markdown("&nbsp;", unsafe_allow_html=True)
            b1, b2, b3 = st.columns(3)
            with b1:
                if st.button("← Revise (Phase 2)", use_container_width=True,
                             help="Go back to Phase 2 to update your earlier clarifying answers."):
                    st.session_state.quote_phase = 2
                    st.rerun()
            with b2:
                regen_disabled = not (parser_configured() and st.session_state.review_answers.strip())
                if st.button("Update quote with changes", use_container_width=True,
                             disabled=regen_disabled,
                             help="Re-run the AI with your iteration text — produces an "
                                  "updated spreadsheet. Stay in Phase 3 to keep iterating."):
                    try:
                        with st.spinner("Re-generating with review answers..."):
                            result = parse_notes_to_structure(_combined_notes_for_parser())
                        st.session_state.parsed_preview = result
                        _hydrate_customer_from_parsed(result)
                        st.session_state.phase3_line_items = None  # force re-hydrate
                        # Generate fresh review questions for this new pass
                        st.session_state.review_questions = []
                        st.session_state.review_answers = ""
                        if result and result.projects:
                            try:
                                review = generate_review_questions(
                                    st.session_state.draft_quick_notes,
                                    st.session_state.clarifying_answers,
                                    _parsed_quote_summary_for_review(result),
                                )
                                if review.get("ok"):
                                    st.session_state.review_questions = review["questions"]
                            except Exception:
                                pass
                        _autosave_draft()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Regenerate failed: {exc}")
            with b3:
                if st.button("Lock in & open Quote Detail", use_container_width=True,
                             type="primary",
                             help="Save this quote and open the Quote Detail for review."):
                    # Use the user's EDITED line items from the Phase 3 spreadsheet,
                    # not a fresh hydration of parsed_preview (which would lose edits).
                    new_items = (st.session_state.phase3_line_items
                                 if st.session_state.phase3_line_items is not None
                                 else hydrate_to_line_items(parsed))
                    st.session_state.draft_line_items = new_items
                    # Polish the raw voice notes into a clean 1-2 sentence brief
                    # so Description / Customers / Quote Detail show something
                    # readable instead of the raw dictation.
                    if parser_configured():
                        try:
                            polished = synthesize_brief(
                                st.session_state.draft_quick_notes,
                                st.session_state.clarifying_answers,
                                st.session_state.review_answers,
                                _parsed_quote_summary_for_review(parsed),
                            )
                            if polished:
                                st.session_state.draft_quick_notes = polished
                        except Exception:
                            pass  # keep raw notes on failure
                    q = _draft_quote()
                    saved_id = save_quote(q)
                    # Capture any custom (non-catalogue) line items into the
                    # user catalogue so they can be reused on future quotes.
                    try:
                        from tools.storage.user_catalogue import (
                            capture_quote_customs as _cap_customs,
                            static_catalogue_skus as _static_skus,
                        )
                        custom_count = _cap_customs(q.line_items, _static_skus())
                    except Exception:
                        custom_count = 0
                    log_event(saved_id,
                              "quote_voice_edited" if st.session_state.voice_edit_mode else "quote_locked_in",
                              {"line_items": len(q.line_items),
                               "had_clarifying_questions": len(st.session_state.clarifying_questions),
                               "had_review_questions": len(st.session_state.review_questions),
                               "custom_items_captured": custom_count,
                               "voice_edit": st.session_state.voice_edit_mode})
                    # Don't reset draft state — preserve in case lock-in fails or
                    # user navigates back. Explicit "Start fresh" still resets.
                    # st.query_params writes don't always flush before
                    # st.switch_page navigates. Stash in session_state too;
                    # Quote Detail picks up whichever is set.
                    st.session_state["_pending_quote_id"] = saved_id
                    st.query_params["quote_id"] = saved_id
                    st.switch_page("pages/4_Quote_Detail.py")


# ---- Projects on the draft (with full takeoff inline) -------------------

if st.session_state.draft_line_items:
    section_header("Projects on This Quote")

    job_type_labels = {j["key"]: j["label"] for j in JOB_TYPES}
    edited = False

    for li_idx, li in enumerate(st.session_state.draft_line_items):
        proj_label = (
            f"{li.label}  ·  {job_type_labels.get(li.job_type, li.job_type)}  ·  "
            f"{fmt_money(li.internal_cost) if li.entries else '—'}"
        )
        with st.expander(proj_label, expanded=(li_idx == len(st.session_state.draft_line_items) - 1)):
            project_edited = render_project_takeoff(li, key_prefix=f"draft_{li_idx}")
            if project_edited:
                edited = True

            # Project actions at bottom
            st.markdown("&nbsp;", unsafe_allow_html=True)
            pa1, pa2 = st.columns([3, 1])
            with pa1:
                new_label = st.text_input(
                    "Rename project", value=li.label, key=f"rename_draft_{li_idx}",
                    label_visibility="collapsed",
                )
                if new_label != li.label:
                    li.label = new_label
                    edited = True
            with pa2:
                if st.button("Remove", key=f"del_draft_{li_idx}", use_container_width=True):
                    st.session_state.draft_line_items.pop(li_idx)
                    st.rerun()

    if edited:
        st.rerun()


# ---- Running pricing summary --------------------------------------------

if st.session_state.draft_line_items:
    q_preview = _draft_quote()
    section_header("Running Pricing")

    sm1, sm2, sm3 = st.columns(3)
    sm1.metric("CUSTOMER TOTAL", fmt_money(q_preview.customer_total))
    sm2.metric("Internal Cost", fmt_money(q_preview.internal_cost))
    sm3.metric("Margin", f"{q_preview.margin_pct}%")

    # Pricing controls (inline, so you can adjust during build)
    with st.expander("Adjust markup / discount / tax / insurance"):
        ec1, ec2 = st.columns(2)
        with ec1:
            new_markup = st.number_input(
                "Markup %", min_value=0.0, max_value=300.0, step=1.0,
                value=float(st.session_state.draft_markup_pct),
            )
            new_discount = st.number_input(
                "Discount %", min_value=0.0, max_value=100.0, step=0.5,
                value=float(st.session_state.draft_discount_pct),
                help="Customer never sees this — they only see the final total.",
            )
        with ec2:
            new_tax = st.number_input(
                "Tax %", min_value=0.0, max_value=30.0, step=0.5,
                value=float(st.session_state.draft_tax_pct),
            )
            new_insurance = st.number_input(
                "Rental insurance %", min_value=0.0, max_value=50.0, step=1.0,
                value=float(st.session_state.draft_insurance_pct),
                help="Applied only to insurance-eligible equipment entries (not trucks).",
            )
        st.session_state.draft_markup_pct = new_markup
        st.session_state.draft_discount_pct = new_discount
        st.session_state.draft_tax_pct = new_tax
        st.session_state.draft_insurance_pct = new_insurance

    st.markdown(
        f'<div style="color:#475569;font-size:13px;margin-top:8px;">'
        f"Cost {fmt_money(q_preview.internal_cost)} → "
        f"+ markup ({q_preview.markup.overall_pct:g}%) → "
        f"− discount ({q_preview.discount_pct:g}%) → "
        f"+ tax ({q_preview.tax_pct:g}%) → "
        f"<strong style='color:#0f172a;'>{fmt_money(q_preview.customer_total)}</strong>"
        f"</div>",
        unsafe_allow_html=True,
    )


# ---- Clear page (always available, bottom of page) ---------------------
st.markdown("---")
cp1, cp2, _ = st.columns([1, 2, 2])
with cp1:
    if st.session_state.get("_clear_confirm"):
        if st.button("Confirm clear", type="primary", use_container_width=True):
            _reset_draft_state(reset_id=True)
            st.session_state["_clear_confirm"] = False
            st.query_params.clear()
            st.rerun()
    else:
        if st.button("Clear page", use_container_width=True):
            st.session_state["_clear_confirm"] = True
            st.rerun()
with cp2:
    if st.session_state.get("_clear_confirm"):
        if st.button("Cancel", use_container_width=True):
            st.session_state["_clear_confirm"] = False
            st.rerun()
