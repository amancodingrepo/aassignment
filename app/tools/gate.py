"""The scope gate.

Nothing calls a tool module directly. Every call comes through `call_tool`,
which decides three things before the tool runs and one after:

* may this role call this tool at all;
* what account is this call scoped to -- injected from the session, never taken
  from the model;
* is this caller allowed the flags they passed;
* and which fields must be removed from the result before it is shown.

The injection is an overwrite, not a default. If the model supplies
`account_id="ACCT-001"` while the session is ACCT-002, the value is replaced,
the call returns the caller's own data, and the attempt lands in `audit_log`.
Combined with `account_id` being absent from the customer-mode schemas, a
well-behaved model never sends it and a jailbroken one gains nothing by trying.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.config import snapshot_now
from app.session import Session
from app.tools.registry import REGISTRY, ToolSpec

# From 02_DATA_MODEL.md. Note this strips the *columns*; the same fact reaching
# a customer through their own contract is legitimate and is not touched -- see
# eval case E4. Scope is a property of the source, not of the string.
INTERNAL_ONLY_FIELDS = {"csm", "assigned_to", "notes"}

INTERNAL_ONLY_ARGS = {"include_deprecated"}


class GateDenial(dict):
    """A denial is a normal result, not an exception.

    The model sees an empty or refused result and carries on, which is what
    should happen: the answer to "show me another account's tickets" is nothing
    at all, delivered calmly.
    """


def audit(
    conn: sqlite3.Connection,
    session: Session,
    tool: str,
    args: dict[str, Any],
    *,
    allowed: bool,
    denial_reason: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (ts, session_role, session_account, tool, args_json, "
        "allowed, denial_reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            snapshot_now().isoformat(),
            session.role,
            session.account_id,
            tool,
            json.dumps(args, default=str),
            int(allowed),
            denial_reason,
        ),
    )
    conn.commit()


def deny(
    conn: sqlite3.Connection,
    session: Session,
    tool: str,
    args: dict[str, Any],
    reason: str,
) -> GateDenial:
    audit(conn, session, tool, args, allowed=False, denial_reason=reason)
    return GateDenial({"denied": True, "reason": reason, "tool": tool})


def strip_internal_fields(value: Any, role: str) -> Any:
    if role == "internal":
        return value
    if isinstance(value, dict):
        return {
            key: strip_internal_fields(item, role)
            for key, item in value.items()
            if key not in INTERNAL_ONLY_FIELDS
        }
    if isinstance(value, list):
        return [strip_internal_fields(item, role) for item in value]
    return value


def _prepare_args(
    session: Session, spec: ToolSpec, args: dict[str, Any]
) -> tuple[dict[str, Any], list[str], str | None]:
    """Returns (args, warnings, audit_note)."""
    prepared = dict(args)
    warnings: list[str] = []
    audit_note: str | None = None

    if not session.is_internal:
        rejected = sorted(set(prepared) & INTERNAL_ONLY_ARGS)
        for name in rejected:
            if prepared.pop(name, None):
                warnings.append(
                    f"{name} is an internal-only option and was ignored."
                )
                audit_note = "internal_only_argument_rejected"

    if spec.account_scoped:
        if session.role == "customer":
            attempted = prepared.get("account_id")
            if attempted and attempted != session.account_id:
                audit_note = "account_scope_override"
            prepared["account_id"] = session.account_id
        elif spec.requires_account and not prepared.get("account_id"):
            return prepared, warnings, "internal_caller_must_name_an_account"

    return prepared, warnings, audit_note


def call_tool(
    conn: sqlite3.Connection,
    session: Session,
    name: str,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    args = dict(args or {})
    spec = REGISTRY.get(name)

    if spec is None:
        return deny(conn, session, name, args, "unknown tool")

    if spec.requires_internal and not session.is_internal:
        return deny(conn, session, name, args, "internal-only tool")

    prepared, warnings, audit_note = _prepare_args(session, spec, args)
    if audit_note == "internal_caller_must_name_an_account":
        return deny(conn, session, name, args, audit_note)

    call_kwargs = dict(prepared)
    if name == "search_documents":
        call_kwargs["role"] = session.role

    try:
        if spec.needs_session:
            result = spec.fn(conn, session, **call_kwargs)
        else:
            result = spec.fn(conn, **call_kwargs)
    except TypeError as error:
        # A malformed tool call from the model is a normal event, not a crash.
        audit(conn, session, name, args, allowed=False, denial_reason="bad_arguments")
        return GateDenial(
            {"denied": True, "reason": f"invalid arguments for {name}: {error}", "tool": name}
        )

    result = strip_internal_fields(result, session.role)
    if warnings:
        existing = list(result.get("warnings") or []) if isinstance(result, dict) else []
        if isinstance(result, dict):
            result["warnings"] = existing + warnings

    # The original args are audited, not the rewritten ones, so the attempted
    # value survives in the log.
    audit(conn, session, name, args, allowed=True, denial_reason=audit_note)
    return result
