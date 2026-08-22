"""Confidence and escalation rules.

Kept out of the prompt on purpose. "Escalate when you are unsure" is advice; a
function that inspects the tool results and returns `escalate` is a rule. The
model can still recommend an escalation on its own judgement, but it cannot
suppress one that these conditions require.

Escalation is itself a state-changing action and goes through the same confirm
flow as everything else. Even a detected P1 is pre-filled and strongly
recommended rather than written silently -- a two-second delay is cheaper than
a write nobody authorised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

HIGH = "high"
MEDIUM = "medium"
ESCALATE = "escalate"

# Requests the system has no tool for. Naming them explicitly is better than
# letting the model discover the absence and improvise something reassuring.
UNSUPPORTED_CAPABILITIES = {
    "refund": ["refund", "refunded", "money back", "reimburse"],
    "address_change": ["change the address", "change address", "update the address"],
    "plan_change": ["upgrade our plan", "downgrade", "change our plan", "plan upgrade"],
    "billing_contact_change": ["billing contact", "change the billing"],
    "fee_waiver_exception": [
        "as a goodwill",
        "goodwill gesture",
        "make an exception",
        "waive it anyway",
        "just this once",
        "override the policy",
    ],
}


@dataclass
class Assessment:
    confidence: str = HIGH
    escalate: bool = False
    reasons: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    stale_guidance: list[dict[str, Any]] = field(default_factory=list)

    def add(self, reason: str, *, confidence: str = ESCALATE) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)
        if confidence == ESCALATE:
            self.escalate = True
            self.confidence = ESCALATE
        elif self.confidence == HIGH:
            self.confidence = confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence": self.confidence,
            "escalate": self.escalate,
            "reasons": list(self.reasons),
            "unsupported_capabilities": list(self.unsupported),
            "stale_guidance": list(self.stale_guidance),
        }


def _detect_unsupported(message: str) -> list[str]:
    lowered = message.lower()
    return [
        capability
        for capability, phrases in UNSUPPORTED_CAPABILITIES.items()
        if any(phrase in lowered for phrase in phrases)
    ]


def walk(value: Any):
    """Yield every dict nested anywhere in a tool result."""
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def assess(
    user_message: str,
    tool_results: list[dict[str, Any]],
    *,
    retrieved_tiers: set[int] | None = None,
) -> Assessment:
    """Decide confidence for one turn from what the tools actually returned."""
    assessment = Assessment()

    assessment.unsupported = _detect_unsupported(user_message)
    for capability in assessment.unsupported:
        assessment.add(
            f"The request needs {capability.replace('_', ' ')}, which this system "
            "has no tool for."
        )

    saw_authority = False
    for result in tool_results:
        for node in walk(result):
            if node.get("eligible") == "unknown" or node.get("must_escalate") is True:
                unknowns = ", ".join(node.get("unknowns") or []) or "required inputs"
                assessment.add(
                    f"A required field is unknown ({unknowns}); the SOP forbids "
                    "promising an outcome under uncertainty."
                )
            if node.get("needs_manager_approval") is True:
                assessment.add(
                    "The computed credit exceeds the manager-approval threshold."
                )
            if node.get("severity") == "P1":
                assessment.add("P1 severity: policy requires immediate escalation.")
            if node.get("breached") is True:
                assessment.add(
                    "A first-response target is already breached and must be stated."
                )
            if node.get("same_tier_disagreement"):
                assessment.add(
                    "Two sources of equal authority disagree; the system does not "
                    "choose between them."
                )
            for conflict in node.get("conflicts") or []:
                if isinstance(conflict, dict) and str(conflict.get("winner", "")).startswith("none"):
                    assessment.add("Same-tier source conflict with no winner.")
            tier = node.get("tier")
            if isinstance(tier, int) and tier <= 3:
                saw_authority = True

    if retrieved_tiers:
        saw_authority = saw_authority or any(tier <= 3 for tier in retrieved_tiers)

    if tool_results and not saw_authority:
        assessment.add(
            "No tier 1-3 source was found for the applicable rule.", confidence=ESCALATE
        )

    return assessment


def find_stale_guidance(
    tool_results: list[dict[str, Any]], conflicts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Tier-6 resolutions retrieved alongside a current source that outranks them.

    Emitted as a flag rather than silently discarded: "we told this customer
    something we no longer stand behind" is useful ops information, and it falls
    straight out of the ladder rather than needing a detector of its own.
    """
    flagged: list[dict[str, Any]] = []
    for result in tool_results:
        for ticket in result.get("tickets") or []:
            resolution = ticket.get("historical_resolution")
            if isinstance(resolution, dict) and resolution.get("authority") == "context_only":
                flagged.append(
                    {
                        "ticket_id": ticket.get("ticket_id"),
                        "account_id": ticket.get("account_id"),
                        "said": resolution.get("value"),
                        "why": (
                            "Past support guidance. Verify against the current "
                            "agreement, SOP, policy or product guide before repeating it."
                        ),
                    }
                )
    return flagged
