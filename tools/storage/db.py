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
from typing import List, Optional

from server.schemas import Quote, QuoteStatus
from tools.storage.paths import db_path


def _connect() -> sqlite3.Connection:
    DB_PATH = db_path()
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


def update_customer_full(
    customer_id: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    address: Optional[str] = None,
    propagate_to_quotes: bool = True,
) -> dict:
    """Update customer contact info + (by default) propagate the change into every
    quote JSON blob for this customer so existing quotes show the corrected info.

    Returns a status dict with how many quotes were updated.
    """
    updates = {"customer_updated": False, "quotes_propagated": 0}
    conn = _connect()
    try:
        # Update customer row
        sets, args = [], []
        for col, val in [("name", name), ("email", email), ("phone", phone), ("address", address)]:
            if val is not None:
                sets.append(f"{col}=?")
                args.append(val)
        if sets:
            args.append(customer_id)
            conn.execute(f"UPDATE customers SET {', '.join(sets)} WHERE customer_id=?", args)
            updates["customer_updated"] = True

        if not propagate_to_quotes:
            conn.commit()
            return updates

        # Propagate into quote JSON blobs so existing quotes show corrected info
        rows = conn.execute(
            "SELECT quote_id, quote_json FROM quotes WHERE customer_id=?",
            (customer_id,),
        ).fetchall()
        for row in rows:
            try:
                blob = json.loads(row["quote_json"])
                cust = blob.get("customer", {})
                if name is not None:
                    cust["name"] = name
                if email is not None:
                    cust["email"] = email or None
                if phone is not None:
                    cust["phone"] = phone or None
                if address is not None:
                    cust["address"] = address
                blob["customer"] = cust
                conn.execute(
                    "UPDATE quotes SET quote_json=?, updated_at=? WHERE quote_id=?",
                    (json.dumps(blob), _now(), row["quote_id"]),
                )
                updates["quotes_propagated"] += 1
            except Exception:
                # If a single blob fails to parse/update, skip it — don't kill the whole transaction.
                continue
        conn.commit()
        return updates
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
    """Sequential quote/invoice number starting from `quote_number_offset` in
    config/company.json (default 1768). Returns the next integer as a string,
    e.g. '1768', '1769'. We never reuse numbers — the next ID is always
    max(existing_numeric_ids, offset_minus_one) + 1.
    """
    from pathlib import Path
    cfg_path = Path(__file__).resolve().parents[2] / "config" / "company.json"
    try:
        offset = int(json.loads(cfg_path.read_text()).get("quote_number_offset", 1768))
    except Exception:
        offset = 1768

    conn = _connect()
    try:
        rows = conn.execute("SELECT quote_id FROM quotes").fetchall()
        max_existing = offset - 1
        for r in rows:
            qid = r["quote_id"]
            if qid and qid.isdigit():
                n = int(qid)
                if n > max_existing:
                    max_existing = n
        return str(max_existing + 1)
    finally:
        conn.close()


def _snapshot_quote(q: Quote) -> None:
    """Write a per-quote JSON snapshot to <data_dir>/backups/<quote_id>.json.
    Belt-and-suspenders backup: if the SQLite DB ever gets corrupted, lost,
    or wiped, every quote can be reconstructed from these JSON files.
    Snapshots live on the SAME persistent disk as the DB, plus they're
    downloadable individually or as a bundle from the Settings page."""
    try:
        from tools.storage.paths import data_dir as _ddir
        backup_dir = _ddir() / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        path = backup_dir / f"{q.quote_id}.json"
        path.write_text(q.model_dump_json(indent=2))
    except Exception:
        # Snapshots are insurance — never let a snapshot failure break a save.
        pass


def save_quote(q: Quote) -> str:
    """Insert or update a quote. Returns the quote_id (assigns one if missing).
    Also writes a JSON snapshot to <data_dir>/backups/ as redundant insurance."""
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
    finally:
        conn.close()

    # Always-on JSON sidecar — runs OUTSIDE the DB transaction so a snapshot
    # failure never breaks the save.
    _snapshot_quote(q)
    return q.quote_id


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


def restore_from_snapshots() -> dict:
    """Walk <data_dir>/backups/*.json and re-save any quotes not currently in
    the DB. Idempotent — re-running with everything intact does nothing.
    Returns a status dict with counts."""
    from tools.storage.paths import data_dir as _ddir
    backup_dir = _ddir() / "backups"
    if not backup_dir.exists():
        return {"ok": True, "found": 0, "restored": 0, "already_present": 0,
                "failed": 0, "reason": "No backups directory yet."}

    found = 0
    restored = 0
    already = 0
    failed = 0
    failures = []

    conn = _connect()
    try:
        existing_ids = {r["quote_id"] for r in conn.execute("SELECT quote_id FROM quotes").fetchall()}
    finally:
        conn.close()

    for path in sorted(backup_dir.glob("*.json")):
        found += 1
        try:
            blob = json.loads(path.read_text())
            qid = blob.get("quote_id")
            if not qid:
                failed += 1
                failures.append(f"{path.name}: missing quote_id")
                continue
            if qid in existing_ids:
                already += 1
                continue
            q = Quote.model_validate(blob)
            save_quote(q)  # writes both DB + snapshot (idempotent for snapshot)
            restored += 1
        except Exception as exc:
            failed += 1
            failures.append(f"{path.name}: {exc}")

    return {"ok": True, "found": found, "restored": restored,
            "already_present": already, "failed": failed,
            "failures": failures[:20]}


def list_snapshot_files() -> list:
    """Return list of {filename, size, modified} for snapshot inventory display."""
    from tools.storage.paths import data_dir as _ddir
    backup_dir = _ddir() / "backups"
    if not backup_dir.exists():
        return []
    out = []
    for path in sorted(backup_dir.glob("*.json")):
        s = path.stat()
        out.append({
            "filename": path.name,
            "size": s.st_size,
            "modified": datetime.fromtimestamp(s.st_mtime).isoformat(timespec="seconds"),
            "path": str(path),
        })
    return out


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
