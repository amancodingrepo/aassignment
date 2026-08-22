"""The authority ladder.

One module owns the question "which clause governs this, for this account, at
this moment?" -- retrieval, the calculators, and the signals detectors all ask
it here rather than each re-deriving precedence. Precedence itself is not
invented: Support Policy v3 §1 states it (signed agreement, then current support
policy, then current product documentation, with historical tickets as context
only) and the tier numbers in the sidecar encode that statement.

Losing clauses are never dropped. They come back attached to the decision as
`Conflict` records so the answer can show which source won and why.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

from app.config import IST, snapshot_now

# Tier meanings, from 03_SOURCE_AUTHORITY.md. Lower number wins.
TIER_CONTRACT = 1
TIER_SOP = 2
TIER_SUPPORT_POLICY = 3
TIER_PRODUCT_GUIDE = 4
TIER_DEPRECATED = 5
TIER_HISTORICAL_TICKET = 6

TIER_LABELS = {
    TIER_CONTRACT: "Signed customer agreement",
    TIER_SOP: "Current operational SOP",
    TIER_SUPPORT_POLICY: "Current support policy",
    TIER_PRODUCT_GUIDE: "Product documentation",
    TIER_DEPRECATED: "Deprecated policy (internal reference only)",
    TIER_HISTORICAL_TICKET: "Historical ticket resolution (context only, may be incorrect)",
}

GLOBAL_SCOPE = "global"


@dataclass
class Clause:
    """A retrieved chunk, with its authority metadata and rule parameters."""

    chunk_id: str
    doc_id: str
    doc_title: str
    tier: int
    status: str
    scope: str
    effective_from: str | None
    effective_to: str | None
    section: str
    section_title: str
    topics: list[str]
    flags: dict[str, Any]
    params: dict[str, Any]
    text: str
    internal_only: bool

    @property
    def citation(self) -> str:
        return f"{self.doc_title} §{self.section}"

    @property
    def tier_label(self) -> str:
        return TIER_LABELS.get(self.tier, f"tier {self.tier}")

    def to_source(self) -> dict[str, Any]:
        """The shape the UI renders as a source card."""
        return {
            "chunk_id": self.chunk_id,
            "doc": self.doc_title,
            "doc_id": self.doc_id,
            "tier": self.tier,
            "tier_label": self.tier_label,
            "clause": f"§{self.section}",
            "section_title": self.section_title,
            "status": self.status,
            "scope": self.scope,
            "citation": self.citation,
        }


@dataclass
class Conflict:
    topic: str
    winner: str
    loser: str
    why: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class RuleResolution:
    """The clauses that govern one rule for one account, best authority first."""

    rule: str
    account_id: str | None
    clauses: list[Clause] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def governing(self) -> Clause | None:
        return self.clauses[0] if self.clauses else None

    def param(self, name: str, default: Any = None) -> Any:
        """First value for `name` walking clauses from strongest authority down."""
        for clause in self.clauses:
            if name in clause.params:
                return clause.params[name]
        return default

    def clause_supplying(self, name: str) -> Clause | None:
        for clause in self.clauses:
            if name in clause.params:
                return clause
        return None


def _parse_boundary(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def is_effective(clause: Clause, when: datetime | None = None) -> bool:
    """Is this clause inside its effective window at the snapshot?"""
    moment = (when or snapshot_now()).astimezone(IST).date()
    start = _parse_boundary(clause.effective_from)
    end = _parse_boundary(clause.effective_to)
    if start and moment < start:
        return False
    if end and moment > end:
        return False
    return True


def row_to_clause(row: sqlite3.Row) -> Clause:
    flags = json.loads(row["flags_json"] or "{}")
    params = flags.pop("params", {}) or {}
    return Clause(
        chunk_id=row["chunk_id"],
        doc_id=row["doc_id"],
        doc_title=row["doc_title"],
        tier=row["tier"],
        status=row["status"],
        scope=row["scope"],
        effective_from=row["effective_from"],
        effective_to=row["effective_to"],
        section=row["section"],
        section_title=row["section_title"],
        topics=json.loads(row["topics_json"] or "[]"),
        flags=flags,
        params=params,
        text=row["text"],
        internal_only=bool(row["internal_only"]),
    )


def load_clauses(conn: sqlite3.Connection) -> list[Clause]:
    rows = conn.execute("SELECT * FROM chunks").fetchall()
    return [row_to_clause(row) for row in rows]


def visible_clauses(
    conn: sqlite3.Connection,
    account_id: str | None,
    *,
    role: str = "customer",
    include_deprecated: bool = False,
    when: datetime | None = None,
) -> list[Clause]:
    """The hard prefilter, applied before any scoring happens.

    Scope is enforced here rather than in the ranker because a low-ranked
    Northstar clause leaking into a LumenWorks answer is exactly as wrong as a
    top-ranked one.
    """
    results: list[Clause] = []
    for clause in load_clauses(conn):
        if clause.internal_only and role != "internal":
            continue
        if clause.tier == TIER_CONTRACT and clause.scope != GLOBAL_SCOPE:
            # Account-bound clause: only its own account may ever see it.
            if account_id is None:
                if role != "internal":
                    continue
            elif clause.scope != account_id:
                continue
        if clause.status == "deprecated" and not include_deprecated:
            continue
        if not is_effective(clause, when):
            # Deprecated documents fall outside their window too; internal users
            # who explicitly opt in still get them, clearly flagged.
            if not (include_deprecated and role == "internal"):
                continue
        results.append(clause)
    return results


def resolve_rule(
    conn: sqlite3.Connection,
    rule: str,
    account_id: str | None,
    *,
    topic: str | None = None,
    when: datetime | None = None,
) -> RuleResolution:
    """Collect every clause that supplies parameters for `rule`, strongest first.

    A contract clause and the SOP clause it modifies both come back. The caller
    reads parameters through `RuleResolution.param`, which walks tiers in order,
    so a contract that sets only a threshold still inherits the SOP's amount.
    """
    candidates = [
        clause
        for clause in visible_clauses(
            conn, account_id, role="internal", include_deprecated=False, when=when
        )
        if clause.params.get("rule") == rule
        and (topic is None or topic in clause.topics)
        # No account named means no contract applies. `visible_clauses` lets an
        # internal caller see every contract, which is right for search and
        # wrong here: a rule resolved for nobody in particular must resolve to
        # the defaults, not to whichever agreement happened to sort first.
        and (clause.tier != TIER_CONTRACT or account_id is not None)
    ]
    # A global clause and the caller's own contract are both in scope; another
    # account's contract is not, and visible_clauses has already removed it.
    candidates.sort(key=lambda clause: (clause.tier, clause.doc_id, clause.section))

    resolution = RuleResolution(rule=rule, account_id=account_id, clauses=candidates)
    resolution.conflicts = detect_conflicts(rule, candidates)
    return resolution


def detect_conflicts(rule: str, clauses: list[Clause]) -> list[Conflict]:
    """Record, rather than silently collapse, every overridden parameter.

    Same-tier disagreement is deliberately *not* resolved here -- it is reported
    as a conflict with no winner so the policy layer can escalate it.
    """
    conflicts: list[Conflict] = []
    seen: dict[str, Clause] = {}
    for clause in clauses:
        for name, value in clause.params.items():
            if name == "rule":
                continue
            if name not in seen:
                seen[name] = clause
                continue
            holder = seen[name]
            if holder.params.get(name) == value:
                continue
            if holder.tier < clause.tier:
                conflicts.append(
                    Conflict(
                        topic=f"{rule}.{name}",
                        winner=f"{holder.citation} (tier {holder.tier})",
                        loser=f"{clause.citation} (tier {clause.tier})",
                        why=(
                            f"{holder.tier_label} overrides {clause.tier_label} "
                            f"on {name} ({value!r} -> {holder.params[name]!r})"
                        ),
                    )
                )
            elif holder.tier == clause.tier:
                conflicts.append(
                    Conflict(
                        topic=f"{rule}.{name}",
                        winner="none - same-tier disagreement",
                        loser=f"{holder.citation} and {clause.citation} (both tier {clause.tier})",
                        why=(
                            "Two sources of equal authority disagree on "
                            f"{name}; the system does not choose between them."
                        ),
                    )
                )
    return conflicts


def has_same_tier_conflict(conflicts: list[Conflict]) -> bool:
    return any(conflict.winner.startswith("none") for conflict in conflicts)


def sla_targets_for(
    conn: sqlite3.Connection,
    account_id: str | None,
    plan: str | None,
    severity: str,
    *,
    include_deprecated: bool = False,
    when: datetime | None = None,
) -> list[sqlite3.Row]:
    """Candidate SLA rows for an account, strongest authority first.

    The deprecated v2 table lives in the same table as v3 and is filtered by the
    same rules that filter its prose. That is the whole point of storing the two
    tables separately and tagging them: 'Enterprise P1' has two answers in the
    corpus, and only one of them is retrievable.
    """
    rows = conn.execute("SELECT * FROM sla_targets WHERE severity = ?", (severity.upper(),)).fetchall()
    clauses = {
        clause.chunk_id: clause
        for clause in visible_clauses(
            conn,
            account_id,
            role="internal" if include_deprecated else "customer",
            include_deprecated=include_deprecated,
            when=when,
        )
    }

    candidates: list[sqlite3.Row] = []
    for row in rows:
        if row["chunk_id"] not in clauses:
            continue
        if row["scope"] != GLOBAL_SCOPE:
            if account_id is None or row["scope"] != account_id:
                continue
            candidates.append(row)
            continue
        # Global policy rows apply only to the account's own plan.
        if row["plan"] == "*" or (plan and row["plan"].lower() == plan.lower()):
            candidates.append(row)

    candidates.sort(key=lambda row: (row["tier"], row["doc_id"]))
    return candidates
