"""Shared utilities for the BMDW estimator Streamlit app.

Theme is the BMT design system (v3.0) ported per
/Users/michaelmackrell/BMT_Shared/DESIGN_SYSTEM.md. Streamlit default font.
Skip the BMT orange brand accent on this product — BMDW has its own brand.
"""

import os
from pathlib import Path

# Load .env on import — every page imports from this module, so env vars
# (ANTHROPIC_API_KEY, BMDW_APP_PASSWORD) are available everywhere.
try:
    from dotenv import load_dotenv
    _ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH, override=False)
except ImportError:
    pass  # python-dotenv not installed; rely on shell env

import streamlit as st


def require_auth() -> None:
    """Password gate. Reads BMDW_APP_PASSWORD from env; if empty/missing, no gate.

    Call this immediately after apply_theme() on every page. Renders a
    centered password prompt and st.stop()s rendering until correct.
    """
    expected = os.environ.get("BMDW_APP_PASSWORD", "").strip()
    if not expected:
        return  # gate disabled

    if st.session_state.get("_bmdw_auth_ok"):
        return

    st.markdown(
        '<div style="max-width:360px;margin:80px auto 0;text-align:center;">'
        '<div style="font-size:14px;color:#94a3b8;letter-spacing:0.06em;'
        'text-transform:uppercase;margin-bottom:8px;">Black Mountain Dirt Works</div>'
        '<div style="color:#cbd5e1;font-size:13px;margin-bottom:20px;">'
        "Enter passcode to continue.</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        with st.form("auth_gate", clear_on_submit=False):
            pw = st.text_input("Passcode", type="password", label_visibility="collapsed",
                               placeholder="Passcode")
            ok = st.form_submit_button("Unlock", use_container_width=True, type="primary")
            if ok:
                if pw == expected:
                    st.session_state["_bmdw_auth_ok"] = True
                    st.rerun()
                else:
                    st.error("Wrong passcode.")
    st.stop()


def apply_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
            --bg-app: #f8fafc;
            --bg-sidebar: #f1f5f9;
            --bg-card: #ffffff;
            --bg-card-alt: #f8fafc;
            --bg-card-accent: #eef2f7;
            --border: #e2e8f0;
            --border-hover: #cbd5e1;
            --text-heading: #0f172a;
            --text-body: #334155;
            --text-value: #1e293b;
            --text-secondary: #475569;
            --text-tertiary: #64748b;
            --active-border: #2563eb;
            --rag-green: #16a34a;
            --rag-yellow: #d97706;
            --rag-red: #dc2626;
            --sev-critical: #dc2626;
            --sev-high: #d97706;
            --sev-medium: #2563eb;
            --sev-low: #64748b;
        }

        .stApp { background-color: #f8fafc; }
        [data-testid="stHeader"] { background-color: #f8fafc; }
        [data-testid="stSidebar"] {
            background-color: #f1f5f9;
            border-right: 1px solid #e2e8f0;
        }
        [data-testid="stSidebar"] * { color: #475569 !important; }
        [data-testid="stSidebar"] h1 { color: #0f172a !important; }

        h1, h2, h3 {
            color: #0f172a !important;
            font-weight: 600 !important;
            letter-spacing: -0.02em;
        }
        p, span, label, div { color: #334155; }

        [data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 12px;
            padding: 18px 14px;
            overflow: visible !important;
            min-width: 0;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        [data-testid="stMetric"] > div { overflow: visible !important; }
        [data-testid="stMetric"] > div > div { overflow: visible !important; }
        [data-testid="stMetricLabel"] {
            color: #64748b !important;
            font-size: 11px !important;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            white-space: nowrap;
            overflow: visible !important;
        }
        [data-testid="stMetricValue"] {
            color: #0f172a !important;
            font-size: 20px !important;
            font-weight: 700 !important;
            white-space: nowrap !important;
            overflow: visible !important;
            text-overflow: unset !important;
        }
        [data-testid="stMetricDelta"] { font-size: 13px !important; }

        [data-testid="stDataFrame"] {
            border-radius: 12px;
            overflow: hidden;
        }
        .stDataFrame div[data-testid="stDataFrameResizable"] {
            border: 1px solid #e2e8f0;
            border-radius: 12px;
        }

        hr { border-color: #e2e8f0 !important; }

        .stTabs [data-baseweb="tab-list"] {
            gap: 8px;
            background: transparent;
        }
        .stTabs [data-baseweb="tab"] {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            color: #475569;
            padding: 10px 20px;
        }
        .stTabs [aria-selected="true"] {
            background: #eef4ff !important;
            border-color: #2563eb !important;
            color: #0f172a !important;
        }

        [data-testid="stSelectbox"] div[data-baseweb="select"] {
            background: #ffffff;
            border-color: #e2e8f0;
        }

        #MainMenu, footer { display: none; }

        /* Touch-friendly buttons */
        .stButton > button {
            min-height: 48px;
            border-radius: 10px;
            border: 1px solid #cbd5e1;
            background: #ffffff;
            color: #0f172a;
            font-weight: 600;
            transition: all 0.15s ease;
            padding: 14px 18px !important;
        }
        .stButton > button:hover {
            border-color: #2563eb;
            background: #f8fafc;
        }

        /* Project card */
        .project-card {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 16px;
            padding: 28px;
            transition: all 0.2s;
            box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05);
        }
        .project-card:hover { border-color: #cbd5e1; }
        .card-label {
            color: #64748b;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            margin-bottom: 6px;
        }
        .card-project {
            color: #0f172a;
            font-size: 16px;
            font-weight: 600;
            margin-bottom: 14px;
            line-height: 1.4;
        }
        .card-number {
            font-size: 36px;
            font-weight: 800;
            line-height: 1;
            margin-bottom: 10px;
        }
        .card-detail {
            color: #64748b;
            font-size: 13px;
            line-height: 1.8;
        }
        .card-detail-value { color: #1e293b; font-weight: 500; }
        .card-risk {
            color: #475569;
            font-size: 12px;
            margin-top: 16px;
            padding-top: 16px;
            border-top: 1px solid #e2e8f0;
            line-height: 1.6;
        }
        .status-dot {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 6px;
        }

        .risk-row {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 12px;
            padding: 16px 20px;
            margin-bottom: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .risk-label { color: #334155; font-size: 13px; }
        .risk-project { color: #64748b; font-size: 11px; margin-top: 2px; }
        .risk-amount { font-size: 16px; font-weight: 700; }

        .section-header {
            color: #0f172a;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            font-weight: 700;
            margin-top: 32px;
            margin-bottom: 18px;
            padding-bottom: 12px;
            border-bottom: 1px solid #e2e8f0;
        }

        /* --- Primary button — clearly visible blue on light bg --- */
        [data-testid="stButton"] button[kind="primary"],
        button[data-testid="baseButton-primary"],
        button[data-testid="stBaseButton-primary"] {
            background: #2563eb !important;
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
            border: 1px solid #1d4ed8 !important;
            box-shadow: 0 2px 8px rgba(37, 99, 235, 0.25) !important;
            font-weight: 700 !important;
            padding: 14px 18px !important;
            margin-top: 14px !important;
        }
        [data-testid="stButton"] button[kind="primary"]:hover,
        button[data-testid="baseButton-primary"]:hover,
        button[data-testid="stBaseButton-primary"]:hover {
            background: #1d4ed8 !important;
            border-color: #1e40af !important;
            box-shadow: 0 4px 12px rgba(37, 99, 235, 0.35) !important;
        }
        [data-testid="stButton"] button[kind="primary"]:disabled,
        button[data-testid="baseButton-primary"]:disabled,
        button[data-testid="stBaseButton-primary"]:disabled {
            background: #e2e8f0 !important;
            color: #94a3b8 !important;
            -webkit-text-fill-color: #94a3b8 !important;
            border: 1px solid #cbd5e1 !important;
            box-shadow: none !important;
            cursor: not-allowed !important;
        }

        /* --- Looser spacing — real breathing room --- */
        h1, h2, h3 { margin-top: 24px !important; margin-bottom: 16px !important; }
        [data-testid="stExpander"] { margin-bottom: 18px !important; }
        .stTextInput, .stTextArea, .stSelectbox, .stNumberInput { margin-bottom: 14px !important; }
        [data-testid="stTextInput"] input,
        [data-testid="stTextArea"] textarea {
            background: #ffffff !important;
            border: 1px solid #cbd5e1 !important;
            border-radius: 8px !important;
            color: #0f172a !important;
            padding: 12px 14px !important;
        }
        [data-testid="stTextInput"] input:focus,
        [data-testid="stTextArea"] textarea:focus {
            border-color: #2563eb !important;
            box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12) !important;
        }

        /* Generous block container so content doesn't hug the screen edges */
        .main .block-container,
        [data-testid="stAppViewContainer"] .main .block-container {
            padding-top: 2rem !important;
            padding-bottom: 4rem !important;
            padding-left: 2rem !important;
            padding-right: 2rem !important;
        }
        /* Vertical breathing room between auto-stacked elements */
        [data-testid="stVerticalBlock"] > [data-testid="stVerticalBlock"] {
            gap: 0.9rem !important;
        }

        @media (max-width: 768px) {
            html { -webkit-text-size-adjust: 100%; }
            .main .block-container,
            [data-testid="stAppViewContainer"] .main .block-container {
                padding-left: 1rem !important;
                padding-right: 1rem !important;
                padding-top: 1.4rem !important;
                max-width: 100% !important;
            }
            [data-testid="stVerticalBlock"] > [data-testid="stVerticalBlock"] {
                gap: 0.7rem !important;
            }
            .stApp { padding: 0 !important; }
            [data-testid="stSidebar"] { min-width: 200px !important; }

            [data-testid="stHorizontalBlock"] {
                flex-wrap: wrap !important;
                gap: 10px !important;
            }
            [data-testid="stHorizontalBlock"] > div {
                flex: 1 1 100% !important;
                min-width: 0 !important;
            }
            [data-testid="stMetric"] {
                padding: 14px 12px !important;
                border-radius: 10px !important;
            }
            [data-testid="stMetricValue"] { font-size: 17px !important; }
            [data-testid="stMetricLabel"] { font-size: 10px !important; }

            .project-card {
                padding: 18px !important;
                border-radius: 12px !important;
            }
            .card-number { font-size: 26px !important; }
            .card-project { font-size: 15px !important; }
            .card-detail { font-size: 12px !important; }

            [data-testid="stDataFrame"] { overflow-x: auto !important; }

            .stTabs [data-baseweb="tab"] {
                padding: 8px 14px !important;
                font-size: 12px !important;
            }

            .section-header {
                font-size: 11px !important;
                margin-top: 22px !important;
                margin-bottom: 14px !important;
                padding-bottom: 10px !important;
            }
            .risk-row {
                padding: 12px 14px !important;
                flex-direction: column !important;
                gap: 6px !important;
            }
            .risk-amount { font-size: 14px !important; }

            .stButton > button { min-height: 56px; font-size: 16px; }
        }

        @media (max-width: 480px) {
            [data-testid="stMetricValue"] { font-size: 15px !important; }
            .card-number { font-size: 22px !important; }
            h1 { font-size: 22px !important; }
            h2 { font-size: 18px !important; }
            h3 { font-size: 15px !important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# Status color helpers — used across cards and pills.
RAG_GREEN = "#22c55e"
RAG_YELLOW = "#f59e0b"
RAG_RED = "#ef4444"
SEV_MEDIUM = "#3b82f6"


def status_dot(color: str) -> str:
    return f'<span class="status-dot" style="background:{color}"></span>'


def status_pill(label: str, color: str) -> str:
    return (
        f'<span style="background:{color};color:white;font-size:10px;'
        f"font-weight:700;padding:2px 8px;border-radius:4px;"
        f'text-transform:uppercase;">{label}</span>'
    )


def alert_card(content_html: str, status_color: str) -> str:
    return (
        f'<div style="background:#111827;border:1px solid #1e293b;'
        f"border-left:4px solid {status_color};border-radius:12px;"
        f'padding:14px 18px;margin-bottom:8px;">{content_html}</div>'
    )


def section_header(label: str) -> None:
    st.markdown(f'<div class="section-header">{label}</div>', unsafe_allow_html=True)


def fmt_money(value: float) -> str:
    return f"${value:,.0f}"


# ---------------------------------------------------------------------------
# Catalogue + takeoff helpers — used by both the capture screen and Quote Detail
# ---------------------------------------------------------------------------

import json as _json
from pathlib import Path as _Path


def _config_dir() -> _Path:
    return _Path(__file__).resolve().parents[1] / "config"


def load_catalogue(name: str) -> dict:
    """Load one of the 5 global catalogues, stripping _README/_settings keys."""
    data = _json.loads((_config_dir() / f"{name}.json").read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def bucket_to_catalogue_name(bucket) -> "str | None":
    from server.schemas import CostBucket
    return {
        CostBucket.MATERIALS: "materials",
        CostBucket.EQUIPMENT: "equipment",
        CostBucket.TRUCKING: "trucking",
        CostBucket.LABOUR: "labour",
        CostBucket.SPOIL: None,
    }[bucket]


def entry_from_catalogue(bucket, cat_key: str, qty: float):
    """Build a LineItemEntry from a catalogue pick + quantity."""
    from server.schemas import LineItemEntry
    cat_name = bucket_to_catalogue_name(bucket)
    cat = load_catalogue(cat_name)
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
        # Trucking supports both hourly (BMDW + contracted tandems) and per-load
        # (block delivery, mobilization). Pick whichever rate the catalogue defines.
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


def render_project_takeoff(li, key_prefix: str) -> bool:
    """Render a project as expander → 5 bucket tabs → entries + add forms.

    Mutates `li.entries` in place when the user adds/edits/removes entries.
    Caller is responsible for persisting (save_quote / session state) and
    calling st.rerun(). Returns True if any change happened in this render.
    """
    from server.schemas import CostBucket, LineItemEntry

    edited = False

    # Project bucket totals strip
    cols = st.columns(5)
    for col, bucket in zip(cols, CostBucket):
        col.metric(bucket.value.title(), fmt_money(li.bucket_total(bucket)))

    st.markdown("&nbsp;", unsafe_allow_html=True)

    # Bucket tabs
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
                    row_key = f"{key_prefix}_row_{entry_idx}"
                    edit_flag_key = f"editing_{row_key}"
                    editing = st.session_state.get(edit_flag_key, False)

                    if not editing:
                        ec = st.columns([5, 2, 2, 1])
                        ec[0].markdown(
                            f'<div style="color:#cbd5e1;font-size:13px;padding:4px 0;">{e.description}</div>',
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
                            if st.button("", key=f"edit_btn_{row_key}", help="Edit"):
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
                                help="Equipment-bucket only. Trucks should be unticked.",
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

            # ---- Add forms (catalogue + freeform) ----
            st.markdown("&nbsp;", unsafe_allow_html=True)
            cat_name = bucket_to_catalogue_name(bucket)
            add_key_base = f"{key_prefix}_add_{bucket.value}"

            if cat_name:
                cat = load_catalogue(cat_name)
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
                        if ac3.form_submit_button("+ Add", use_container_width=True) and qty > 0:
                            li.entries.append(entry_from_catalogue(bucket, pick, qty))
                            edited = True

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
                if fc4.form_submit_button("+ Add", use_container_width=True) and new_desc and new_qty > 0:
                    li.entries.append(LineItemEntry(
                        bucket=bucket,
                        description=new_desc,
                        quantity=float(new_qty),
                        unit_cost=float(new_cost),
                        unit="lump",
                        rental_insurance_eligible=False,
                    ))
                    edited = True

    return edited
