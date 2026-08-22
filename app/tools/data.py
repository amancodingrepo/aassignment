"""Structured lookups.

Parameterised functions rather than text-to-SQL. Text-to-SQL demos beautifully
and then turns scope enforcement into a string-parsing problem: you end up
trying to prove that no generated WHERE clause can widen the filter you injected.
Named tools with an injected account filter cannot be prompt-injected into a
cross-account read, because the model never writes the query.

`account_id` arrives here already decided by the gate. These functions trust it
and nothing else.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.authority import TIER_HISTORICAL_TICKET

MAX_LIMIT = 100
DEFAULT_LIMIT = 20


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _clamp(limit: int | None) -> int:
    if not limit or limit < 1:
        return DEFAULT_LIMIT
    return min(int(limit), MAX_LIMIT)


def _wrap_historical_resolution(ticket: dict[str, Any]) -> dict[str, Any]:
    """Tier-6 framing applied at the tool layer, not asked for in the prompt.

    The workbook's own README says historical resolutions may be incorrect. A
    system prompt that says "treat these as context only" survives right up to
    the first adversarial question; a wrapper that makes the warning part of the
    data the model reads does not have that failure mode.
    """
    value = ticket.get("historical_resolution")
    if not value:
        ticket["historical_resolution"] = None
        return ticket
    ticket["historical_resolution"] = {
        "value": value,
        "authority": "context_only",
        "tier": TIER_HISTORICAL_TICKET,
        "warning": (
            "Historical support resolution. May be incorrect and is never policy "
            "authority. Verify against the current agreement, SOP, policy or "
            "product guide before repeating it."
        ),
    }
    return ticket


def lookup_orders(
    conn: sqlite3.Connection,
    *,
    account_id: str | None = None,
    order_id: str | None = None,
    status: str | None = None,
    carrier: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []

    if account_id:
        clauses.append("account_id = ?")
        params.append(account_id)
    if order_id:
        clauses.append("order_id = ?")
        params.append(order_id)
    if status:
        clauses.append("UPPER(status) = ?")
        params.append(status.upper())
    if carrier:
        clauses.append("LOWER(carrier) = ?")
        params.append(carrier.lower())

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM orders{where} ORDER BY order_id LIMIT ?"
    params.append(_clamp(limit))

    rows = _rows_to_dicts(conn.execute(sql, params).fetchall())
    return {
        "orders": rows,
        "found": bool(rows),
        "count": len(rows),
        "filters": {
            "order_id": order_id,
            "status": status,
            "carrier": carrier,
        },
    }


def lookup_tickets(
    conn: sqlite3.Connection,
    *,
    account_id: str | None = None,
    ticket_id: str | None = None,
    status: str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []

    if account_id:
        clauses.append("account_id = ?")
        params.append(account_id)
    if ticket_id:
        clauses.append("ticket_id = ?")
        params.append(ticket_id)
    if status:
        clauses.append("LOWER(status) = ?")
        params.append(status.lower())
    if since:
        clauses.append("created_at >= ?")
        params.append(since)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM tickets{where} ORDER BY created_at DESC LIMIT ?"
    params.append(_clamp(limit))

    rows = [_wrap_historical_resolution(row) for row in _rows_to_dicts(conn.execute(sql, params).fetchall())]
    return {
        "tickets": rows,
        "found": bool(rows),
        "count": len(rows),
        "filters": {"ticket_id": ticket_id, "status": status, "since": since},
    }


def lookup_account(
    conn: sqlite3.Connection, *, account_id: str | None = None
) -> dict[str, Any]:
    """Which plan, and which contract governs. The agent should not infer the
    contract from the account name -- that is how ACCT-004 ends up being read
    against Northstar's agreement because both are Enterprise."""
    if account_id:
        row = conn.execute(
            "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        if row is None:
            return {"account": None, "found": False}
        account = dict(row)
        account["has_contract"] = bool(account.get("contract_file"))
        return {"account": account, "found": True}

    rows = _rows_to_dicts(
        conn.execute("SELECT * FROM accounts ORDER BY account_id").fetchall()
    )
    for account in rows:
        account["has_contract"] = bool(account.get("contract_file"))
    return {"accounts": rows, "found": bool(rows), "count": len(rows)}


def read_audit_log(conn: sqlite3.Connection, limit: int | None = None) -> dict[str, Any]:
    rows = _rows_to_dicts(
        conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (_clamp(limit),)
        ).fetchall()
    )
    return {"entries": rows, "count": len(rows)}


def list_actions(
    conn: sqlite3.Connection, *, account_id: str | None = None, limit: int | None = None
) -> dict[str, Any]:
    if account_id:
        rows = conn.execute(
            "SELECT * FROM actions WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
            (account_id, _clamp(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM actions ORDER BY created_at DESC LIMIT ?", (_clamp(limit),)
        ).fetchall()
    return {"actions": _rows_to_dicts(rows), "count": len(rows)}
