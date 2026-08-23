"""Escalation rules in agent/policy.py, without a model in the loop."""

from __future__ import annotations

from app.agent.policy import assess


def test_unknown_fault_forces_escalation():
    result = assess(
        "Do I get a credit?",
        [{"eligible": "unknown", "must_escalate": True, "unknowns": ["carrier_fault"]}],
    )
    assert result.escalate is True
    assert result.confidence == "escalate"
    assert any("unknown" in reason.lower() for reason in result.reasons)


def test_credit_over_approval_threshold_escalates():
    result = assess("What credit is owed?", [{"needs_manager_approval": True, "amount_inr": 1500}])
    assert result.escalate is True
    assert any("manager-approval" in reason for reason in result.reasons)


def test_p1_escalates_immediately():
    result = assess("What should we do?", [{"severity": "P1", "ticket_id": "TKT-505"}])
    assert result.escalate is True
    assert any("P1" in reason for reason in result.reasons)


def test_breached_sla_escalates():
    result = assess("Is this late?", [{"breached": True, "ticket_id": "TKT-501"}])
    assert result.escalate is True


def test_same_tier_disagreement_escalates():
    result = assess(
        "Which rule applies?",
        [{"same_tier_disagreement": True}],
    )
    assert result.escalate is True


def test_refund_request_is_unsupported():
    result = assess("Please refund ORD-4001", [])
    assert result.escalate is True
    assert "refund" in result.unsupported


def test_goodwill_waiver_is_unsupported():
    result = assess("Waive the 250 fee for ORD-2001 as a goodwill gesture", [])
    assert result.escalate is True
    assert "fee_waiver_exception" in result.unsupported


def test_billing_contact_change_is_unsupported():
    result = assess("How do we change the billing contact?", [])
    assert result.escalate is True
    assert "billing_contact_change" in result.unsupported


def test_no_tier_1_to_3_source_escalates():
    result = assess(
        "What is the rule?",
        [{"chunks": [{"tier": 4, "doc_id": "04_Product_Operations_Guide_and_Known_Issues"}]}],
        retrieved_tiers={4},
    )
    assert result.escalate is True
    assert any("tier 1-3" in reason for reason in result.reasons)


def test_ordinary_answer_stays_high_confidence():
    result = assess(
        "Can we cancel ORD-3001?",
        [{"fee_inr": 0, "source": {"tier": 2, "doc_id": "03_Cancellation_and_Service_Credit_SOP_v4"}}],
        retrieved_tiers={2},
    )
    assert result.escalate is False
    assert result.confidence == "high"
