"""State-changing actions, and the two-phase confirm protocol.

The important property is structural rather than behavioural. `propose_action`
is a tool the model can call and it writes nothing. `execute_confirmed` is not a
tool at all -- it is reachable only from the `/confirm` HTTP endpoint, with a
single-use token bound to the session that created the proposal. So it is not
that the model asks nicely before writing; it is that a model turn has no path
to a write. Those are very different security properties and only one of them
survives a jailbreak.

Token expiry is measured with `time.monotonic`, not the dataset snapshot. A
confirm card timing out is real elapsed time in front of a real person, which is
the one clock in this system that is not frozen.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import CONFIRM_TOKEN_TTL_SECONDS, snapshot_now
from app.session import Session
from app.tools import calc

KIND_ESCALATION = "escalation"
KIND_TICKET_UPDATE = "ticket_update"
KIND_FOLLOW_UP = "follow_up_task"

KINDS = (KIND_ESCALATION, KIND_TICKET_UPDATE, KIND_FOLLOW_UP)

# Preview keys that carry internal routing information. The gate strips internal
# *columns*; these are derived fields, so they are trimmed here.
INTERNAL_ONLY_PREVIEW_FIELDS = ("notify", "assign_to")

ACTION_PREFIXES = {
    KIND_ESCALATION: "ESC",
    KIND_TICKET_UPDATE: "UPD",
    KIND_FOLLOW_UP: "TSK",
}


class ProposalError(RuntimeError):
    pass


@dataclass
class Proposal:
    token: str
    session_id: str
    kind: str
    ticket_id: str | None
    account_id: str | None
    payload: dict[str, Any]
    preview: dict[str, Any]
    warnings: list[str]
    created_monotonic: float
    expires_at_display: str
    used: bool = False
    cancelled: bool = False
    fingerprint: str = ""

    def is_expired(self, now: float | None = None) -> bool:
        current = now if now is not None else time.monotonic()
        return (current - self.created_monotonic) > CONFIRM_TOKEN_TTL_SECONDS


@dataclass
class ProposalStore:
    """In-process store. Proposals are short-lived by design, so they do not
    need to survive a restart -- and a proposal that outlived the conversation
    that produced it would be a liability, not a feature."""

    _items: dict[str, Proposal] = field(default_factory=dict)

    def put(self, proposal: Proposal) -> None:
        self._items[proposal.token] = proposal

    def get(self, token: str) -> Proposal | None:
        return self._items.get(token)

    def clear(self) -> None:
        self._items.clear()


STORE = ProposalStore()


def _fingerprint(kind: str, ticket_id: str | None, payload: dict[str, Any]) -> str:
    """Identifies exactly what was previewed.

    If the user edits a field in the confirm card the fingerprint no longer
    matches, the token is refused, and a fresh proposal has to be generated --
    so what gets written is always what was shown.
    """
    body = json.dumps(
        {"kind": kind, "ticket_id": ticket_id, "payload": payload},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _next_action_id(conn: sqlite3.Connection, kind: str) -> str:
    prefix = ACTION_PREFIXES.get(kind, "ACT")
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM actions WHERE kind = ?", (kind,)
    ).fetchone()
    return f"{prefix}-{1001 + int(row['n'])}"


def _account_row(conn: sqlite3.Connection, account_id: str | None) -> sqlite3.Row | None:
    if not account_id:
        return None
    return conn.execute(
        "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
    ).fetchone()


def _ticket_row(
    conn: sqlite3.Connection, ticket_id: str | None, account_id: str | None
) -> sqlite3.Row | None:
    if not ticket_id:
        return None
    if account_id:
        return conn.execute(
            "SELECT * FROM tickets WHERE ticket_id = ? AND account_id = ?",
            (ticket_id, account_id),
        ).fetchone()
    return conn.execute(
        "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
    ).fetchone()


def _escalation_preview(
    conn: sqlite3.Connection, ticket: sqlite3.Row, payload: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    account = _account_row(conn, ticket["account_id"])

    severity = payload.get("severity")
    severity_source = None
    if not severity:
        classified = calc.classify_severity(conn, ticket["ticket_id"])
        severity = classified["severity"]
        severity_source = classified["source"]

    sla = calc.sla_status(conn, ticket["ticket_id"], severity=severity)
    sources = [source["citation"] for source in filter(None, [sla.get("source"), severity_source])]

    if sla.get("breached"):
        warnings.append(
            f"First-response target already breached by "
            f"{sla['breach_by_minutes']:.0f} minutes."
        )
    if severity == "P1":
        warnings.append("P1 severity: policy requires immediate escalation.")

    preview = {
        "kind": KIND_ESCALATION,
        "ticket_id": ticket["ticket_id"],
        "account": f"{account['account_name']} ({account['account_id']})" if account else None,
        "subject": ticket["subject"],
        "severity": severity,
        "reason": payload.get("reason") or f"{severity} per current support policy",
        "sla_target": sla.get("target_text"),
        "elapsed": _humanise(sla.get("elapsed_minutes")),
        "breached": sla.get("breached"),
        "assign_to": payload.get("assign_to") or "support on-call",
        "notify": (account["csm"] if account else None),
        "sources": sources,
    }
    return preview, warnings


def _follow_up_preview(
    conn: sqlite3.Connection,
    ticket: sqlite3.Row | None,
    account_id: str | None,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    account = _account_row(conn, account_id)
    amount = payload.get("credit_amount_inr")

    if amount is not None:
        approval = calc.evaluate_credit_amount(conn, account_id, amount)
        if approval["needs_manager_approval"]:
            warnings.append(
                f"Credit of INR {amount} exceeds the INR "
                f"{approval['threshold_inr']} manager-approval threshold."
            )

    preview = {
        "kind": KIND_FOLLOW_UP,
        "ticket_id": ticket["ticket_id"] if ticket else payload.get("ticket_id"),
        "order_id": payload.get("order_id"),
        "account": f"{account['account_name']} ({account['account_id']})" if account else None,
        "title": payload.get("title") or "Follow-up task",
        "details": payload.get("details"),
        "credit_amount_inr": amount,
        "due": payload.get("due"),
        "assign_to": payload.get("assign_to") or "support queue",
        "sources": payload.get("sources") or [],
    }
    return preview, warnings


def _ticket_update_preview(
    conn: sqlite3.Connection, ticket: sqlite3.Row, payload: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    changes = payload.get("changes") or {}
    allowed = {"status", "severity", "assigned_to", "note"}
    rejected = sorted(set(changes) - allowed)
    warnings = (
        [f"These fields cannot be changed by this system: {', '.join(rejected)}"]
        if rejected
        else []
    )
    preview = {
        "kind": KIND_TICKET_UPDATE,
        "ticket_id": ticket["ticket_id"],
        "subject": ticket["subject"],
        "current": {
            "status": ticket["status"],
            "assigned_to": ticket["assigned_to"],
        },
        "changes": {key: value for key, value in changes.items() if key in allowed},
        "sources": payload.get("sources") or [],
    }
    return preview, warnings


def _humanise(minutes: float | None) -> str | None:
    if minutes is None:
        return None
    hours, remainder = divmod(int(round(minutes)), 60)
    return f"{hours}h{remainder:02d}m" if hours else f"{remainder}m"


def propose_action(
    conn: sqlite3.Connection,
    session: Session,
    *,
    kind: str,
    ticket_id: str | None = None,
    account_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Phase one. Builds a preview, stores it against a token, writes nothing."""
    payload = dict(payload or {})
    if kind not in KINDS:
        return {
            "ok": False,
            "error": f"unknown action kind {kind!r}",
            "supported_kinds": list(KINDS),
        }

    scope = session.account_id or account_id
    ticket = _ticket_row(conn, ticket_id, session.account_id)
    if ticket_id and ticket is None:
        # Either it does not exist or it is not this caller's. Same answer.
        return {"ok": False, "error": "ticket not found", "ticket_id": ticket_id}
    if ticket is not None:
        scope = ticket["account_id"]

    if kind == KIND_ESCALATION:
        if ticket is None:
            return {"ok": False, "error": "an escalation needs a ticket_id"}
        preview, warnings = _escalation_preview(conn, ticket, payload)
    elif kind == KIND_TICKET_UPDATE:
        if ticket is None:
            return {"ok": False, "error": "a ticket update needs a ticket_id"}
        preview, warnings = _ticket_update_preview(conn, ticket, payload)
    else:
        preview, warnings = _follow_up_preview(conn, ticket, scope, payload)

    if not session.is_internal:
        # Internal routing fields are not the customer's business, and the gate
        # strips columns rather than preview keys -- so the preview trims itself.
        for internal_field in INTERNAL_ONLY_PREVIEW_FIELDS:
            preview.pop(internal_field, None)

    token = secrets.token_urlsafe(24)
    proposal = Proposal(
        token=token,
        session_id=session.session_id,
        kind=kind,
        ticket_id=ticket["ticket_id"] if ticket is not None else payload.get("ticket_id"),
        account_id=scope,
        payload=payload,
        preview=preview,
        warnings=warnings,
        created_monotonic=time.monotonic(),
        expires_at_display=f"{CONFIRM_TOKEN_TTL_SECONDS // 60} minutes from now",
        fingerprint=_fingerprint(kind, ticket_id, payload),
    )
    STORE.put(proposal)

    return {
        "ok": True,
        "requires_confirmation": True,
        "token": token,
        "kind": kind,
        "preview": preview,
        "warnings": warnings,
        "expires_in_seconds": CONFIRM_TOKEN_TTL_SECONDS,
        "expires_at": proposal.expires_at_display,
        "note": (
            "Nothing has been written. This proposal executes only when the user "
            "confirms it, and only through POST /confirm."
        ),
    }


def cancel_proposal(session: Session, token: str) -> dict[str, Any]:
    proposal = STORE.get(token)
    if proposal is None:
        return {"ok": False, "error": "unknown token"}
    if proposal.session_id != session.session_id:
        return {"ok": False, "error": "token does not belong to this session"}
    proposal.cancelled = True
    return {"ok": True, "status": "cancelled", "token": token}


def execute_confirmed(
    conn: sqlite3.Connection,
    session: Session,
    token: str,
    *,
    edited_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Phase two. The only function in the system that writes an action.

    Deliberately not exported through the tool registry -- see the module
    docstring.
    """
    proposal = STORE.get(token)
    if proposal is None:
        return {"ok": False, "error": "unknown or expired token"}
    if proposal.session_id != session.session_id:
        return {"ok": False, "error": "token does not belong to this session"}
    if proposal.used:
        return {"ok": False, "error": "token already used"}
    if proposal.cancelled:
        return {"ok": False, "error": "proposal was cancelled"}
    if proposal.is_expired():
        return {"ok": False, "error": "token expired"}
    if edited_payload is not None and _fingerprint(
        proposal.kind, proposal.ticket_id, edited_payload
    ) != proposal.fingerprint:
        return {
            "ok": False,
            "error": "proposal was edited; request a fresh preview before confirming",
        }
    if session.role != "internal" and proposal.kind != KIND_ESCALATION:
        # Customers may ask for their own ticket to be escalated. Everything
        # else is staff-only.
        return {"ok": False, "error": "customers may only confirm escalations"}

    action_id = _next_action_id(conn, proposal.kind)
    created_at = snapshot_now().isoformat()
    conn.execute(
        "INSERT INTO actions (action_id, kind, ticket_id, account_id, payload_json, "
        "created_by, created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'executed')",
        (
            action_id,
            proposal.kind,
            proposal.ticket_id,
            proposal.account_id,
            json.dumps({"payload": proposal.payload, "preview": proposal.preview}),
            session.user_name,
            created_at,
        ),
    )
    conn.commit()
    proposal.used = True

    return {
        "ok": True,
        "action_id": action_id,
        "kind": proposal.kind,
        "ticket_id": proposal.ticket_id,
        "account_id": proposal.account_id,
        "created_at": created_at,
        "status": "executed",
        "preview": proposal.preview,
    }
