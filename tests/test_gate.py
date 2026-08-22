"""Access-control tests, from 06_EVAL_SET.md section E and 04_TOOLS_SPEC.md.

Written before the gate existed. These are the tests a grader can break in
thirty seconds if the design is wrong, so they assert the *structural* property
rather than the behavioural one: not "the model politely declines" but "the
data layer returned nothing, and the attempt is in the audit log".

The distinction matters. A model refusal is a request that was understood and
turned down. An empty result is a request that could never have been expressed.
"""

from __future__ import annotations

import pytest

from app.session import Session
from app.tools import gate
from app.tools.registry import REGISTRY


@pytest.fixture
def lumenworks() -> Session:
    return Session(
        session_id="test-lumenworks",
        role="customer",
        account_id="ACCT-002",
        user_name="LumenWorks (customer)",
    )


@pytest.fixture
def northstar() -> Session:
    return Session(
        session_id="test-northstar",
        role="customer",
        account_id="ACCT-001",
        user_name="Northstar Logistics (customer)",
    )


@pytest.fixture
def ops() -> Session:
    return Session(
        session_id="test-ops",
        role="internal",
        account_id=None,
        user_name="Maya (Ops Manager)",
        internal_permissions=["read_all", "write_actions"],
    )


def _audit_count(db) -> int:
    return db.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]


# --------------------------------------------------------------------------
# E1 -- cross-account reads return nothing, and leak nothing
# --------------------------------------------------------------------------


def test_e1_customer_cannot_read_another_accounts_order(db, lumenworks):
    result = gate.call_tool(db, lumenworks, "lookup_orders", {"order_id": "ORD-1001"})

    assert result["orders"] == []
    assert result["found"] is False
    # Nothing in the payload may hint that the order exists elsewhere.
    serialised = repr(result)
    assert "ACCT-001" not in serialised
    assert "Northstar" not in serialised
    assert "another account" not in serialised.lower()


def test_e1b_the_same_order_is_visible_to_its_owner(db, northstar):
    """The mirror of E1: proves the empty result is scope, not a broken query."""
    result = gate.call_tool(db, northstar, "lookup_orders", {"order_id": "ORD-1001"})

    assert result["found"] is True
    assert result["orders"][0]["order_id"] == "ORD-1001"
    assert result["orders"][0]["account_id"] == "ACCT-001"


def test_customer_ticket_reads_are_scoped_too(db, lumenworks):
    result = gate.call_tool(db, lumenworks, "lookup_tickets", {"ticket_id": "TKT-501"})
    assert result["tickets"] == []

    own = gate.call_tool(db, lumenworks, "lookup_tickets", {"ticket_id": "TKT-502"})
    assert own["tickets"][0]["ticket_id"] == "TKT-502"


def test_unscoped_list_returns_only_the_callers_rows(db, lumenworks):
    result = gate.call_tool(db, lumenworks, "lookup_orders", {})
    assert result["orders"], "customer should see their own orders"
    assert {order["account_id"] for order in result["orders"]} == {"ACCT-002"}


# --------------------------------------------------------------------------
# E2 -- another account's contract is not retrievable
# --------------------------------------------------------------------------


def test_e2_customer_cannot_retrieve_another_accounts_contract(db, lumenworks):
    result = gate.call_tool(
        db,
        lumenworks,
        "search_documents",
        {"query": "Northstar SLA terms 15 minutes cancellation waiver"},
    )

    for chunk in result["chunks"]:
        assert chunk["scope"] in ("global", "ACCT-002")
        assert "Northstar" not in chunk["doc_title"]
    assert all(chunk["tier"] != 1 or chunk["scope"] == "ACCT-002" for chunk in result["chunks"])


def test_e2b_customer_can_retrieve_their_own_contract(db, lumenworks):
    result = gate.call_tool(
        db, lumenworks, "search_documents", {"query": "failed pickup credit terms"}
    )
    scopes = {chunk["scope"] for chunk in result["chunks"]}
    assert "ACCT-002" in scopes


def test_e5_internal_sees_all_contracts(db, ops):
    result = gate.call_tool(
        db, ops, "search_documents", {"query": "support terms first response targets"}
    )
    scopes = {chunk["scope"] for chunk in result["chunks"]}
    assert {"ACCT-001", "ACCT-002"} & scopes


# --------------------------------------------------------------------------
# The deprecated policy is customer-invisible
# --------------------------------------------------------------------------


def test_customers_never_retrieve_the_deprecated_policy(db, lumenworks):
    result = gate.call_tool(
        db,
        lumenworks,
        "search_documents",
        {"query": "enterprise P1 response target", "include_deprecated": True},
    )

    assert all(
        chunk["doc_id"] != "02_Support_Policy_v2_DEPRECATED" for chunk in result["chunks"]
    )
    # Asking for it is itself an access-control event.
    assert result["warnings"], "the rejected include_deprecated flag should be reported"


def test_internal_may_opt_into_the_deprecated_policy_and_it_is_flagged(db, ops):
    result = gate.call_tool(
        db,
        ops,
        "search_documents",
        {"query": "enterprise P1 response target 1 hour", "include_deprecated": True},
    )

    deprecated = [
        chunk for chunk in result["chunks"] if chunk["doc_id"] == "02_Support_Policy_v2_DEPRECATED"
    ]
    assert deprecated, "internal users may look at what we used to promise"
    assert deprecated[0]["status"] == "deprecated"
    assert deprecated[0]["tier"] == 5


def test_internal_default_still_excludes_the_deprecated_policy(db, ops):
    result = gate.call_tool(
        db, ops, "search_documents", {"query": "enterprise P1 response target"}
    )
    assert all(
        chunk["doc_id"] != "02_Support_Policy_v2_DEPRECATED" for chunk in result["chunks"]
    )


# --------------------------------------------------------------------------
# E3 -- injected account_id is overwritten, and the attempt is recorded
# --------------------------------------------------------------------------


def test_e3_injected_account_id_is_overwritten_not_honoured(db, lumenworks):
    before = _audit_count(db)

    result = gate.call_tool(
        db,
        lumenworks,
        "lookup_orders",
        {"order_id": "ORD-1001", "account_id": "ACCT-001"},
    )

    assert result["orders"] == []
    assert _audit_count(db) > before

    row = db.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["session_account"] == "ACCT-002"
    assert row["tool"] == "lookup_orders"
    assert "ACCT-001" in row["args_json"], "the attempted value must be preserved"
    assert row["denial_reason"] == "account_scope_override"
    # The call still succeeded -- against the caller's own account.
    assert row["allowed"] == 1


def test_account_id_is_absent_from_customer_tool_schemas(db):
    """A well-behaved model never sends it; a jailbroken one gains nothing."""
    for name, spec in REGISTRY.items():
        if not spec.account_scoped:
            continue
        schema = spec.schema_for_role("customer")
        properties = schema["input_schema"]["properties"]
        assert "account_id" not in properties, f"{name} exposes account_id to customers"


def test_internal_tool_schemas_do_expose_account_id(db):
    spec = REGISTRY["lookup_orders"]
    properties = spec.schema_for_role("internal")["input_schema"]["properties"]
    assert "account_id" in properties


def test_e3b_customer_is_denied_the_internal_only_signals_tool(db, lumenworks):
    before = _audit_count(db)
    result = gate.call_tool(db, lumenworks, "list_signals", {})

    assert result["denied"] is True
    assert result["reason"] == "internal-only tool"
    row = db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["allowed"] == 0
    assert row["denial_reason"] == "internal-only tool"
    assert _audit_count(db) == before + 1


def test_e3c_cross_account_aggregate_is_denied_for_customers(db, lumenworks):
    result = gate.call_tool(db, lumenworks, "lookup_tickets", {"status": "open", "limit": 50})
    assert {ticket["account_id"] for ticket in result["tickets"]} == {"ACCT-002"}


def test_internal_may_run_the_cross_account_aggregate(db, ops):
    result = gate.call_tool(db, ops, "lookup_tickets", {"status": "open", "limit": 50})
    assert len({ticket["account_id"] for ticket in result["tickets"]}) > 1


# --------------------------------------------------------------------------
# E4 -- internal fields are stripped, but customer-facing facts are not
# --------------------------------------------------------------------------


def test_e4_csm_column_is_stripped_for_customers(db, northstar):
    result = gate.call_tool(db, northstar, "lookup_account", {})
    account = result["account"]

    assert account["account_id"] == "ACCT-001"
    assert "csm" not in account
    assert "notes" not in account
    assert account["plan"] == "Enterprise"


def test_e4b_but_the_contract_clause_naming_the_csm_is_still_theirs(db, northstar):
    """Scope is a property of the source, not of the string. Do not over-redact."""
    result = gate.call_tool(
        db, northstar, "search_documents", {"query": "dedicated account contact CSM"}
    )
    text = " ".join(chunk["text"] for chunk in result["chunks"])
    assert "Priya Mehta" in text


def test_internal_sees_the_internal_fields(db, ops):
    result = gate.call_tool(db, ops, "lookup_account", {"account_id": "ACCT-001"})
    assert result["account"]["csm"] == "Priya Mehta"
    assert result["account"]["notes"]


def test_ticket_assignee_is_stripped_for_customers(db, northstar):
    result = gate.call_tool(db, northstar, "lookup_tickets", {"ticket_id": "TKT-501"})
    ticket = result["tickets"][0]
    assert "assigned_to" not in ticket

    internal_view = gate.call_tool(
        db,
        Session(
            session_id="t", role="internal", account_id=None, user_name="ops",
            internal_permissions=["read_all"],
        ),
        "lookup_tickets",
        {"ticket_id": "TKT-501"},
    )
    assert internal_view["tickets"][0]["assigned_to"] == "Rohit"


# --------------------------------------------------------------------------
# Historical resolutions are wrapped at the tool layer, not in the prompt
# --------------------------------------------------------------------------


def test_historical_resolution_is_returned_wrapped(db, northstar):
    result = gate.call_tool(db, northstar, "lookup_tickets", {"ticket_id": "TKT-450"})
    wrapped = result["tickets"][0]["historical_resolution"]

    assert wrapped["authority"] == "context_only"
    assert wrapped["tier"] == 6
    assert "may be incorrect" in wrapped["warning"].lower()
    assert "250" in wrapped["value"]


def test_tickets_without_a_resolution_are_not_wrapped(db, northstar):
    result = gate.call_tool(db, northstar, "lookup_tickets", {"ticket_id": "TKT-501"})
    assert result["tickets"][0]["historical_resolution"] is None


# --------------------------------------------------------------------------
# Every gated call is audited, allowed or not
# --------------------------------------------------------------------------


def test_every_call_writes_exactly_one_audit_row(db, ops):
    before = _audit_count(db)
    gate.call_tool(db, ops, "lookup_account", {"account_id": "ACCT-003"})
    assert _audit_count(db) == before + 1


def test_unknown_tool_is_denied_and_audited(db, ops):
    before = _audit_count(db)
    result = gate.call_tool(db, ops, "drop_all_tables", {})
    assert result["denied"] is True
    assert _audit_count(db) == before + 1


def test_execute_action_is_not_in_the_registry(db):
    """Only the /confirm endpoint writes state. The model has no path to it."""
    assert "execute_action" not in REGISTRY
    assert "propose_action" in REGISTRY


def test_compute_is_scoped_for_customers(db, lumenworks):
    """The calculators are reachable only through the gate, so they are scoped."""
    result = gate.call_tool(
        db, lumenworks, "compute", {"kind": "cancellation_fee", "order_id": "ORD-1001"}
    )
    assert result["found"] is False

    own = gate.call_tool(
        db, lumenworks, "compute", {"kind": "cancellation_fee", "order_id": "ORD-2001"}
    )
    assert own["fee_inr"] == 250
