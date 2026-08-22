"""SQLite access.

SQLite rather than in-memory pandas on purpose: the scope gate's guarantee is
that a customer-scoped read cannot address another account's rows, and that is
far easier to believe when it is a `WHERE account_id = ?` in a real query
boundary than when it is a filter applied somewhere in Python.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
  account_id TEXT PRIMARY KEY,
  account_name TEXT,
  plan TEXT,
  status TEXT,
  csm TEXT,
  contract_file TEXT,
  premium_support INTEGER,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS orders (
  order_id TEXT PRIMARY KEY,
  account_id TEXT,
  carrier TEXT,
  status TEXT,
  booked_at TEXT,
  pickup_window_start TEXT,
  pickup_window_end TEXT,
  pickup_actual_at TEXT,
  shipment_fee_inr REAL,
  carrier_fault INTEGER,
  customer_fault INTEGER,
  cancellation_requested_at TEXT,
  notes TEXT,
  minutes_booked_to_cancel_request REAL,
  hours_past_pickup_window_end REAL,
  is_cancellable_status INTEGER
);

CREATE TABLE IF NOT EXISTS tickets (
  ticket_id TEXT PRIMARY KEY,
  account_id TEXT,
  created_at TEXT,
  status TEXT,
  subject TEXT,
  description TEXT,
  channel TEXT,
  assigned_to TEXT,
  last_customer_message_at TEXT,
  historical_resolution TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY,
  doc_id TEXT,
  doc_title TEXT,
  tier INTEGER,
  status TEXT,
  scope TEXT,
  effective_from TEXT,
  effective_to TEXT,
  section TEXT,
  section_title TEXT,
  topics_json TEXT,
  flags_json TEXT,
  text TEXT,
  internal_only INTEGER
);

CREATE TABLE IF NOT EXISTS sla_targets (
  doc_id TEXT,
  chunk_id TEXT,
  tier INTEGER,
  status TEXT,
  scope TEXT,
  plan TEXT,
  severity TEXT,
  target_text TEXT,
  target_minutes REAL,
  business_hours REAL,
  business_days REAL,
  coverage TEXT,
  PRIMARY KEY (doc_id, plan, severity)
);

CREATE TABLE IF NOT EXISTS actions (
  action_id TEXT PRIMARY KEY,
  kind TEXT,
  ticket_id TEXT,
  account_id TEXT,
  payload_json TEXT,
  created_by TEXT,
  created_at TEXT,
  status TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  session_role TEXT,
  session_account TEXT,
  tool TEXT,
  args_json TEXT,
  allowed INTEGER,
  denial_reason TEXT
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(path) if path is not None else DB_PATH
    if str(target) != ":memory:":
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None
