"""Tool schemas exposed to the model.

Six narrow tools rather than two broad ones. A model picks correctly between
tools whose names describe one job and badly between tools that take a mode
argument -- and a narrow tool is also a narrower thing to have to secure.

`execute_action` is deliberately absent. So is `account_id`, in customer mode:
`schema_for_role` strips it, so the parameter a customer session must not choose
is not merely ignored, it is never offered.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.session import Session
from app.tools import actions as actions_module
from app.tools import calc, data, docs

ACCOUNT_ID_PROPERTY = {
    "account_id": {
        "type": "string",
        "description": "Account to scope the query to, e.g. ACCT-003. Internal callers only.",
    }
}


@dataclass
class ToolSpec:
    name: str
    description: str
    properties: dict[str, Any]
    required: list[str] = field(default_factory=list)
    account_scoped: bool = False
    requires_internal: bool = False
    requires_account: bool = False
    fn: Callable[..., Any] | None = None
    needs_session: bool = False

    def schema_for_role(self, role: str) -> dict[str, Any]:
        properties = dict(self.properties)
        if self.account_scoped and role == "internal":
            properties.update(ACCOUNT_ID_PROPERTY)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": list(self.required),
            },
        }


def _search_documents(
    conn: sqlite3.Connection,
    *,
    query: str,
    topics: list[str] | None = None,
    include_deprecated: bool = False,
    account_id: str | None = None,
    role: str = "customer",
) -> dict[str, Any]:
    return docs.search_documents(
        conn,
        query,
        topics=topics,
        include_deprecated=include_deprecated,
        account_id=account_id,
        role=role,
    )


def _compute(
    conn: sqlite3.Connection,
    *,
    kind: str,
    account_id: str | None = None,
    order_id: str | None = None,
    ticket_id: str | None = None,
    severity: str | None = None,
    hours_past_window_end: float | None = None,
    carrier_fault: bool | None = None,
    customer_fault: bool | None = None,
    shipment_fee_inr: float | None = None,
) -> dict[str, Any]:
    """One tool with a discriminator rather than three.

    The three calculations share their inputs and their citation shape, and in
    testing a single `compute` with an explicit `kind` was chosen more reliably
    than three similarly-named tools.
    """
    if kind == "cancellation_fee":
        if not order_id:
            return {"error": "cancellation_fee needs an order_id"}
        return calc.cancellation_fee(conn, order_id, account_id=account_id)

    if kind == "service_credit":
        return calc.service_credit(
            conn,
            order_id,
            account_id=account_id,
            hours_past_window_end=hours_past_window_end,
            carrier_fault=carrier_fault,
            customer_fault=customer_fault,
            shipment_fee_inr=shipment_fee_inr,
        )

    if kind == "sla_status":
        if not ticket_id:
            return {"error": "sla_status needs a ticket_id"}
        return calc.sla_status(conn, ticket_id, severity=severity, account_id=account_id)

    return {
        "error": f"unknown compute kind {kind!r}",
        "supported": ["cancellation_fee", "service_credit", "sla_status"],
    }


def _propose_action(
    conn: sqlite3.Connection,
    session: Session,
    *,
    kind: str,
    ticket_id: str | None = None,
    account_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return actions_module.propose_action(
        conn, session, kind=kind, ticket_id=ticket_id, account_id=account_id, payload=payload
    )


def _list_signals(conn: sqlite3.Connection, **_: Any) -> dict[str, Any]:
    # Imported lazily: the signals package depends on the tools package.
    from app.signals.detect import detect_signals

    return {"signals": [signal.to_dict() for signal in detect_signals(conn)]}


REGISTRY: dict[str, ToolSpec] = {
    "search_documents": ToolSpec(
        name="search_documents",
        description=(
            "Search ParcelPilot's policy, SOP, product and contract documents. "
            "Returns clauses with their authority tier, so a tier-1 signed "
            "agreement can be recognised as outranking the tier-2 SOP. Always "
            "search before stating an entitlement."
        ),
        properties={
            "query": {"type": "string", "description": "What to look for, in plain language."},
            "topics": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "cancellation",
                        "service_credit",
                        "sla",
                        "severity",
                        "escalation",
                        "plan_capability",
                        "known_issue",
                        "account_contact",
                        "source_precedence",
                    ],
                },
                "description": "Optional topic filter.",
            },
            "include_deprecated": {
                "type": "boolean",
                "description": (
                    "Internal only. Include superseded policy versions, clearly "
                    "flagged, for questions about what we used to promise."
                ),
            },
        },
        required=["query"],
        account_scoped=True,
        fn=_search_documents,
    ),
    "lookup_orders": ToolSpec(
        name="lookup_orders",
        description=(
            "Look up shipment orders with derived fields already computed: "
            "minutes from booking to the cancellation request, hours past the "
            "pickup window, and whether the status permits cancellation. Check "
            "the order status before applying any cancellation rule."
        ),
        properties={
            "order_id": {"type": "string", "description": "e.g. ORD-2001"},
            "status": {"type": "string", "description": "DRAFT, BOOKED, PICKED_UP or DELIVERED"},
            "carrier": {"type": "string"},
            "limit": {"type": "integer"},
        },
        account_scoped=True,
        fn=data.lookup_orders,
    ),
    "lookup_tickets": ToolSpec(
        name="lookup_tickets",
        description=(
            "Look up support tickets. Any historical resolution comes back "
            "wrapped and marked as context only -- past answers may be wrong and "
            "must never be repeated as policy without checking a current source."
        ),
        properties={
            "ticket_id": {"type": "string", "description": "e.g. TKT-505"},
            "status": {"type": "string", "description": "open or closed"},
            "since": {"type": "string", "description": "ISO timestamp lower bound"},
            "limit": {"type": "integer"},
        },
        account_scoped=True,
        fn=data.lookup_tickets,
    ),
    "lookup_account": ToolSpec(
        name="lookup_account",
        description=(
            "Plan, status, and which contract file governs this account. Use it "
            "to find out whether a signed agreement exists before assuming one "
            "does -- two accounts on the same plan can have different terms."
        ),
        properties={},
        account_scoped=True,
        fn=data.lookup_account,
    ),
    "compute": ToolSpec(
        name="compute",
        description=(
            "Run a deterministic calculation. You must use this for every fee, "
            "credit amount, elapsed time and SLA breach -- do not do arithmetic "
            "or date maths yourself. Returns the source clause it applied. "
            "kind=cancellation_fee needs order_id; kind=service_credit takes an "
            "order_id or a stated hypothetical; kind=sla_status needs ticket_id."
        ),
        properties={
            "kind": {
                "type": "string",
                "enum": ["cancellation_fee", "service_credit", "sla_status"],
            },
            "order_id": {"type": "string"},
            "ticket_id": {"type": "string"},
            "severity": {"type": "string", "enum": ["P1", "P2", "P3"]},
            "hours_past_window_end": {
                "type": "number",
                "description": "Hypothetical service_credit input.",
            },
            "carrier_fault": {"type": "boolean"},
            "customer_fault": {"type": "boolean"},
            "shipment_fee_inr": {"type": "number"},
        },
        required=["kind"],
        account_scoped=True,
        fn=_compute,
    ),
    "propose_action": ToolSpec(
        name="propose_action",
        description=(
            "Propose a state-changing action for the user to confirm: an "
            "escalation, a ticket update, or a follow-up task. This writes "
            "nothing. It returns a preview and a token; the user confirms in the "
            "interface. Use it whenever an answer requires something to happen "
            "rather than merely be explained."
        ),
        properties={
            "kind": {
                "type": "string",
                "enum": ["escalation", "ticket_update", "follow_up_task"],
            },
            "ticket_id": {"type": "string"},
            "payload": {
                "type": "object",
                "description": (
                    "Fields for the action: reason, severity, assign_to, title, "
                    "details, due, credit_amount_inr, order_id, changes, sources."
                ),
            },
        },
        required=["kind"],
        account_scoped=True,
        fn=_propose_action,
        needs_session=True,
    ),
    "list_signals": ToolSpec(
        name="list_signals",
        description=(
            "Internal only. The proactive issue queue: SLA breaches, repeated "
            "root causes, carrier concentration, cancellation spikes, "
            "multi-account impact, and stale past guidance."
        ),
        properties={},
        requires_internal=True,
        fn=_list_signals,
    ),
}


def schemas_for(role: str) -> list[dict[str, Any]]:
    return [
        spec.schema_for_role(role)
        for spec in REGISTRY.values()
        if not (spec.requires_internal and role != "internal")
    ]
