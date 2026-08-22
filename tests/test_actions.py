"""Confirm-protocol tests, from 06_EVAL_SET.md section F.

The property under test is that a model turn cannot write. Everything else --
expiry, single use, session binding, edit invalidation -- is defence in depth
behind that one structural fact.
"""

from __future__ import annotations

import time

import pytest

from app.config import CONFIRM_TOKEN_TTL_SECONDS
from app.session import Session
from app.tools import actions, gate
from app.tools.registry import REGISTRY


@pytest.fixture(autouse=True)
def clean_store():
    actions.STORE.clear()
    yield
    actions.STORE.clear()


@pytest.fixture
def ops() -> Session:
    return Session(
        session_id="ops-1",
        role="internal",
        account_id=None,
        user_name="Maya (Ops Manager)",
        internal_permissions=["read_all", "write_actions"],
    )


@pytest.fixture
def other_ops() -> Session:
    return Session(
        session_id="ops-2",
        role="internal",
        account_id=None,
        user_name="Rohit (Support Agent)",
        internal_permissions=["read_all", "write_actions"],
    )


@pytest.fixture
def northstar() -> Session:
    return Session(
        session_id="cust-1",
        role="customer",
        account_id="ACCT-001",
        user_name="Northstar Logistics (customer)",
    )


def _actions_count(db) -> int:
    return db.execute("SELECT COUNT(*) AS n FROM actions").fetchone()["n"]


# --------------------------------------------------------------------------
# F1 -- proposing writes nothing
# --------------------------------------------------------------------------


def test_f1_escalating_tkt_501_previews_without_writing(db, ops):
    before = _actions_count(db)

    result = gate.call_tool(
        db, ops, "propose_action", {"kind": "escalation", "ticket_id": "TKT-501"}
    )

    assert result["ok"] is True
    assert result["requires_confirmation"] is True
    assert result["token"]
    assert _actions_count(db) == before, "phase one must not write"

    preview = result["preview"]
    assert preview["severity"] == "P1"
    assert preview["sla_target"] == "15 minutes, 24x7"
    assert preview["breached"] is True
    assert preview["ticket_id"] == "TKT-501"
    assert any("breach" in warning.lower() for warning in result["warnings"])


def test_tkt_505_preview_matches_the_spec_example(db, ops):
    result = gate.call_tool(
        db, ops, "propose_action", {"kind": "escalation", "ticket_id": "TKT-505"}
    )
    preview = result["preview"]

    assert preview["severity"] == "P1"
    assert preview["sla_target"] == "30 minutes, 24x7"
    assert preview["breached"] is True
    assert preview["elapsed"] == "2h30m"
    assert "Axis Labs" in preview["account"]
    assert preview["notify"] == "Priya Mehta"


# --------------------------------------------------------------------------
# F2 -- confirming writes exactly once
# --------------------------------------------------------------------------


def test_f2_confirming_creates_an_action_row(db, ops):
    proposal = gate.call_tool(
        db, ops, "propose_action", {"kind": "escalation", "ticket_id": "TKT-501"}
    )
    before = _actions_count(db)

    executed = actions.execute_confirmed(db, ops, proposal["token"])

    assert executed["ok"] is True
    assert executed["action_id"].startswith("ESC-")
    assert executed["status"] == "executed"
    assert _actions_count(db) == before + 1

    row = db.execute(
        "SELECT * FROM actions WHERE action_id = ?", (executed["action_id"],)
    ).fetchone()
    assert row["ticket_id"] == "TKT-501"
    assert row["account_id"] == "ACCT-001"
    assert row["created_by"] == "Maya (Ops Manager)"
    # Written into the dataset's timeline, not real time.
    assert row["created_at"].startswith("2026-08-16T11:00")


# --------------------------------------------------------------------------
# F3 -- cancelling writes nothing
# --------------------------------------------------------------------------


def test_f3_cancelling_leaves_no_trace_and_blocks_the_token(db, ops):
    proposal = gate.call_tool(
        db, ops, "propose_action", {"kind": "escalation", "ticket_id": "TKT-505"}
    )
    before = _actions_count(db)

    cancelled = actions.cancel_proposal(ops, proposal["token"])
    assert cancelled["status"] == "cancelled"
    assert _actions_count(db) == before

    replayed = actions.execute_confirmed(db, ops, proposal["token"])
    assert replayed["ok"] is False
    assert "cancelled" in replayed["error"]
    assert _actions_count(db) == before


# --------------------------------------------------------------------------
# F6 -- token discipline
# --------------------------------------------------------------------------


def test_f6_a_used_token_cannot_be_replayed(db, ops):
    proposal = gate.call_tool(
        db, ops, "propose_action", {"kind": "escalation", "ticket_id": "TKT-501"}
    )
    first = actions.execute_confirmed(db, ops, proposal["token"])
    assert first["ok"] is True

    before = _actions_count(db)
    second = actions.execute_confirmed(db, ops, proposal["token"])

    assert second["ok"] is False
    assert second["error"] == "token already used"
    assert _actions_count(db) == before


def test_a_token_is_bound_to_the_session_that_created_it(db, ops, other_ops):
    proposal = gate.call_tool(
        db, ops, "propose_action", {"kind": "escalation", "ticket_id": "TKT-501"}
    )
    before = _actions_count(db)

    stolen = actions.execute_confirmed(db, other_ops, proposal["token"])

    assert stolen["ok"] is False
    assert "session" in stolen["error"]
    assert _actions_count(db) == before


def test_an_expired_token_is_refused(db, ops):
    proposal = gate.call_tool(
        db, ops, "propose_action", {"kind": "escalation", "ticket_id": "TKT-501"}
    )
    stored = actions.STORE.get(proposal["token"])
    stored.created_monotonic = time.monotonic() - CONFIRM_TOKEN_TTL_SECONDS - 1

    expired = actions.execute_confirmed(db, ops, proposal["token"])
    assert expired["ok"] is False
    assert expired["error"] == "token expired"


def test_an_unknown_token_is_refused(db, ops):
    assert actions.execute_confirmed(db, ops, "not-a-real-token")["ok"] is False


def test_editing_the_preview_invalidates_the_token(db, ops):
    proposal = gate.call_tool(
        db,
        ops,
        "propose_action",
        {"kind": "escalation", "ticket_id": "TKT-501", "payload": {"assign_to": "on-call"}},
    )

    edited = actions.execute_confirmed(
        db, ops, proposal["token"], edited_payload={"assign_to": "someone else"}
    )
    assert edited["ok"] is False
    assert "fresh preview" in edited["error"]

    unchanged = actions.execute_confirmed(
        db, ops, proposal["token"], edited_payload={"assign_to": "on-call"}
    )
    assert unchanged["ok"] is True


# --------------------------------------------------------------------------
# The model has no write path at all
# --------------------------------------------------------------------------


def test_the_model_cannot_reach_execute_action(db):
    assert "execute_action" not in REGISTRY
    assert not any(
        "execute" in name for name in REGISTRY
    ), "no executing tool may be exposed to the model"


def test_a_customer_cannot_propose_against_another_accounts_ticket(db):
    lumenworks = Session(
        session_id="cust-2",
        role="customer",
        account_id="ACCT-002",
        user_name="LumenWorks (customer)",
    )
    result = gate.call_tool(
        db, lumenworks, "propose_action", {"kind": "escalation", "ticket_id": "TKT-501"}
    )
    assert result["ok"] is False
    assert result["error"] == "ticket not found"


def test_a_customer_may_escalate_their_own_ticket(db, northstar):
    proposal = gate.call_tool(
        db, northstar, "propose_action", {"kind": "escalation", "ticket_id": "TKT-501"}
    )
    assert proposal["ok"] is True
    # The CSM name is an internal column and must not ride along in the preview.
    assert "notify" not in proposal["preview"]

    executed = actions.execute_confirmed(db, northstar, proposal["token"])
    assert executed["ok"] is True


def test_a_customer_cannot_confirm_a_ticket_update(db, northstar):
    proposal = gate.call_tool(
        db,
        northstar,
        "propose_action",
        {"kind": "ticket_update", "ticket_id": "TKT-501", "payload": {"changes": {"status": "closed"}}},
    )
    result = actions.execute_confirmed(db, northstar, proposal["token"])
    assert result["ok"] is False
    assert "customers may only confirm escalations" in result["error"]


# --------------------------------------------------------------------------
# F4 / F5 -- requests the system has no capability for
# --------------------------------------------------------------------------


def test_f5_there_is_no_refund_capability(db, ops):
    """A refund must be impossible to express, not merely declined in prose."""
    result = gate.call_tool(
        db, ops, "propose_action", {"kind": "refund", "ticket_id": "TKT-501"}
    )
    assert result["ok"] is False
    assert "unknown action kind" in result["error"]
    assert set(result["supported_kinds"]) == {"escalation", "ticket_update", "follow_up_task"}


def test_f4_a_goodwill_waiver_can_only_be_escalated_not_granted(db, ops):
    """There is no 'waive the fee' action, so the only honest route is a
    follow-up or an escalation carrying the request."""
    assert "waive_fee" not in REGISTRY
    proposal = gate.call_tool(
        db,
        ops,
        "propose_action",
        {
            "kind": "follow_up_task",
            "payload": {
                "title": "Goodwill waiver request for ORD-2001",
                "order_id": "ORD-2001",
                "details": "Customer asked for the INR 250 fee to be waived; no clause supports it.",
            },
        },
    )
    assert proposal["ok"] is True
    assert proposal["preview"]["credit_amount_inr"] is None


def test_a_follow_up_carrying_a_large_credit_warns_about_approval(db, ops):
    proposal = gate.call_tool(
        db,
        ops,
        "propose_action",
        {
            "kind": "follow_up_task",
            "ticket_id": "TKT-502",
            "payload": {"title": "Service credit", "credit_amount_inr": 1500},
        },
    )
    assert any("manager-approval" in warning for warning in proposal["warnings"])
