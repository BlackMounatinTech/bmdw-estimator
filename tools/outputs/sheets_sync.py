"""Google Sheets sync — Job Ledger and Customer Roster.

The SQLite DB is the source of truth. This module mirrors the relevant rows
into a Google Sheet so Michael's accountant + CRA can see them anywhere.

Setup:
1. Create a Google Sheet with two tabs: "Job Ledger" and "Customer Roster".
2. Create a Google Cloud service account, download JSON key.
3. Share the Sheet with the service account's email (Editor access).
4. Drop the JSON key at config/google_service_account.json (gitignored).
5. Set the sheet ID in config/company.json under "google_sheet_id".

See workflows/setup_google_sheets.md for the full step-by-step.
"""

import json
from pathlib import Path
from typing import List, Optional

from server.schemas import Quote
from tools.storage import list_customers, list_recent_quotes

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
SERVICE_ACCOUNT_PATH = CONFIG_DIR / "google_service_account.json"

JOB_LEDGER_HEADERS = [
    "Invoice #", "Date Quoted", "Date Won", "Date Completed",
    "Customer", "Site Address", "Job Type(s)", "Description",
    "Quote Total", "Final Invoiced", "Internal Cost",
    "Gross Profit $", "Gross Profit %", "Status",
    "Quote PDF", "Contract PDF", "Notes",
]

CUSTOMER_ROSTER_HEADERS = [
    "Customer ID", "Name", "Phone", "Email", "Address",
    "First Job", "Last Activity", "# Jobs", "Lifetime Revenue", "Notes",
]


def _get_sheet_id() -> Optional[str]:
    cfg = json.loads((CONFIG_DIR / "company.json").read_text())
    sid = cfg.get("google_sheet_id")
    return sid if sid and sid != "TBD" else None


def _get_client():
    """Lazy import + auth — so the app boots even before sheets are configured."""
    if not SERVICE_ACCOUNT_PATH.exists():
        raise FileNotFoundError(
            f"Google service account JSON not found at {SERVICE_ACCOUNT_PATH}. "
            "See workflows/setup_google_sheets.md."
        )
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(str(SERVICE_ACCOUNT_PATH), scopes=scopes)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def is_configured() -> bool:
    return SERVICE_ACCOUNT_PATH.exists() and _get_sheet_id() is not None


def _quote_row(q: dict, full_quote: Optional[Quote] = None) -> List:
    job_types = ""
    description = ""
    if full_quote is not None:
        job_types = ", ".join({li.job_type.replace("_", " ").title() for li in full_quote.line_items})
        description = " + ".join(li.label for li in full_quote.line_items)
    profit = (q.get("final_invoiced") or q["customer_total"]) - q["internal_cost"]
    profit_pct = round(
        profit / (q.get("final_invoiced") or q["customer_total"]) * 100, 2
    ) if (q.get("final_invoiced") or q["customer_total"]) else 0
    return [
        q["quote_id"],
        q["created_at"][:10],
        q["updated_at"][:10] if q["status"] == "won" else "",
        q["updated_at"][:10] if q.get("final_invoiced") else "",
        q["customer_name"],
        q.get("customer_address", ""),
        job_types,
        description,
        q["customer_total"],
        q.get("final_invoiced") or "",
        q["internal_cost"],
        round(profit, 2),
        profit_pct,
        q["status"].upper(),
        "",  # Quote PDF link — wired once PDF generator lands
        "",  # Contract PDF link
        "",  # Notes
    ]


def _customer_row(c: dict) -> List:
    return [
        c["customer_id"],
        c["name"],
        c.get("phone", ""),
        c.get("email", ""),
        c.get("address", ""),
        (c.get("first_job_at") or "")[:10],
        (c.get("last_activity_at") or "")[:10],
        c.get("job_count", 0),
        round(c.get("lifetime_revenue", 0) or 0, 2),
        "",
    ]


def push_full_sync() -> dict:
    """Wipe and rewrite both sheets from the SQLite DB. Returns a status dict."""
    if not is_configured():
        return {"ok": False, "reason": "Google Sheets not configured. See workflows/setup_google_sheets.md."}

    sheet_id = _get_sheet_id()
    svc = _get_client().spreadsheets().values()

    # --- Job Ledger ---
    quotes = list_recent_quotes(limit=10_000)
    rows = [JOB_LEDGER_HEADERS]
    for q in quotes:
        from tools.storage import load_quote
        full = load_quote(q["quote_id"])
        rows.append(_quote_row(q, full))
    svc.clear(spreadsheetId=sheet_id, range="Job Ledger").execute()
    svc.update(
        spreadsheetId=sheet_id,
        range="Job Ledger!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()

    # --- Customer Roster ---
    customers = list_customers()
    rows = [CUSTOMER_ROSTER_HEADERS] + [_customer_row(c) for c in customers]
    svc.clear(spreadsheetId=sheet_id, range="Customer Roster").execute()
    svc.update(
        spreadsheetId=sheet_id,
        range="Customer Roster!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()

    return {"ok": True, "quotes_pushed": len(quotes), "customers_pushed": len(customers)}
