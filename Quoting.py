"""BMDW Estimator — Streamlit entry point.

Single-page mobile-first capture flow. Everything lives on this page:
customer info, quick notes, AI parser, project + 5-bucket takeoff, running
pricing summary, save/review.
"""

import streamlit as st

from server.schemas import (
    CostBucket,
    Customer,
    Markup,
    Quote,
    Urgency,
)
from tools.calculator import JOB_TYPES, create_empty_project, get_job_type
from tools.parser.checklist import JOB_TYPE_QUESTIONS, UNIVERSAL_QUESTIONS
from tools.parser.notes_to_line_items import (
    generate_clarifying_questions,
    generate_review_questions,
    hydrate_to_line_items,
    is_configured as parser_configured,
    parse_notes_to_structure,
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
    page_icon="◆",
    layout="centered",
    initial_sidebar_state="collapsed",
)
apply_theme()
require_auth()
init_db()


# ---- Session state -------------------------------------------------------

defaults = {
    "draft_quick_notes": "",
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
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


def _reset_draft_state(reset_id: bool = True) -> None:
    st.session_state.draft_quick_notes = ""
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
            "Return the FULL UPDATED quote with the requested changes applied. "
            "Keep every existing line that the contractor didn't ask to change. "
            "Modify lines as instructed. Add new lines if requested. Remove lines "
            "if the contractor explicitly asks. Re-emit the entire quote so we can "
            "replace it cleanly."
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
    """Load an existing quote into session state for editing. Returns True on success."""
    q = load_quote(quote_id)
    if q is None:
        return False
    st.session_state.draft_quick_notes = q.quick_notes or ""
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
    return True


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
        customer=st.session_state.draft_customer,
        site_address=st.session_state.draft_site_address or None,
        urgency=Urgency(st.session_state.draft_urgency),
        line_items=st.session_state.draft_line_items,
        markup=Markup(overall_pct=st.session_state.draft_markup_pct),
        discount_pct=st.session_state.draft_discount_pct,
        tax_pct=st.session_state.draft_tax_pct,
        rental_insurance_pct=st.session_state.draft_insurance_pct,
        quick_notes=st.session_state.draft_quick_notes or None,
    )


# ---- Header --------------------------------------------------------------

st.markdown(
    '<div style="display:flex;align-items:center;justify-content:space-between;'
    'margin-bottom:8px">'
    '<div style="font-size:14px;color:#94a3b8;letter-spacing:0.06em;'
    'text-transform:uppercase;">◆ BMDW Estimator</div>'
    '<div style="font-size:11px;color:#64748b;">v0.5</div>'
    "</div>",
    unsafe_allow_html=True,
)


# ---- Editing-mode banner + drafts in progress ---------------------------

if st.session_state.current_editing_id:
    eb1, eb2 = st.columns([4, 1])
    with eb1:
        st.markdown(
            f'<div style="background:#111827;border:1px solid #1e293b;'
            f'border-left:4px solid #3b82f6;border-radius:8px;'
            f'padding:10px 14px;color:#cbd5e1;font-size:13px;margin-bottom:8px;">'
            f"✏ Editing <strong>{st.session_state.current_editing_id}</strong> — "
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
        with st.expander(f"📝 {len(drafts)} draft{'s' if len(drafts) != 1 else ''} in progress — click to edit", expanded=False):
            for q in drafts[:10]:
                qid = q["quote_id"]
                pending_delete = st.session_state.delete_confirm_id == qid
                bcol_a, bcol_b, bcol_c = st.columns([4, 1, 1])
                with bcol_a:
                    st.markdown(
                        f'<div style="color:#cbd5e1;font-size:13px;padding:6px 0;">'
                        f"<strong style='color:#f1f5f9;'>{qid}</strong> — "
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
                        if st.button("🗑", key=f"del_draft_{qid}", use_container_width=True,
                                     help="Delete this draft permanently"):
                            st.session_state.delete_confirm_id = qid
                            st.rerun()
                if pending_delete:
                    cc1, cc2 = st.columns([5, 1])
                    with cc1:
                        st.caption(f"⚠ Delete {qid} permanently? Tap Confirm to delete or Cancel to keep.")
                    with cc2:
                        if st.button("Cancel", key=f"cancel_del_{qid}", use_container_width=True):
                            st.session_state.delete_confirm_id = None
                            st.rerun()


# ---- Customer ------------------------------------------------------------

section_header("Customer")

c1, c2 = st.columns(2)
with c1:
    cust_name = st.text_input("Name", value=st.session_state.draft_customer.name, placeholder="John Smith")
with c2:
    cust_phone = st.text_input("Phone", value=st.session_state.draft_phone, placeholder="(250) 555-1234")

c3, c4 = st.columns(2)
with c3:
    cust_email = st.text_input("Email", value=st.session_state.draft_customer.email or "", placeholder="john@example.com")
with c4:
    urgency_options = [u.value for u in Urgency]
    urgency_idx = urgency_options.index(st.session_state.draft_urgency)
    urgency = st.selectbox(
        "Urgency", urgency_options, index=urgency_idx,
        format_func=lambda x: {"low": "Low — flexible",
                               "moderate": "Moderate — within a month",
                               "high": "High — ASAP"}[x],
    )

site_address = st.text_input(
    "Job site address",
    value=st.session_state.draft_site_address,
    placeholder="1234 Smith Rd, Duncan, BC",
)

st.session_state.draft_customer = Customer(
    name=cust_name,
    address=site_address,
    email=cust_email or None,
    phone=cust_phone or None,
)
st.session_state.draft_phone = cust_phone
st.session_state.draft_site_address = site_address
st.session_state.draft_urgency = urgency


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
        bg = "#3b82f6" if active else ("#1e293b" if done else "#0d1321")
        color = "white" if active else ("#94a3b8" if done else "#475569")
        weight = "700" if active else "500"
        marker = "●" if active else ("✓" if done else "○")
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


# Phases run for new quotes (not editing) OR when editing via voice (regenerate flow).
if (not is_editing) or st.session_state.voice_edit_mode:
    if st.session_state.voice_edit_mode:
        st.markdown(
            f'<div style="background:#111827;border:1px solid #1e293b;'
            f'border-left:4px solid #f59e0b;border-radius:8px;'
            f'padding:10px 14px;color:#cbd5e1;font-size:13px;margin-bottom:8px;">'
            f"🎤 <strong>Voice-editing {st.session_state.current_editing_id}</strong> — "
            f"dictate what to change in Phase 2 below. Lock-in will overwrite this quote."
            f"</div>",
            unsafe_allow_html=True,
        )
    _phase_pill(phase)

    # ===== PHASE 1 — INPUT DETAILS =====
    if phase == 1:
        section_header("Phase 1 — Input Details")
        st.markdown(
            '<div style="color:#64748b;font-size:12px;margin-bottom:6px;">'
            "Dictate everything you know about the job — projects, dimensions, materials, "
            "site notes. Use the iPhone keyboard 🎤 button. The AI will read this and "
            "ask you targeted clarifying questions in Phase 2."
            "</div>",
            unsafe_allow_html=True,
        )
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

        if st.button("Ask me clarifying questions  →", use_container_width=True,
                     type="primary", disabled=not quick_notes.strip(),
                     help=clarify_help):
            with st.spinner("Reading the brief and figuring out what I need to know..."):
                result = generate_clarifying_questions(quick_notes)
            if result["ok"]:
                st.session_state.clarifying_questions = result["questions"]
                st.session_state.clarifier_error = None
                st.session_state.quote_phase = 2
                st.rerun()
            else:
                st.session_state.clarifier_error = result.get("reason") or "Unknown failure."
                st.error(st.session_state.clarifier_error)

    # ===== PHASE 2 — CLARIFYING QUESTIONS (or VOICE-EDIT) =====
    elif phase == 2:
        if st.session_state.voice_edit_mode:
            # ---- Voice-edit variant: show the current quote as reference,
            # then a single textarea for change instructions. ----
            section_header("Phase 2 — Edit existing quote")
            st.markdown(
                '<div style="color:#64748b;font-size:12px;margin-bottom:10px;">'
                "Below is the current quote. Dictate the changes you want — add lines, "
                "modify quantities or descriptions, change the location/supplier, swap a "
                "material, remove something, etc. Then hit <strong>Generate Updated Quote</strong>."
                "</div>",
                unsafe_allow_html=True,
            )

            # Current quote summary (compact)
            with st.expander(f"📋 Current quote — {len(st.session_state.draft_line_items)} project(s)",
                             expanded=True):
                for li in st.session_state.draft_line_items:
                    st.markdown(
                        f'<div style="color:#f1f5f9;font-size:13px;font-weight:600;'
                        f'margin-top:8px;margin-bottom:4px;">◆ {li.label}</div>',
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
                                f'<div style="color:#cbd5e1;font-size:12px;padding:1px 0;">'
                                f"• {e.description} — {e.quantity:g} {e.unit} × ${e.unit_cost:.2f} "
                                f'<span style="color:#94a3b8;">= ${e.total_cost:.2f}</span></div>',
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
                if st.button("Generate Updated Quote  →", use_container_width=True,
                             type="primary",
                             disabled=not (parser_configured() and clarifying_answers.strip()),
                             help="AI applies your changes to the existing quote and "
                                  "produces a full updated version."):
                    try:
                        with st.spinner("Applying changes (10-15s)..."):
                            result = parse_notes_to_structure(_combined_notes_for_parser())
                        st.session_state.parsed_preview = result
                        st.session_state.quote_phase = 3
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
                    '<div style="background:#111827;border:1px solid #1e293b;'
                    'border-left:4px solid #22c55e;border-radius:8px;'
                    'padding:12px 16px;margin-bottom:12px;color:#cbd5e1;font-size:13px;">'
                    "✓ The AI didn't have any clarifying questions — your brief was complete enough. "
                    "You can still add anything you forgot below, or skip straight to generation."
                    "</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div style="color:#64748b;font-size:12px;margin-bottom:10px;">'
                    "Read the questions, then dictate your answers below — one big block is fine, "
                    "the AI parses what you say. Cover the questions in any order. "
                    "Reminder on round-trip times: close pit ≈ 1 hr · Campbell River ≈ 2 hr · Tofino ≈ 8 hr."
                    "</div>",
                    unsafe_allow_html=True,
                )
                for i, q in enumerate(questions, start=1):
                    st.markdown(
                        f'<div style="background:#111827;border:1px solid #1e293b;'
                        f'border-left:4px solid #3b82f6;border-radius:8px;'
                        f'padding:10px 14px;margin-bottom:6px;color:#cbd5e1;font-size:13px;">'
                        f'<strong style="color:#f1f5f9;">Q{i}.</strong> {q}</div>',
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
                if st.button("Generate Quote  →", use_container_width=True, type="primary",
                             disabled=not parser_configured(),
                             help="AI uses your brief + answers to produce the line items, "
                                  "then asks any final review questions."):
                    try:
                        with st.spinner("Generating quote (this can take 10-15s for big jobs)..."):
                            result = parse_notes_to_structure(_combined_notes_for_parser())
                        st.session_state.parsed_preview = result
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

            for w in parsed.warnings:
                st.markdown(
                    f'<div style="background:#111827;border:1px solid #1e293b;'
                    f'border-left:4px solid #f59e0b;border-radius:8px;'
                    f'padding:10px 14px;margin-bottom:6px;color:#cbd5e1;font-size:13px;">'
                    f"⚠ {w}</div>",
                    unsafe_allow_html=True,
                )

            for project in parsed.projects:
                with st.expander(
                    f"◆ {project.label}  ·  {project.job_type.replace('_', ' ').title()}",
                    expanded=True,
                ):
                    for bucket in CostBucket:
                        entries = [e for e in project.line_entries if e.bucket == bucket]
                        if not entries:
                            continue
                        st.markdown(
                            f'<div style="color:{bucket_color[bucket]};font-size:11px;'
                            f'font-weight:700;text-transform:uppercase;'
                            f'letter-spacing:0.06em;margin:8px 0 4px;">{bucket.value}</div>',
                            unsafe_allow_html=True,
                        )
                        for e in entries:
                            flag = " ⚠ catalogue" if e.needs_catalogue_add else ""
                            cat_ref = f' · `{e.catalogue_key}`' if e.catalogue_key else ""
                            st.markdown(
                                f"- {e.description} — **{e.quantity:g} {e.unit}**{cat_ref}{flag}"
                            )

            # Optional reference checklist (collapsible) — secondary to the AI's
            # targeted questions in Phase 2, but still useful as a final scan.
            detected_job_types = []
            seen_jt = set()
            for proj in parsed.projects:
                if proj.job_type not in seen_jt:
                    detected_job_types.append(proj.job_type)
                    seen_jt.add(proj.job_type)

            with st.expander("📋 Final review checklist (optional reference)", expanded=False):
                st.markdown(
                    '<div style="color:#3b82f6;font-size:11px;font-weight:700;'
                    'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Universal</div>',
                    unsafe_allow_html=True,
                )
                for q in UNIVERSAL_QUESTIONS:
                    st.markdown(
                        f'<div style="color:#cbd5e1;font-size:13px;padding:3px 0;">• {q}</div>',
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
                            f'<div style="color:#cbd5e1;font-size:13px;padding:3px 0;">• {q}</div>',
                            unsafe_allow_html=True,
                        )

            # ---- Phase 3 second-round review questions (redundancy pass) ----
            review_qs = st.session_state.review_questions
            if review_qs:
                st.markdown("&nbsp;", unsafe_allow_html=True)
                section_header("Final review — anything to refine?")
                st.markdown(
                    '<div style="color:#64748b;font-size:12px;margin-bottom:10px;">'
                    "AI looked at the generated quote and flagged these final confirmations. "
                    "Answer any that need correcting, then hit Regenerate. Or skip and Lock in."
                    "</div>",
                    unsafe_allow_html=True,
                )
                for i, rq in enumerate(review_qs, start=1):
                    st.markdown(
                        f'<div style="background:#111827;border:1px solid #1e293b;'
                        f'border-left:4px solid #f59e0b;border-radius:8px;'
                        f'padding:10px 14px;margin-bottom:6px;color:#cbd5e1;font-size:13px;">'
                        f'<strong style="color:#f1f5f9;">R{i}.</strong> {rq}</div>',
                        unsafe_allow_html=True,
                    )
                review_answers = st.text_area(
                    "Review answers (voice or type)",
                    value=st.session_state.review_answers,
                    height=140,
                    placeholder="e.g. R1: pit round trip is actually 2 hours, not 1. R2: yes use Browns River.",
                    label_visibility="collapsed",
                    key="phase3_review_answers",
                )
                st.session_state.review_answers = review_answers

            st.markdown("&nbsp;", unsafe_allow_html=True)
            b1, b2, b3 = st.columns(3)
            with b1:
                if st.button("← Revise (Phase 2)", use_container_width=True,
                             help="Go back to Phase 2 to update your answers, then regenerate."):
                    st.session_state.quote_phase = 2
                    st.rerun()
            with b2:
                regen_disabled = not (parser_configured() and st.session_state.review_answers.strip())
                if st.button("🔄 Regenerate with review", use_container_width=True,
                             disabled=regen_disabled,
                             help="Re-run the parser with your review answers added to the input."):
                    try:
                        with st.spinner("Re-generating with review answers..."):
                            result = parse_notes_to_structure(_combined_notes_for_parser())
                        st.session_state.parsed_preview = result
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
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Regenerate failed: {exc}")
            with b3:
                if st.button("✓ Lock in & open Quote Detail", use_container_width=True,
                             type="primary",
                             help="Save this quote and open the Quote Detail for review."):
                    new_items = hydrate_to_line_items(parsed)
                    st.session_state.draft_line_items = new_items
                    q = _draft_quote()
                    saved_id = save_quote(q)
                    log_event(saved_id,
                              "quote_voice_edited" if st.session_state.voice_edit_mode else "quote_locked_in",
                              {"line_items": len(q.line_items),
                               "had_clarifying_questions": len(st.session_state.clarifying_questions),
                               "had_review_questions": len(st.session_state.review_questions),
                               "voice_edit": st.session_state.voice_edit_mode})
                    # Don't reset draft state — preserve in case lock-in fails or
                    # user navigates back. Explicit "Start fresh" still resets.
                    # st.query_params writes don't always flush before
                    # st.switch_page navigates. Stash in session_state too;
                    # Quote Detail picks up whichever is set.
                    st.session_state["_pending_quote_id"] = saved_id
                    st.query_params["quote_id"] = saved_id
                    st.switch_page("pages/4_Quote_Detail.py")


# ---- Manual / fallback path: add an empty project -----------------------
# Only show this when:
#   - editing an existing draft (need to add more projects manually), OR
#   - in Phase 1 with no projects yet (giving the manual escape hatch)

show_manual_add = is_editing or (phase == 1 and not st.session_state.draft_line_items)

if show_manual_add:
    st.markdown("---")
    n_projects = len(st.session_state.draft_line_items)
    if is_editing:
        section_header("Add another project (manual)")
        st.caption(
            f"This quote has {n_projects} project{'s' if n_projects != 1 else ''}. "
            "Add another by picking a job type — fills the 5 buckets directly, no AI."
        )
    else:
        section_header("Or — skip the AI and add a project manually")
        st.caption("If you already know exactly what you want (no AI needed), pick a job type.")

    job_type_keys = [j["key"] for j in JOB_TYPES]
    mc1, mc2, mc3 = st.columns([2, 3, 2])
    with mc1:
        selected_key = st.selectbox(
            "Job type", job_type_keys,
            format_func=lambda k: get_job_type(k)["label"],
            label_visibility="collapsed",
        )
    with mc2:
        custom_label = st.text_input(
            "Optional label",
            placeholder=f"e.g. {get_job_type(selected_key)['label']} — back yard",
            label_visibility="collapsed",
        )
    with mc3:
        btn_label = "+ Add another" if n_projects > 0 else "+ Add project"
        if st.button(btn_label, use_container_width=True):
            li = create_empty_project(selected_key, custom_label or None)
            st.session_state.draft_line_items.append(li)
            st.rerun()


# ---- Projects on the draft (with full takeoff inline) -------------------

if st.session_state.draft_line_items:
    section_header("Projects on This Quote")

    job_type_labels = {j["key"]: j["label"] for j in JOB_TYPES}
    edited = False

    for li_idx, li in enumerate(st.session_state.draft_line_items):
        proj_label = (
            f"◆ {li.label}  ·  {job_type_labels.get(li.job_type, li.job_type)}  ·  "
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
                if st.button("🗑 Remove", key=f"del_draft_{li_idx}", use_container_width=True):
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
        f'<div style="color:#94a3b8;font-size:13px;margin-top:8px;">'
        f"Cost {fmt_money(q_preview.internal_cost)} → "
        f"+ markup ({q_preview.markup.overall_pct:g}%) → "
        f"− discount ({q_preview.discount_pct:g}%) → "
        f"+ tax ({q_preview.tax_pct:g}%) → "
        f"<strong style='color:#f1f5f9;'>{fmt_money(q_preview.customer_total)}</strong>"
        f"</div>",
        unsafe_allow_html=True,
    )


# ---- Bottom action bar ---------------------------------------------------

st.markdown("&nbsp;", unsafe_allow_html=True)

ready_to_save = bool(cust_name and (st.session_state.draft_quick_notes.strip() or st.session_state.draft_line_items))
ready_to_review = bool(st.session_state.draft_line_items and cust_name and site_address)

is_editing = bool(st.session_state.current_editing_id)
save_label = "Update Draft" if is_editing else "Save Draft"

a1, a2 = st.columns(2)
with a1:
    if st.button(save_label, use_container_width=True, disabled=not ready_to_save,
                 help="Save the draft so it's persisted. Stays on this page."):
        draft = _draft_quote()
        saved_id = save_quote(draft)
        st.session_state.current_editing_id = saved_id
        st.session_state.loaded_quote_id = saved_id
        st.query_params["quote_id"] = saved_id
        log_event(saved_id, "draft_updated" if is_editing else "draft_saved", {
            "line_items": len(draft.line_items),
            "urgency": urgency,
            "has_notes": bool(draft.quick_notes),
        })
        st.success(f"{'Updated' if is_editing else 'Saved'} as {saved_id}.")

with a2:
    if st.button("Review & Send  →", use_container_width=True, type="primary",
                 disabled=not ready_to_review,
                 help="Persist the quote and open the Quote Detail for contract + send."):
        q = _draft_quote()
        saved_id = save_quote(q)
        log_event(saved_id, "quote_drafted", {"line_items": len(q.line_items)})
        _reset_draft_state(reset_id=True)
        # See Phase 3 lock-in note — query_params don't always flush before
        # switch_page; use session_state as the reliable handoff.
        st.session_state["_pending_quote_id"] = saved_id
        st.query_params["quote_id"] = saved_id
        st.switch_page("pages/4_Quote_Detail.py")


# ---- Clear page (always available, bottom of page) ---------------------
st.markdown("---")
st.markdown(
    '<div style="color:#64748b;font-size:11px;font-weight:700;'
    'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">'
    "Reset</div>",
    unsafe_allow_html=True,
)
st.caption(
    "Wipes the customer info, quick notes, clarifying answers, and any in-progress projects "
    "from this page. Saved quotes in the database are NOT touched — they're still visible "
    "from Customers / Jobs / Quote Detail."
)
cp1, cp2, cp3 = st.columns([1, 2, 2])
with cp1:
    if st.session_state.get("_clear_confirm"):
        if st.button("Confirm clear", type="primary", use_container_width=True):
            _reset_draft_state(reset_id=True)
            st.session_state["_clear_confirm"] = False
            st.query_params.clear()
            st.success("Page cleared.")
            st.rerun()
    else:
        if st.button("🗑 Clear page", use_container_width=True,
                     help="Reset all in-progress data on this page (saved quotes are kept)."):
            st.session_state["_clear_confirm"] = True
            st.rerun()
with cp2:
    if st.session_state.get("_clear_confirm"):
        if st.button("Cancel", use_container_width=True):
            st.session_state["_clear_confirm"] = False
            st.rerun()
