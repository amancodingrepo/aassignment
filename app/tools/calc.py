"""Deterministic calculators.

The model chooses *which* rule applies and explains it. It never works out a
number. Every function here returns the source clause it applied, so the
citation chain attached to an answer is machine-generated rather than something
the model asserted and might have got wrong.

Each calculator is run twice: once with the full authority ladder, and once with
the account's contract removed. Comparing the two outcomes is how conflicts are
detected. That is deliberately outcome-level rather than parameter-level -- a
contract that waives a fee and an SOP that charges one share no parameter names,
but they plainly disagree, and a customer deserves to be told which won.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.authority import (
    TIER_SOP,
    Clause,
    RuleResolution,
    resolve_rule,
    row_to_clause,
    sla_targets_for,
)
from app.config import (
    BUSINESS_DAY_END_HOUR,
    BUSINESS_DAY_START_HOUR,
    BUSINESS_DAYS,
    BUSINESS_HOURS_PER_DAY,
    IST,
    snapshot_now,
)

# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------


def business_minutes_between(start: datetime, end: datetime) -> float:
    """Minutes of business time between two instants.

    The pack states half its targets in "business hours" and "business days" and
    never defines either, and the snapshot day is a Sunday -- so this is an
    assumption made visible rather than a fact. The calendar lives in config.py
    and the answer object reports which basis was used.
    """
    if end <= start:
        return 0.0

    start = start.astimezone(IST)
    end = end.astimezone(IST)
    total = 0.0
    cursor = start.date()
    while cursor <= end.date():
        if cursor.weekday() in BUSINESS_DAYS:
            day_open = datetime(
                cursor.year, cursor.month, cursor.day, BUSINESS_DAY_START_HOUR, tzinfo=IST
            )
            day_close = datetime(
                cursor.year, cursor.month, cursor.day, BUSINESS_DAY_END_HOUR, tzinfo=IST
            )
            overlap_start = max(start, day_open)
            overlap_end = min(end, day_close)
            if overlap_end > overlap_start:
                total += (overlap_end - overlap_start).total_seconds() / 60
        cursor += timedelta(days=1)
    return total


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


# --------------------------------------------------------------------------
# Row access. Scope is enforced here as well as at the gate, so there is no
# path to a cross-account number even for an internal caller who forgot to say
# which account they meant.
# --------------------------------------------------------------------------


def _order(conn: sqlite3.Connection, order_id: str, account_id: str | None) -> sqlite3.Row | None:
    if account_id:
        return conn.execute(
            "SELECT * FROM orders WHERE order_id = ? AND account_id = ?",
            (order_id, account_id),
        ).fetchone()
    return conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()


def _ticket(conn: sqlite3.Connection, ticket_id: str, account_id: str | None) -> sqlite3.Row | None:
    if account_id:
        return conn.execute(
            "SELECT * FROM tickets WHERE ticket_id = ? AND account_id = ?",
            (ticket_id, account_id),
        ).fetchone()
    return conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()


def _account(conn: sqlite3.Connection, account_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
    ).fetchone()


def _without_contract(resolution: RuleResolution) -> RuleResolution:
    """The same rule with tier-1 clauses removed: what the default policy says."""
    baseline = [clause for clause in resolution.clauses if clause.tier >= TIER_SOP]
    return RuleResolution(rule=resolution.rule, account_id=None, clauses=baseline)


def _source(clause: Clause | None) -> dict[str, Any] | None:
    return clause.to_source() if clause else None


# --------------------------------------------------------------------------
# Cancellation fee
# --------------------------------------------------------------------------


@dataclass
class _CancellationOutcome:
    cancellable: bool
    fee_inr: int | None
    rule_applied: str
    clause: Clause | None
    blocked: dict[str, Any] | None = None
    waiver_applied: bool = False


def _cancellation_outcome(
    status: str, minutes_since_booking: float | None, resolution: RuleResolution
) -> _CancellationOutcome:
    status_rules = resolution.param("status_rules") or {}
    status_clause = resolution.clause_supplying("status_rules")
    rule = status_rules.get(status)

    if rule is None:
        return _CancellationOutcome(
            cancellable=False,
            fee_inr=None,
            rule_applied="unknown_status",
            clause=status_clause,
            blocked={"reason": "unknown_status", "status": status},
        )

    # Status is checked before any waiver. A contract that waives cancellation
    # fees says nothing about whether a picked-up parcel may be cancelled, and
    # applying the waiver first is the single most common way to get A4 wrong.
    if not rule.get("cancellable"):
        return _CancellationOutcome(
            cancellable=False,
            fee_inr=None,
            rule_applied="status_not_cancellable",
            clause=status_clause,
            blocked={"reason": rule.get("reason"), "status": status},
        )

    if rule.get("fee") == "free":
        return _CancellationOutcome(
            cancellable=True,
            fee_inr=0,
            rule_applied="status_free",
            clause=status_clause,
        )

    waiver_clause = resolution.clause_supplying("waives_cancellation_fee")
    waives = bool(resolution.param("waives_cancellation_fee"))
    if waives and waiver_clause is not None:
        applicable = waiver_clause.params.get("waiver_applies_to_statuses")
        if applicable is None or status in applicable:
            return _CancellationOutcome(
                cancellable=True,
                fee_inr=0,
                rule_applied="contract_waiver",
                clause=waiver_clause,
                waiver_applied=True,
            )

    free_window = resolution.param("free_window_minutes")
    fee_clause = resolution.clause_supplying("free_window_minutes")
    if free_window is None or minutes_since_booking is None:
        return _CancellationOutcome(
            cancellable=True,
            fee_inr=None,
            rule_applied="insufficient_information",
            clause=fee_clause or status_clause,
        )

    if minutes_since_booking <= free_window:
        return _CancellationOutcome(
            cancellable=True,
            fee_inr=0,
            rule_applied="within_free_window",
            clause=fee_clause,
        )

    return _CancellationOutcome(
        cancellable=True,
        fee_inr=int(resolution.param("late_fee_inr")),
        rule_applied="after_free_window",
        clause=resolution.clause_supplying("late_fee_inr") or fee_clause,
    )


def cancellation_fee(
    conn: sqlite3.Connection, order_id: str, account_id: str | None = None
) -> dict[str, Any]:
    """What it costs this account to cancel this order, and under which clause."""
    order = _order(conn, order_id, account_id)
    if order is None:
        # A clean miss. No hint that the order exists under another account.
        return {
            "found": False,
            "order_id": order_id,
            "fee_inr": None,
            "cancellable": None,
            "rule_applied": "order_not_found",
            "source": None,
            "sources": [],
            "conflicts": [],
        }

    owner = order["account_id"]
    resolution = resolve_rule(conn, "cancellation_fee", owner)
    baseline = _without_contract(resolution)

    booked_at = _parse(order["booked_at"])
    minutes = order["minutes_booked_to_cancel_request"]
    timing_basis = "cancellation_request"
    if minutes is None and booked_at is not None:
        # No request on file: price the cancellation as of the snapshot, which
        # is what "can we cancel this?" asked right now actually means.
        minutes = (snapshot_now() - booked_at).total_seconds() / 60
        timing_basis = "snapshot"

    outcome = _cancellation_outcome(order["status"], minutes, resolution)
    default_outcome = _cancellation_outcome(order["status"], minutes, baseline)

    conflicts = [conflict.to_dict() for conflict in resolution.conflicts]
    if (
        outcome.fee_inr != default_outcome.fee_inr
        or outcome.cancellable != default_outcome.cancellable
    ) and outcome.clause is not None:
        conflicts.insert(
            0,
            {
                "topic": "cancellation_fee",
                "winner": f"{outcome.clause.citation} (tier {outcome.clause.tier})",
                "loser": (
                    f"{default_outcome.clause.citation} (tier {default_outcome.clause.tier})"
                    if default_outcome.clause
                    else "default policy"
                ),
                "why": (
                    f"{outcome.clause.tier_label} sets the fee to "
                    f"{outcome.fee_inr} for {owner}; the default rule would have "
                    f"given {default_outcome.fee_inr}."
                ),
            },
        )

    waiver_clause = resolution.clause_supplying("waives_cancellation_fee")
    return {
        "found": True,
        "order_id": order["order_id"],
        "account_id": owner,
        "status": order["status"],
        "cancellable": outcome.cancellable,
        "fee_inr": outcome.fee_inr,
        "currency": "INR",
        "rule_applied": outcome.rule_applied,
        "blocked": outcome.blocked,
        "minutes_since_booking": minutes,
        "timing_basis": timing_basis,
        "free_window_minutes": resolution.param("free_window_minutes"),
        "waiver_available": bool(resolution.param("waives_cancellation_fee")),
        "waiver_applied": outcome.waiver_applied,
        "waiver_source": _source(waiver_clause) if resolution.param("waives_cancellation_fee") else None,
        "source": _source(outcome.clause),
        "sources": [clause.to_source() for clause in resolution.clauses],
        "default_outcome": {
            "fee_inr": default_outcome.fee_inr,
            "cancellable": default_outcome.cancellable,
            "rule_applied": default_outcome.rule_applied,
            "source": _source(default_outcome.clause),
        },
        "conflicts": conflicts,
    }


# --------------------------------------------------------------------------
# Service credit
# --------------------------------------------------------------------------


@dataclass
class _CreditOutcome:
    eligible: bool | str
    amount_inr: int | None
    rule_applied: str
    clause: Clause | None
    threshold_hours: float | None
    unknowns: list[str]
    amount_formula: str | None = None


def _credit_outcome(
    hours_past_window_end: float | None,
    carrier_fault: bool | None,
    customer_fault: bool | None,
    shipment_fee_inr: float | None,
    resolution: RuleResolution,
) -> _CreditOutcome:
    threshold = resolution.param("threshold_hours")
    threshold_clause = resolution.clause_supplying("threshold_hours")

    # SOP §3: do not promise a credit when carrier fault, pickup timing, or
    # customer fault is unknown. Unknown is a third state, not a soft "no".
    unknowns: list[str] = []
    if carrier_fault is None:
        unknowns.append("carrier_fault")
    if customer_fault is None:
        unknowns.append("customer_fault")
    if hours_past_window_end is None:
        unknowns.append("pickup_timing")
    if unknowns:
        return _CreditOutcome(
            eligible="unknown",
            amount_inr=None,
            rule_applied="insufficient_information",
            clause=resolution.clause_supplying("threshold_hours"),
            threshold_hours=threshold,
            unknowns=unknowns,
        )

    if customer_fault:
        return _CreditOutcome(
            eligible=False,
            amount_inr=None,
            rule_applied="customer_at_fault",
            clause=threshold_clause,
            threshold_hours=threshold,
            unknowns=[],
        )
    if not carrier_fault:
        return _CreditOutcome(
            eligible=False,
            amount_inr=None,
            rule_applied="carrier_not_at_fault",
            clause=threshold_clause,
            threshold_hours=threshold,
            unknowns=[],
        )
    if threshold is None or hours_past_window_end <= threshold:
        return _CreditOutcome(
            eligible=False,
            amount_inr=None,
            rule_applied="below_threshold",
            clause=threshold_clause,
            threshold_hours=threshold,
            unknowns=[],
        )

    fixed = resolution.param("fixed_credit_inr")
    if fixed is not None:
        return _CreditOutcome(
            eligible=True,
            amount_inr=int(fixed),
            rule_applied="contract_fixed_amount",
            clause=resolution.clause_supplying("fixed_credit_inr"),
            threshold_hours=threshold,
            unknowns=[],
        )

    cap = resolution.param("credit_cap_inr")
    pct = resolution.param("credit_pct_of_fee")
    amount_clause = resolution.clause_supplying("credit_cap_inr")
    formula = None
    if cap is not None and pct is not None:
        formula = f"min({int(cap)}, {pct:.0%} of shipment fee)"

    if shipment_fee_inr is None:
        # Eligible, but no honest number can be given without the fee.
        return _CreditOutcome(
            eligible=True,
            amount_inr=None,
            rule_applied="sop_default_amount",
            clause=amount_clause,
            threshold_hours=threshold,
            unknowns=["shipment_fee_inr"],
            amount_formula=formula,
        )

    amount = min(float(cap), float(pct) * float(shipment_fee_inr))
    return _CreditOutcome(
        eligible=True,
        amount_inr=int(round(amount)),
        rule_applied="sop_default_amount",
        clause=amount_clause,
        threshold_hours=threshold,
        unknowns=[],
        amount_formula=formula,
    )


def _monthly_credits_consumed(conn: sqlite3.Connection, account_id: str) -> int:
    """Credits already committed this snapshot month, from the actions table."""
    month_prefix = snapshot_now().strftime("%Y-%m")
    total = 0
    rows = conn.execute(
        "SELECT payload_json, created_at FROM actions "
        "WHERE account_id = ? AND status = 'executed'",
        (account_id,),
    ).fetchall()
    for row in rows:
        if not (row["created_at"] or "").startswith(month_prefix):
            continue
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        amount = payload.get("credit_amount_inr") or payload.get("amount_inr")
        if isinstance(amount, (int, float)):
            total += int(amount)
    return total


def evaluate_credit_amount(
    conn: sqlite3.Connection, account_id: str | None, amount_inr: float | None
) -> dict[str, Any]:
    """Does this amount need a manager? SOP §3, read from the clause."""
    resolution = resolve_rule(conn, "credit_approval", account_id)
    threshold = resolution.param("manager_approval_above_inr")
    clause = resolution.clause_supplying("manager_approval_above_inr")
    needs = amount_inr is not None and threshold is not None and amount_inr > threshold
    return {
        "amount_inr": amount_inr,
        "threshold_inr": threshold,
        "needs_manager_approval": bool(needs),
        "source": _source(clause),
    }


def service_credit(
    conn: sqlite3.Connection,
    order_id: str | None = None,
    *,
    account_id: str | None = None,
    hours_past_window_end: float | None = None,
    carrier_fault: bool | None = None,
    customer_fault: bool | None = None,
    shipment_fee_inr: float | None = None,
) -> dict[str, Any]:
    """Failed-pickup credit for a real order, or for a stated hypothetical.

    The hypothetical form exists because half the eval set asks "a pickup is
    three hours late, do I get a credit?" without naming an order, and answering
    that from prose rather than from the rule engine would put the arithmetic
    back in the model's hands.
    """
    order_row = None
    if order_id is not None:
        order_row = _order(conn, order_id, account_id)
        if order_row is None:
            return {
                "found": False,
                "order_id": order_id,
                "eligible": None,
                "amount_inr": None,
                "rule_applied": "order_not_found",
                "source": None,
                "sources": [],
                "conflicts": [],
                "unknowns": [],
            }
        account_id = order_row["account_id"]
        hours_past_window_end = order_row["hours_past_pickup_window_end"]
        carrier_fault = (
            None if order_row["carrier_fault"] is None else bool(order_row["carrier_fault"])
        )
        customer_fault = (
            None if order_row["customer_fault"] is None else bool(order_row["customer_fault"])
        )
        shipment_fee_inr = order_row["shipment_fee_inr"]

    resolution = resolve_rule(conn, "service_credit", account_id)
    baseline = _without_contract(resolution)

    outcome = _credit_outcome(
        hours_past_window_end, carrier_fault, customer_fault, shipment_fee_inr, resolution
    )
    default_outcome = _credit_outcome(
        hours_past_window_end, carrier_fault, customer_fault, shipment_fee_inr, baseline
    )

    approval = evaluate_credit_amount(conn, account_id, outcome.amount_inr)

    conflicts = [conflict.to_dict() for conflict in resolution.conflicts]
    contract_effect = None
    if outcome.eligible != default_outcome.eligible or outcome.amount_inr != default_outcome.amount_inr:
        if outcome.clause is not None and outcome.clause.tier < TIER_SOP:
            better = (outcome.eligible is True and default_outcome.eligible is not True) or (
                outcome.amount_inr is not None
                and default_outcome.amount_inr is not None
                and outcome.amount_inr > default_outcome.amount_inr
            )
            contract_effect = "more_generous" if better else "less_generous"
            conflicts.insert(
                0,
                {
                    "topic": "service_credit",
                    "winner": f"{outcome.clause.citation} (tier {outcome.clause.tier})",
                    "loser": (
                        f"{default_outcome.clause.citation} (tier {default_outcome.clause.tier})"
                        if default_outcome.clause
                        else "SOP default"
                    ),
                    "why": (
                        f"The signed agreement replaces the default failed-pickup "
                        f"terms for {account_id}: eligible={outcome.eligible}, "
                        f"amount={outcome.amount_inr}. Under the SOP default it "
                        f"would have been eligible={default_outcome.eligible}, "
                        f"amount={default_outcome.amount_inr}."
                    ),
                },
            )

    cap_clause = resolution.clause_supplying("monthly_aggregate_cap_inr")
    monthly_cap = resolution.param("monthly_aggregate_cap_inr")
    consumed = _monthly_credits_consumed(conn, account_id) if account_id else 0

    return {
        "found": True,
        "order_id": order_id,
        "account_id": account_id,
        "eligible": outcome.eligible,
        "amount_inr": outcome.amount_inr,
        "currency": "INR",
        "amount_formula": outcome.amount_formula,
        "rule_applied": outcome.rule_applied,
        "threshold_hours": outcome.threshold_hours,
        "hours_past_window_end": hours_past_window_end,
        "carrier_fault": carrier_fault,
        "customer_fault": customer_fault,
        "shipment_fee_inr": shipment_fee_inr,
        "unknowns": outcome.unknowns,
        "must_escalate": outcome.eligible == "unknown",
        "needs_manager_approval": approval["needs_manager_approval"],
        "approval_threshold_inr": approval["threshold_inr"],
        "approval_source": approval["source"],
        "monthly_cap_inr": monthly_cap,
        "monthly_cap_source": _source(cap_clause),
        "monthly_credits_consumed_inr": consumed,
        "monthly_cap_remaining_inr": (monthly_cap - consumed) if monthly_cap is not None else None,
        "source": _source(outcome.clause),
        "sources": [clause.to_source() for clause in resolution.clauses],
        "default_outcome": {
            "eligible": default_outcome.eligible,
            "amount_inr": default_outcome.amount_inr,
            "rule_applied": default_outcome.rule_applied,
            "source": _source(default_outcome.clause),
        },
        "contract_effect": contract_effect,
        "conflicts": conflicts,
    }


# --------------------------------------------------------------------------
# Severity and SLA
# --------------------------------------------------------------------------


def classify_severity(
    conn: sqlite3.Connection,
    ticket_id: str | None = None,
    *,
    text: str | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Severity from the policy's own indicators.

    Used by the signals detectors and the tests, which run with no model. In
    conversation the agent reads §2 and decides, because "no workaround" is a
    judgement rather than a string match -- but a detector that silently skipped
    severity would be worse than one that approximates it and says so.
    """
    if ticket_id is not None:
        ticket = _ticket(conn, ticket_id, account_id)
        if ticket is None:
            return {"severity": None, "found": False, "source": None}
        haystack = f"{ticket['subject'] or ''} {ticket['description'] or ''}"
        account_id = ticket["account_id"]
    else:
        haystack = text or ""

    resolution = resolve_rule(conn, "severity_classification", account_id)
    clause = resolution.clause_supplying("indicators")
    indicators = resolution.param("indicators") or {}
    lowered = haystack.lower()

    for severity in resolution.param("severity_levels", ["P1", "P2", "P3"]):
        for indicator in indicators.get(severity, []):
            if indicator.lower() in lowered:
                return {
                    "severity": severity,
                    "found": True,
                    "matched_indicator": indicator,
                    "basis": "policy_indicator",
                    "source": _source(clause),
                }

    return {
        "severity": "P3",
        "found": True,
        "matched_indicator": None,
        "basis": "default_when_no_indicator_matches",
        "source": _source(clause),
    }


def resolve_sla_target(
    conn: sqlite3.Connection,
    *,
    account_id: str | None,
    plan: str | None,
    severity: str,
    include_deprecated: bool = False,
) -> dict[str, Any]:
    """The governing first-response target, plus every candidate considered."""
    candidates = sla_targets_for(
        conn, account_id, plan, severity, include_deprecated=include_deprecated
    )
    listed = [
        {
            "doc_id": row["doc_id"],
            "chunk_id": row["chunk_id"],
            "tier": row["tier"],
            "status": row["status"],
            "scope": row["scope"],
            "plan": row["plan"],
            "target_text": row["target_text"],
            "target_minutes": row["target_minutes"],
        }
        for row in candidates
    ]
    if not candidates:
        return {
            "found": False,
            "severity": severity,
            "plan": plan,
            "target_text": None,
            "target_minutes": None,
            "business_hours_target": None,
            "business_days_target": None,
            "coverage": None,
            "source": None,
            "all_candidates": listed,
        }

    winner = candidates[0]
    same_tier = [row for row in candidates[1:] if row["tier"] == winner["tier"]]
    disagreeing = [row for row in same_tier if row["target_text"] != winner["target_text"]]

    chunk = conn.execute(
        "SELECT * FROM chunks WHERE chunk_id = ?", (winner["chunk_id"],)
    ).fetchone()
    source = row_to_clause(chunk).to_source() if chunk else {}
    source.update({"doc_id": winner["doc_id"], "tier": winner["tier"], "scope": winner["scope"]})

    return {
        "found": True,
        "severity": severity,
        "plan": plan,
        "target_text": winner["target_text"],
        "target_minutes": winner["target_minutes"],
        "business_hours_target": winner["business_hours"],
        "business_days_target": winner["business_days"],
        "coverage": winner["coverage"],
        "source": source,
        "same_tier_disagreement": [row["doc_id"] for row in disagreeing],
        "all_candidates": listed,
    }


def sla_status(
    conn: sqlite3.Connection,
    ticket_id: str,
    severity: str | None = None,
    *,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Elapsed against target for one ticket, at the snapshot."""
    ticket = _ticket(conn, ticket_id, account_id)
    if ticket is None:
        return {
            "found": False,
            "ticket_id": ticket_id,
            "breached": None,
            "source": None,
        }

    owner = ticket["account_id"]
    account = _account(conn, owner)
    plan = account["plan"] if account else None

    classified = None
    if severity is None:
        classified = classify_severity(conn, ticket_id)
        severity = classified["severity"]

    resolved = resolve_sla_target(
        conn, account_id=owner, plan=plan, severity=severity
    )

    created_at = _parse(ticket["created_at"])
    now = snapshot_now()
    elapsed_minutes = (now - created_at).total_seconds() / 60 if created_at else None
    business_elapsed = (
        business_minutes_between(created_at, now) if created_at else None
    )

    target_minutes = resolved["target_minutes"]
    basis = "wall_clock"
    comparable_elapsed = elapsed_minutes

    if target_minutes is None and resolved["business_hours_target"] is not None:
        target_minutes = resolved["business_hours_target"] * 60
        basis = "business_hours"
        comparable_elapsed = business_elapsed
    elif target_minutes is None and resolved["business_days_target"] is not None:
        target_minutes = resolved["business_days_target"] * BUSINESS_HOURS_PER_DAY * 60
        basis = "business_hours"
        comparable_elapsed = business_elapsed

    breached = None
    breach_by = None
    consumed_pct = None
    if target_minutes is not None and comparable_elapsed is not None:
        breached = comparable_elapsed > target_minutes
        breach_by = max(0.0, comparable_elapsed - target_minutes)
        consumed_pct = (comparable_elapsed / target_minutes) * 100 if target_minutes else None

    return {
        "found": True,
        "ticket_id": ticket["ticket_id"],
        "account_id": owner,
        "plan": plan,
        "severity": severity,
        "severity_basis": classified["basis"] if classified else "caller_supplied",
        "severity_source": classified["source"] if classified else None,
        "target_text": resolved["target_text"],
        "target_minutes": resolved["target_minutes"],
        "business_hours_target": resolved["business_hours_target"],
        "business_days_target": resolved["business_days_target"],
        "coverage": resolved["coverage"],
        "comparable_target_minutes": target_minutes,
        "basis": basis,
        "elapsed_minutes": elapsed_minutes,
        "business_elapsed_minutes": business_elapsed,
        "comparable_elapsed_minutes": comparable_elapsed,
        "breached": breached,
        "breach_by_minutes": breach_by,
        "consumed_pct": consumed_pct,
        "created_at": ticket["created_at"],
        "measured_at": now.isoformat(),
        "source": resolved["source"],
        "all_candidates": resolved["all_candidates"],
        "same_tier_disagreement": resolved.get("same_tier_disagreement", []),
    }
