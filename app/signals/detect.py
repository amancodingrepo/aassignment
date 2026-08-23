"""Proactive issue detection.

Deterministic rules, not clustering. With seven tickets and six orders an
embedding-based anomaly detector would be theatre: it would fit noise, and no
alert it raised could be explained to the person expected to act on it. Rules
that name their own trigger are testable and produce alerts a human can act on
immediately.

At ParcelPilot's stated real volume -- hundreds of requests weekly -- detector 2
is where embedding-based clustering earns its place, for near-duplicate
complaint detection. The threshold to gate it behind is in the product note.

Every signal carries the evidence rows that produced it and the arithmetic
behind its rank. An unexplained priority score is exactly the confident opacity
this system exists to avoid.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from app.authority import load_clauses, resolve_rule
from app.config import snapshot_now
from app.tools import calc

# Detector 4 has no historical baseline to learn from -- the pack is a single
# frozen day. This is an assumption, stated here and labelled as assumed in the
# interface rather than presented as a learned norm.
ASSUMED_CANCELLATIONS_PER_DAY = 1.0
CANCELLATION_SPIKE_MULTIPLE = 2.0
CANCELLATION_CLUSTER_WINDOW = timedelta(hours=2)

AT_RISK_THRESHOLD_PCT = 75.0

SEVERITY_WEIGHT = {"P1": 3.0, "P2": 2.0, "P3": 1.0}
KIND_SEVERITY = {
    "sla_breach": "P1",
    "sla_at_risk": "P2",
    "repeated_root_cause": "P2",
    "carrier_concentration": "P2",
    "cancellation_spike": "P2",
    "multi_account_impact": "P1",
    "stale_guidance": "P2",
}


@dataclass
class Signal:
    id: str
    kind: str
    severity: str
    title: str
    detail: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    affected_accounts: list[str] = field(default_factory=list)
    recommended_action: str = ""
    seed_query: str = ""
    computed_at: str = ""
    rank_score: float = 0.0
    rank_terms: dict[str, float] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "evidence": self.evidence,
            "affected_accounts": self.affected_accounts,
            "recommended_action": self.recommended_action,
            "seed_query": self.seed_query,
            "computed_at": self.computed_at,
            "rank_score": round(self.rank_score, 3),
            "rank_terms": {key: round(value, 3) for key, value in self.rank_terms.items()},
            "sources": self.sources,
        }


def _rank(signal: Signal, breach_magnitude: float = 1.0) -> None:
    """severity x account impact x breach magnitude, with the terms kept visible."""
    severity_term = SEVERITY_WEIGHT.get(signal.severity, 1.0)
    impact_term = float(max(1, len(signal.affected_accounts)))
    signal.rank_terms = {
        "severity": severity_term,
        "account_impact": impact_term,
        "breach_magnitude": round(breach_magnitude, 3),
    }
    signal.rank_score = severity_term * impact_term * breach_magnitude


def _open_tickets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM tickets WHERE LOWER(status) = 'open' ORDER BY created_at"
    ).fetchall()


def _account_name(conn: sqlite3.Connection, account_id: str) -> str:
    row = conn.execute(
        "SELECT account_name FROM accounts WHERE account_id = ?", (account_id,)
    ).fetchone()
    return row["account_name"] if row else account_id


# --------------------------------------------------------------------------
# 1. SLA breach and approaching breach
# --------------------------------------------------------------------------


def detect_sla_breaches(conn: sqlite3.Connection) -> list[Signal]:
    signals: list[Signal] = []
    for ticket in _open_tickets(conn):
        classified = calc.classify_severity(conn, ticket["ticket_id"])
        status = calc.sla_status(conn, ticket["ticket_id"], severity=classified["severity"])
        if not status.get("found") or status.get("consumed_pct") is None:
            continue

        consumed = status["consumed_pct"]
        if consumed <= AT_RISK_THRESHOLD_PCT:
            continue

        breached = bool(status["breached"])
        kind = "sla_breach" if breached else "sla_at_risk"
        account_id = ticket["account_id"]
        signal = Signal(
            id=f"SIG-{kind.upper()}-{ticket['ticket_id']}",
            kind=kind,
            severity=classified["severity"],
            title=(
                f"{ticket['ticket_id']} ({_account_name(conn, account_id)}): "
                f"{classified['severity']} "
                + ("breached" if breached else "approaching breach")
            ),
            detail=(
                f"Target {status['target_text']} from {status['source'].get('citation') or status['source'].get('doc_id')}. "
                f"Elapsed {status['elapsed_minutes']:.0f} minutes"
                + (
                    f", breached by {status['breach_by_minutes']:.0f} minutes."
                    if breached
                    else f", {consumed:.0f}% of target consumed."
                )
            ),
            evidence=[
                {
                    "ticket_id": ticket["ticket_id"],
                    "subject": ticket["subject"],
                    "created_at": ticket["created_at"],
                    "severity": classified["severity"],
                    "severity_basis": classified.get("matched_indicator"),
                    "target": status["target_text"],
                    "elapsed_minutes": round(status["elapsed_minutes"], 1),
                    "breach_by_minutes": round(status["breach_by_minutes"] or 0, 1),
                    "basis": status["basis"],
                }
            ],
            affected_accounts=[account_id],
            recommended_action=(
                "Escalate now and state the breach to the customer."
                if breached
                else "Respond before the target elapses."
            ),
            seed_query=f"Why is {ticket['ticket_id']} breaching, and should it be escalated?",
            computed_at=snapshot_now().isoformat(),
            sources=[status["source"]] if status.get("source") else [],
        )
        magnitude = max(1.0, consumed / 100.0)
        _rank(signal, magnitude)
        signals.append(signal)
    return signals


# --------------------------------------------------------------------------
# 2. Same root cause across tickets
# --------------------------------------------------------------------------


def _known_issues(conn: sqlite3.Connection) -> list[Any]:
    return [
        clause
        for clause in load_clauses(conn)
        if clause.flags.get("issue_id") and clause.status != "deprecated"
    ]


def _matches_issue(clause: Any, text: str) -> str | None:
    lowered = text.lower()
    for keyword in clause.flags.get("match_keywords", []):
        if keyword.lower() in lowered:
            return keyword
    return None


def detect_repeated_root_cause(conn: sqlite3.Connection) -> list[Signal]:
    signals: list[Signal] = []
    tickets = conn.execute("SELECT * FROM tickets ORDER BY created_at").fetchall()

    for clause in _known_issues(conn):
        if clause.flags.get("do_not_apply_to_new_incidents"):
            # KI-176 is resolved. The guide forbids using it to explain new
            # incidents, so it is not allowed to generate alerts either.
            continue

        linked: list[dict[str, Any]] = []
        for ticket in tickets:
            haystack = f"{ticket['subject']} {ticket['description']}"
            keyword = _matches_issue(clause, haystack)
            if not keyword:
                continue
            linked.append(
                {
                    "ticket_id": ticket["ticket_id"],
                    "account_id": ticket["account_id"],
                    "status": ticket["status"],
                    "subject": ticket["subject"],
                    "matched_on": keyword,
                }
            )

        if len(linked) < 2:
            continue

        accounts = sorted({item["account_id"] for item in linked})
        open_ids = [item["ticket_id"] for item in linked if item["status"] == "open"]
        closed_ids = [item["ticket_id"] for item in linked if item["status"] != "open"]
        issue_id = clause.flags["issue_id"]

        signal = Signal(
            id=f"SIG-ROOTCAUSE-{issue_id}",
            kind="repeated_root_cause",
            severity=KIND_SEVERITY["repeated_root_cause"],
            title=f"{issue_id} has {len(linked)} linked tickets",
            detail=(
                f"{clause.section_title}. Open: {', '.join(open_ids) or 'none'}. "
                f"Previously reported: {', '.join(closed_ids) or 'none'}. "
                + (
                    "A recurrence against the same account, not an isolated report."
                    if closed_ids and set(accounts) == {linked[0]["account_id"]}
                    else ""
                )
            ),
            evidence=linked,
            affected_accounts=accounts,
            recommended_action=(
                f"Link these tickets to {issue_id} and give the documented workaround."
            ),
            seed_query=f"What is {issue_id}, which tickets does it explain, and what is the workaround?",
            computed_at=snapshot_now().isoformat(),
            sources=[clause.to_source()],
        )
        _rank(signal, 1.0 + 0.25 * len(linked))
        signals.append(signal)
    return signals


# --------------------------------------------------------------------------
# 3. Carrier concentration
# --------------------------------------------------------------------------


def detect_carrier_concentration(conn: sqlite3.Connection) -> list[Signal]:
    problems: dict[str, list[dict[str, Any]]] = {}

    known_issue_by_carrier = {
        clause.flags["carrier"]: clause
        for clause in _known_issues(conn)
        if clause.flags.get("carrier")
    }

    orders = conn.execute(
        "SELECT * FROM orders WHERE status != 'DELIVERED' OR carrier_fault = 1"
    ).fetchall()
    for order in orders:
        reasons = []
        if order["cancellation_requested_at"]:
            reasons.append("cancellation requested")
        if order["carrier_fault"] == 1:
            reasons.append("carrier accepted fault")
        if (order["hours_past_pickup_window_end"] or 0) > 0 and not order["pickup_actual_at"]:
            reasons.append(
                f"{order['hours_past_pickup_window_end']:.1f}h past pickup window"
            )
        if not reasons:
            continue
        problems.setdefault(order["carrier"], []).append(
            {
                "type": "order",
                "order_id": order["order_id"],
                "account_id": order["account_id"],
                "reason": ", ".join(reasons),
            }
        )

    carriers = [row["carrier"] for row in conn.execute("SELECT DISTINCT carrier FROM orders")]
    for ticket in _open_tickets(conn):
        haystack = f"{ticket['subject']} {ticket['description']}".lower()
        for carrier in carriers:
            if carrier.lower() in haystack:
                problems.setdefault(carrier, []).append(
                    {
                        "type": "ticket",
                        "ticket_id": ticket["ticket_id"],
                        "account_id": ticket["account_id"],
                        "reason": ticket["subject"],
                    }
                )

    # ORD-4001 is DELIVERED, so the "open problems" query misses it, but the
    # spec still counts it as a SwiftShip signal because KI-211 is about that
    # carrier. Attach every order on a carrier that already has a known issue.
    for carrier, clause in known_issue_by_carrier.items():
        seen = {item.get("order_id") for item in problems.get(carrier, [])}
        extras = conn.execute(
            "SELECT * FROM orders WHERE carrier = ?", (carrier,)
        ).fetchall()
        for order in extras:
            if order["order_id"] in seen:
                continue
            problems.setdefault(carrier, []).append(
                {
                    "type": "order",
                    "order_id": order["order_id"],
                    "account_id": order["account_id"],
                    "reason": (
                        f"order on {carrier} "
                        f"(known issue {clause.flags['issue_id']})"
                    ),
                }
            )

    signals: list[Signal] = []
    for carrier, evidence in sorted(problems.items()):
        if len(evidence) < 2:
            continue
        accounts = sorted({item["account_id"] for item in evidence})
        clause = known_issue_by_carrier.get(carrier)
        signal = Signal(
            id=f"SIG-CARRIER-{carrier.upper().replace(' ', '-')}",
            kind="carrier_concentration",
            severity=KIND_SEVERITY["carrier_concentration"],
            title=f"{carrier}: {len(evidence)} open problem signals",
            detail=(
                f"Across {len(accounts)} account(s)."
                + (f" Subject of open known issue {clause.flags['issue_id']}." if clause else "")
            ),
            evidence=evidence,
            affected_accounts=accounts,
            recommended_action=f"Review {carrier} performance and check for a systemic cause.",
            seed_query=f"What is going wrong with {carrier} right now, and which accounts are affected?",
            computed_at=snapshot_now().isoformat(),
            sources=[clause.to_source()] if clause else [],
        )
        _rank(signal, 1.0 + 0.2 * len(evidence))
        signals.append(signal)
    return signals


# --------------------------------------------------------------------------
# 4. Cancellation spike
# --------------------------------------------------------------------------


def detect_cancellation_spike(conn: sqlite3.Connection) -> list[Signal]:
    now = snapshot_now()
    today = now.date().isoformat()
    rows = conn.execute(
        "SELECT * FROM orders WHERE cancellation_requested_at IS NOT NULL "
        "ORDER BY cancellation_requested_at"
    ).fetchall()
    todays = [row for row in rows if (row["cancellation_requested_at"] or "").startswith(today)]

    if len(todays) < ASSUMED_CANCELLATIONS_PER_DAY * CANCELLATION_SPIKE_MULTIPLE:
        return []

    total_orders = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
    stamps = [row["cancellation_requested_at"] for row in todays]
    clustered = _largest_cluster(stamps)

    accounts = sorted({row["account_id"] for row in todays})
    signal = Signal(
        id="SIG-CANCELSPIKE",
        kind="cancellation_spike",
        severity=KIND_SEVERITY["cancellation_spike"],
        title=f"{len(todays)} cancellation requests today, {clustered} within a two-hour window",
        detail=(
            f"{len(todays)} of {total_orders} orders in the dataset have a "
            f"cancellation request on {today}. Baseline is assumed "
            f"({ASSUMED_CANCELLATIONS_PER_DAY:.0f}/day), not learned -- the pack "
            "contains a single day, so treat the comparison as indicative."
        ),
        evidence=[
            {
                "order_id": row["order_id"],
                "account_id": row["account_id"],
                "requested_at": row["cancellation_requested_at"],
                "minutes_after_booking": round(row["minutes_booked_to_cancel_request"] or 0),
                "carrier": row["carrier"],
            }
            for row in todays
        ],
        affected_accounts=accounts,
        recommended_action="Check for a common carrier or product cause behind the cluster.",
        seed_query="Why are so many orders being cancelled today, and do they share a cause?",
        computed_at=now.isoformat(),
    )
    _rank(signal, len(todays) / max(ASSUMED_CANCELLATIONS_PER_DAY, 1.0))
    return [signal]


def _largest_cluster(stamps: list[str]) -> int:
    from datetime import datetime

    parsed = sorted(datetime.fromisoformat(stamp) for stamp in stamps if stamp)
    best = 0
    for index, start in enumerate(parsed):
        count = sum(1 for other in parsed[index:] if other - start <= CANCELLATION_CLUSTER_WINDOW)
        best = max(best, count)
    return best


# --------------------------------------------------------------------------
# 5. Multi-account impact
# --------------------------------------------------------------------------


def detect_multi_account_impact(conn: sqlite3.Connection, base: list[Signal]) -> list[Signal]:
    """Escalate anything touching two or more accounts by a level."""
    signals: list[Signal] = []
    for signal in base:
        if signal.kind in ("multi_account_impact", "sla_at_risk"):
            continue
        if len(signal.affected_accounts) < 2:
            continue
        names = ", ".join(
            f"{_account_name(conn, account_id)} ({account_id})"
            for account_id in signal.affected_accounts
        )
        escalated = Signal(
            id=f"SIG-MULTIACCOUNT-{signal.id}",
            kind="multi_account_impact",
            severity="P1",
            title=f"Multi-account impact: {signal.title}",
            detail=f"Touches {len(signal.affected_accounts)} accounts: {names}.",
            evidence=signal.evidence,
            affected_accounts=signal.affected_accounts,
            recommended_action=(
                "Treat as systemic rather than per-customer, and consider a "
                "proactive notification to every affected account."
            ),
            seed_query=f"Which accounts are affected by {signal.title}, and is there a systemic cause?",
            computed_at=snapshot_now().isoformat(),
            sources=signal.sources,
        )
        _rank(escalated, 1.5)
        signals.append(escalated)
    return signals


# --------------------------------------------------------------------------
# 6. Stale guidance
# --------------------------------------------------------------------------

_AMOUNT_RE = re.compile(r"(?:inr|rs\.?)\s*([\d,]+)", re.IGNORECASE)
_ROWS_RE = re.compile(r"([\d,]+)\s*rows", re.IGNORECASE)


def _number(text: str) -> int:
    return int(text.replace(",", ""))


def detect_stale_guidance(conn: sqlite3.Connection) -> list[Signal]:
    """Past resolutions that a current tier 1-4 source contradicts.

    Two checkers, hand-written against the two claim shapes this corpus
    contains: a stated cancellation fee, and a stated plan capability limit.
    Generalising this is a real piece of work and is item 2 on the roadmap in
    the product note -- but the mechanism is the authority ladder that already
    exists, not a new subsystem.
    """
    signals: list[Signal] = []
    rows = conn.execute(
        "SELECT * FROM tickets WHERE historical_resolution IS NOT NULL "
        "AND TRIM(historical_resolution) != ''"
    ).fetchall()

    for ticket in rows:
        said = ticket["historical_resolution"]
        account_id = ticket["account_id"]
        lowered = said.lower()
        contradiction: str | None = None
        source: dict[str, Any] | None = None

        if "cancellation fee" in lowered or "cancel" in lowered:
            resolution = resolve_rule(conn, "cancellation_fee", account_id)
            waiver_clause = resolution.clause_supplying("waives_cancellation_fee")
            if resolution.param("waives_cancellation_fee") and waiver_clause is not None:
                contradiction = (
                    f"The signed agreement waives the cancellation fee for this "
                    f"account, so the stated fee was wrong for {account_id}."
                )
                source = waiver_clause.to_source()
            else:
                stated = _AMOUNT_RE.search(said)
                current = resolution.param("late_fee_inr")
                if stated and current is not None and _number(stated.group(1)) != int(current):
                    contradiction = (
                        f"The current fee is INR {int(current)}, not INR "
                        f"{_number(stated.group(1))}."
                    )
                    clause = resolution.clause_supplying("late_fee_inr")
                    source = clause.to_source() if clause else None

        if contradiction is None and "rows" in lowered:
            stated = _ROWS_RE.search(said)
            limit_clause = next(
                (
                    clause
                    for clause in load_clauses(conn)
                    if "bulk_upload_max_rows" in clause.params
                ),
                None,
            )
            if stated and limit_clause is not None:
                limit = int(limit_clause.params["bulk_upload_max_rows"])
                if _number(stated.group(1)) != limit:
                    contradiction = (
                        f"The product supports {limit:,} rows per CSV. The stated "
                        f"{_number(stated.group(1)):,}-row figure was a plan limit "
                        "that does not exist."
                    )
                    source = limit_clause.to_source()

        if contradiction is None:
            continue

        signal = Signal(
            id=f"SIG-STALE-{ticket['ticket_id']}",
            kind="stale_guidance",
            severity=KIND_SEVERITY["stale_guidance"],
            title=(
                f"{ticket['ticket_id']}: we told "
                f"{_account_name(conn, account_id)} something we no longer stand behind"
            ),
            detail=contradiction,
            evidence=[
                {
                    "ticket_id": ticket["ticket_id"],
                    "account_id": account_id,
                    "closed_subject": ticket["subject"],
                    "we_said": said,
                    "current_position": contradiction,
                }
            ],
            affected_accounts=[account_id],
            recommended_action=(
                "Proactively correct the customer, and check whether the same "
                "answer was given to anyone else."
            ),
            seed_query=(
                f"{ticket['ticket_id']} told this customer: \"{said}\" - is that "
                "still correct, and what should we tell them now?"
            ),
            computed_at=snapshot_now().isoformat(),
            sources=[source] if source else [],
        )
        _rank(signal, 1.2)
        signals.append(signal)
    return signals


# --------------------------------------------------------------------------


def detect_signals(conn: sqlite3.Connection) -> list[Signal]:
    """Recomputed on request. The data is a frozen snapshot -- a cron job here
    would be pretend infrastructure."""
    base: list[Signal] = []
    base.extend(detect_sla_breaches(conn))
    base.extend(detect_repeated_root_cause(conn))
    base.extend(detect_carrier_concentration(conn))
    base.extend(detect_cancellation_spike(conn))
    base.extend(detect_stale_guidance(conn))

    everything = base + detect_multi_account_impact(conn, base)
    everything.sort(key=lambda signal: signal.rank_score, reverse=True)
    return everything
