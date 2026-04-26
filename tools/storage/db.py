"""SQLite storage layer.

Source of truth for all quote/customer/job data. Spreadsheet sync is a mirror
generated from this database — never the reverse. An append-only `job_events`
table is the audit trail (CRA-friendly).

Schema is intentionally simple. Quotes are stored as JSON blobs alongside
their relational metadata; the JSON is the same Pydantic Quote object used
everywhere else in the app.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from server.schemas import Quote, QuoteStatus

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "bmdw.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS customers (
                customer_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT,
                phone TEXT,
                address TEXT,
                lead_status TEXT NOT NULL DEFAULT 'cold',
                notes TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS quotes (
                quote_id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                status TEXT NOT NULL,
                customer_total REAL NOT NULL,
                internal_cost REAL NOT NULL,
                margin_pct REAL NOT NULL,
                final_invoiced REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                quote_json TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
            );

            -- Append-only audit trail. Never UPDATE or DELETE rows here.
            CREATE TABLE IF NOT EXISTS job_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                quote_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                occurred_at TEXT NOT NULL,
                FOREIGN KEY (quote_id) REFERENCES quotes(quote_id)
            );

            CREATE INDEX IF NOT EXISTS idx_quotes_customer
                ON quotes(customer_id);
            CREATE INDEX IF NOT EXISTS idx_events_quote
                ON job_events(quote_id);
            """
        )
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")


def upsert_customer_from_quote(q: Quote) -> str:
    """Ensure the customer exists; return customer_id. Doesn't overwrite
    lead_status or notes (those are managed via the Customers page)."""
    cust = q.customer
    customer_id = f"CUST-{_slug(cust.name)[:20] or 'unnamed'}"
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT customer_id FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE customers SET email=?, phone=?, address=? "
                "WHERE customer_id=?",
                (cust.email, cust.phone, cust.address, customer_id),
            )
        else:
            conn.execute(
                "INSERT INTO customers (customer_id, name, email, phone, "
                "address, lead_status, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (customer_id, cust.name, cust.email, cust.phone, cust.address,
                 cust.lead_status.value if hasattr(cust.lead_status, "value") else cust.lead_status,
                 cust.notes, _now()),
            )
        conn.commit()
        return customer_id
    finally:
        conn.close()


def update_customer_meta(customer_id: str, lead_status: str = None, notes: str = None) -> None:
    conn = _connect()
    try:
        sets, args = [], []
        if lead_status is not None:
            sets.append("lead_status=?")
            args.append(lead_status)
        if notes is not None:
            sets.append("notes=?")
            args.append(notes)
        if not sets:
            return
        args.append(customer_id)
        conn.execute(f"UPDATE customers SET {', '.join(sets)} WHERE customer_id=?", args)
        conn.commit()
    finally:
        conn.close()


def next_quote_id() -> str:
    """Sequential per-year invoice number, e.g. 2026-041."""
    conn = _connect()
    try:
        year = datetime.utcnow().year
        prefix = f"{year}-"
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM quotes WHERE quote_id LIKE ?",
            (f"{prefix}%",),
        ).fetchone()
        return f"{prefix}{row['n'] + 1:03d}"
    finally:
        conn.close()


def save_quote(q: Quote) -> str:
    """Insert or update a quote. Returns the quote_id (assigns one if missing)."""
    if q.quote_id == "DRAFT" or not q.quote_id:
        q.quote_id = next_quote_id()
    customer_id = upsert_customer_from_quote(q)

    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT quote_id FROM quotes WHERE quote_id = ?", (q.quote_id,)
        ).fetchone()
        payload = (
            q.status.value,
            q.customer_total,
            q.internal_cost,
            q.margin_pct,
            None,  # final_invoiced — set later via mark_completed()
            _now(),
            q.model_dump_json(),
            q.quote_id,
        )
        if existing:
            conn.execute(
                "UPDATE quotes SET status=?, customer_total=?, internal_cost=?, "
                "margin_pct=?, final_invoiced=COALESCE(?, final_invoiced), "
                "updated_at=?, quote_json=? WHERE quote_id=?",
                payload,
            )
        else:
            conn.execute(
                "INSERT INTO quotes (status, customer_total, internal_cost, "
                "margin_pct, final_invoiced, created_at, updated_at, quote_json, "
                "quote_id, customer_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*payload[:5], _now(), *payload[5:], customer_id),
            )
        conn.commit()
        return q.quote_id
    finally:
        conn.close()


def log_event(quote_id: str, event_type: str, payload: Optional[dict] = None) -> None:
    """Append-only event log. Never UPDATE or DELETE these rows."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO job_events (quote_id, event_type, payload_json, occurred_at) "
            "VALUES (?, ?, ?, ?)",
            (quote_id, event_type, json.dumps(payload) if payload else None, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def delete_quote(quote_id: str) -> bool:
    """Permanently delete a quote and its event log. Returns True if a row was removed."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM job_events WHERE quote_id = ?", (quote_id,))
        cur = conn.execute("DELETE FROM quotes WHERE quote_id = ?", (quote_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def mark_status(quote_id: str, status: QuoteStatus, final_invoiced: Optional[float] = None) -> None:
    conn = _connect()
    try:
        if final_invoiced is not None:
            conn.execute(
                "UPDATE quotes SET status=?, final_invoiced=?, updated_at=? WHERE quote_id=?",
                (status.value, final_invoiced, _now(), quote_id),
            )
        else:
            conn.execute(
                "UPDATE quotes SET status=?, updated_at=? WHERE quote_id=?",
                (status.value, _now(), quote_id),
            )
        conn.commit()
    finally:
        conn.close()
    log_event(
        quote_id,
        f"status_changed_to_{status.value}",
        {"final_invoiced": final_invoiced} if final_invoiced is not None else None,
    )


def list_customers() -> List[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT c.customer_id, c.name, c.email, c.phone, c.address,
                   c.lead_status, c.notes,
                   COUNT(q.quote_id) AS job_count,
                   COALESCE(SUM(COALESCE(q.final_invoiced, q.customer_total)), 0) AS lifetime_revenue,
                   MIN(q.created_at) AS first_job_at,
                   MAX(q.updated_at) AS last_activity_at
            FROM customers c
            LEFT JOIN quotes q ON q.customer_id = c.customer_id
            GROUP BY c.customer_id
            ORDER BY last_activity_at DESC NULLS LAST
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_quotes_for_customer(customer_id: str) -> List[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT quote_id, status, customer_total, internal_cost, margin_pct, "
            "final_invoiced, created_at, updated_at FROM quotes "
            "WHERE customer_id = ? ORDER BY created_at DESC",
            (customer_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_recent_quotes(limit: int = 20) -> List[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT q.quote_id, q.status, q.customer_total, q.internal_cost, "
            "q.margin_pct, q.final_invoiced, q.created_at, q.updated_at, "
            "c.name AS customer_name, c.address AS customer_address "
            "FROM quotes q JOIN customers c ON c.customer_id = q.customer_id "
            "ORDER BY q.updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def load_quote(quote_id: str) -> Optional[Quote]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT quote_json FROM quotes WHERE quote_id = ?", (quote_id,)
        ).fetchone()
        if not row:
            return None
        return Quote.model_validate_json(row["quote_json"])
    finally:
        conn.close()


def load_events(quote_id: str) -> List[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT event_type, payload_json, occurred_at FROM job_events "
            "WHERE quote_id = ? ORDER BY occurred_at DESC",
            (quote_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def dashboard_metrics() -> dict:
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT
              COUNT(CASE WHEN status IN ('draft','sent') THEN 1 END) AS open_quotes,
              COALESCE(SUM(CASE WHEN status='won' AND date(updated_at) >= date('now','-7 day')
                                  THEN COALESCE(final_invoiced, customer_total) END), 0) AS week_won_dollars,
              COALESCE(AVG(CASE WHEN status='won' THEN margin_pct END), 0) AS avg_margin_pct
            FROM quotes
            """
        ).fetchone()
        return dict(row)
    finally:
        conn.close()
