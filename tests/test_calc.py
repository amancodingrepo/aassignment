"""Golden cases from 06_EVAL_SET.md sections A, B and C.

Written before tools/calc.py existed. The point of doing it in that order is
that once these pass, the correct answer to every calculation in the pack is
known without an LLM in the loop -- so any later wrong answer in the chat window
is an agent bug, not an arithmetic bug, and that distinction is worth hours.

Nothing here reaches into the implementation for constants. Expected values are
transcribed from the eval set, which was transcribed from the PDFs.
"""

from __future__ import annotations

import pytest

from app.config import snapshot_now
from app.tools import calc

# --------------------------------------------------------------------------
# The reference clock
# --------------------------------------------------------------------------


def test_snapshot_is_the_declared_instant(db):
    now = snapshot_now()
    assert (now.year, now.month, now.day) == (2026, 8, 16)
    assert (now.hour, now.minute) == (11, 0)
    assert now.utcoffset().total_seconds() == 5.5 * 3600


# --------------------------------------------------------------------------
# Section A -- cancellation
# --------------------------------------------------------------------------


def test_a1_northstar_ord_1001_is_free_despite_being_late(db):
    """Contract waiver beats the SOP's 30-minute window. Canary: fee must be 0."""
    result = calc.cancellation_fee(db, "ORD-1001")

    assert result["found"] is True
    assert result["cancellable"] is True
    assert result["fee_inr"] == 0
    assert result["rule_applied"] == "contract_waiver"
    assert result["source"]["doc_id"] == "05_Northstar_Logistics_Enterprise_Agreement"
    assert result["source"]["clause"] == "§2"
    assert result["source"]["tier"] == 1
    # 120 minutes after booking: the default rule would have charged.
    assert result["minutes_since_booking"] == pytest.approx(120)
    assert result["default_outcome"]["fee_inr"] == 250
    # The loser is reported, not dropped.
    assert result["conflicts"], "contract-vs-SOP conflict must be surfaced"
    conflict = result["conflicts"][0]
    assert "Northstar" in conflict["winner"]
    assert "SOP" in conflict["loser"]


def test_a2_lumenworks_ord_2001_pays_the_standard_fee(db):
    """Canary: a contract that declines a waiver must not be read as granting one."""
    result = calc.cancellation_fee(db, "ORD-2001")

    assert result["cancellable"] is True
    assert result["fee_inr"] == 250
    assert result["rule_applied"] == "after_free_window"
    assert result["minutes_since_booking"] == pytest.approx(75)
    assert result["source"]["doc_id"] == "03_Cancellation_and_Service_Credit_SOP_v4"
    assert result["source"]["clause"] == "§1"


def test_a3_beacon_ord_3001_is_inside_the_free_window(db):
    """No contract at all: pure SOP, and the contract layer stays out of it."""
    result = calc.cancellation_fee(db, "ORD-3001")

    assert result["fee_inr"] == 0
    assert result["rule_applied"] == "within_free_window"
    assert result["minutes_since_booking"] == pytest.approx(15)
    assert result["source"]["tier"] == 2
    assert result["conflicts"] == []


def test_a4_northstar_ord_1002_cannot_be_cancelled_at_all(db):
    """The trap. PICKED_UP outranks the waiver; status is checked first."""
    result = calc.cancellation_fee(db, "ORD-1002")

    assert result["cancellable"] is False
    assert result["fee_inr"] is None
    assert result["blocked"]["reason"] == "return_to_origin"
    assert result["rule_applied"] == "status_not_cancellable"
    # The waiver exists for this account and is deliberately not applied.
    assert result["waiver_available"] is True
    assert result["waiver_applied"] is False


def test_a5_axis_ord_4001_is_delivered(db):
    result = calc.cancellation_fee(db, "ORD-4001")

    assert result["cancellable"] is False
    assert result["fee_inr"] is None
    assert result["blocked"]["reason"] == "already_delivered"


def test_cancellation_is_scoped_when_an_account_is_supplied(db):
    """The calculators honour scope too, so no path around the gate exists."""
    assert calc.cancellation_fee(db, "ORD-1001", account_id="ACCT-002")["found"] is False
    assert calc.cancellation_fee(db, "ORD-1001", account_id="ACCT-001")["found"] is True


def test_unknown_order_is_a_clean_miss(db):
    result = calc.cancellation_fee(db, "ORD-9999")
    assert result["found"] is False
    assert result.get("fee_inr") is None


# --------------------------------------------------------------------------
# Section B -- service credits
# --------------------------------------------------------------------------


def test_b1_lumenworks_ord_2002_gets_the_contract_amount(db):
    """4h30m past window end, carrier fault, no customer fault -> fixed 300."""
    result = calc.service_credit(db, "ORD-2002")

    assert result["eligible"] is True
    assert result["amount_inr"] == 300
    assert result["rule_applied"] == "contract_fixed_amount"
    assert result["hours_past_window_end"] == pytest.approx(4.5)
    assert result["threshold_hours"] == 4
    assert result["source"]["doc_id"] == "06_LumenWorks_Service_Agreement"
    assert result["source"]["clause"] == "§3"
    assert result["needs_manager_approval"] is False
    assert result["unknowns"] == []
    # The default would have been min(500, 10% of 2400) = 240, so the contract
    # is the more generous source here and the answer should say so.
    assert result["default_outcome"]["amount_inr"] == 240
    assert result["contract_effect"] == "more_generous"
    assert result["conflicts"]


def test_b2_lumenworks_at_three_hours_is_not_eligible(db):
    """Canary: the contract is *worse* than the default here. 4h threshold."""
    result = calc.service_credit(
        db,
        account_id="ACCT-002",
        hours_past_window_end=3,
        carrier_fault=True,
        customer_fault=False,
        shipment_fee_inr=1800,
    )

    assert result["eligible"] is False
    assert result["amount_inr"] is None
    assert result["threshold_hours"] == 4
    assert result["rule_applied"] == "below_threshold"
    assert result["source"]["doc_id"] == "06_LumenWorks_Service_Agreement"
    # Under the SOP default it would have qualified; that must be visible.
    assert result["default_outcome"]["eligible"] is True
    assert result["contract_effect"] == "less_generous"
    assert result["conflicts"]


def test_b3_northstar_at_three_hours_falls_back_to_the_sop(db):
    """No failed-pickup clause in the Northstar agreement -> SOP default."""
    result = calc.service_credit(
        db,
        account_id="ACCT-001",
        hours_past_window_end=3,
        carrier_fault=True,
        customer_fault=False,
        shipment_fee_inr=4200,
    )

    assert result["eligible"] is True
    assert result["threshold_hours"] == 2
    assert result["amount_inr"] == 420  # min(500, 10% of 4200)
    assert result["rule_applied"] == "sop_default_amount"
    assert result["source"]["doc_id"] == "03_Cancellation_and_Service_Credit_SOP_v4"
    # The agreement still contributes its aggregate cap.
    assert result["monthly_cap_inr"] == 5000
    assert result["monthly_cap_source"]["doc_id"] == "05_Northstar_Logistics_Enterprise_Agreement"


def test_b4_beacon_at_three_hours_is_pure_sop(db):
    result = calc.service_credit(
        db,
        account_id="ACCT-003",
        hours_past_window_end=3,
        carrier_fault=True,
        customer_fault=False,
        shipment_fee_inr=1200,
    )

    assert result["eligible"] is True
    assert result["amount_inr"] == 120  # min(500, 10% of 1200)
    assert result["threshold_hours"] == 2
    assert result["monthly_cap_inr"] is None
    assert result["conflicts"] == []


def test_b5_unknown_fault_is_never_promised(db):
    """SOP §3 forbids promising under uncertainty; 'unknown' is not 'no'."""
    result = calc.service_credit(
        db,
        account_id="ACCT-003",
        hours_past_window_end=6,
        carrier_fault=None,
        customer_fault=False,
        shipment_fee_inr=1200,
    )

    assert result["eligible"] == "unknown"
    assert result["amount_inr"] is None
    assert "carrier_fault" in result["unknowns"]
    assert result["must_escalate"] is True
    assert result["rule_applied"] == "insufficient_information"


def test_b5b_unknown_timing_is_also_blocking(db):
    result = calc.service_credit(
        db,
        account_id="ACCT-003",
        hours_past_window_end=None,
        carrier_fault=True,
        customer_fault=False,
        shipment_fee_inr=1200,
    )
    assert result["eligible"] == "unknown"
    assert "pickup_timing" in result["unknowns"]


def test_b6_large_fee_still_caps_at_five_hundred(db):
    result = calc.service_credit(
        db,
        account_id="ACCT-001",
        hours_past_window_end=3,
        carrier_fault=True,
        customer_fault=False,
        shipment_fee_inr=20000,
    )

    assert result["eligible"] is True
    assert result["amount_inr"] == 500  # min(500, 2000)
    assert result["needs_manager_approval"] is False


def test_manager_approval_branch_exists_even_though_the_pack_never_hits_it(db):
    """The >INR 1,000 branch is unexercised by the supplied data.

    Driven here with an explicit contract-style override so the branch is not
    dead code in the submission.
    """
    result = calc.evaluate_credit_amount(db, account_id="ACCT-001", amount_inr=1500)
    assert result["needs_manager_approval"] is True
    assert result["threshold_inr"] == 1000
    assert result["source"]["clause"] == "§3"

    assert calc.evaluate_credit_amount(db, account_id="ACCT-001", amount_inr=1000)[
        "needs_manager_approval"
    ] is False


def test_customer_fault_blocks_eligibility(db):
    result = calc.service_credit(
        db,
        account_id="ACCT-003",
        hours_past_window_end=6,
        carrier_fault=True,
        customer_fault=True,
        shipment_fee_inr=1200,
    )
    assert result["eligible"] is False
    assert result["rule_applied"] == "customer_at_fault"


def test_credit_without_a_fee_is_eligible_but_unquantified(db):
    """B3 as literally asked: no fee given, so no amount can honestly be stated."""
    result = calc.service_credit(
        db,
        account_id="ACCT-001",
        hours_past_window_end=3,
        carrier_fault=True,
        customer_fault=False,
        shipment_fee_inr=None,
    )
    assert result["eligible"] is True
    assert result["amount_inr"] is None
    assert "shipment_fee_inr" in result["unknowns"]
    assert result["amount_formula"] == "min(500, 10% of shipment fee)"


# --------------------------------------------------------------------------
# Section C -- severity and SLA
# --------------------------------------------------------------------------


def test_c1_tkt_501_breaches_the_northstar_fifteen_minute_target(db):
    result = calc.sla_status(db, "TKT-501", severity="P1")

    assert result["target_minutes"] == 15
    assert result["target_text"] == "15 minutes, 24x7"
    assert result["elapsed_minutes"] == pytest.approx(30)
    assert result["breached"] is True
    assert result["breach_by_minutes"] == pytest.approx(15)
    assert result["source"]["doc_id"] == "05_Northstar_Logistics_Enterprise_Agreement"
    assert result["source"]["tier"] == 1
    assert result["basis"] == "wall_clock"


def test_c2_tkt_505_breaches_the_enterprise_default_by_two_hours(db):
    """Canary: Enterprise P1 is 30 minutes (v3), never 1 hour (deprecated v2)."""
    result = calc.sla_status(db, "TKT-505", severity="P1")

    assert result["target_minutes"] == 30
    assert result["target_minutes"] != 60, "deprecated v2 policy leaked into retrieval"
    assert result["source"]["doc_id"] == "01_Support_Policy_v3_CURRENT"
    assert result["elapsed_minutes"] == pytest.approx(150)
    assert result["breached"] is True
    assert result["breach_by_minutes"] == pytest.approx(120)


def test_c3_tkt_502_is_inside_the_lumenworks_target(db):
    result = calc.sla_status(db, "TKT-502", severity="P2")

    assert result["target_text"] == "4 business hours"
    assert result["business_hours_target"] == 4
    assert result["elapsed_minutes"] == pytest.approx(75)
    assert result["breached"] is False
    assert result["source"]["doc_id"] == "06_LumenWorks_Service_Agreement"


def test_c4_tkt_503_is_inside_the_standard_target(db):
    result = calc.sla_status(db, "TKT-503", severity="P3")

    assert result["target_text"] == "2 business days"
    assert result["elapsed_minutes"] == pytest.approx(55)
    assert result["breached"] is False
    assert result["source"]["doc_id"] == "01_Support_Policy_v3_CURRENT"


def test_c5_tkt_504_is_inside_the_northstar_p3_target(db):
    result = calc.sla_status(db, "TKT-504", severity="P3")

    assert result["target_text"] == "8 business hours"
    assert result["elapsed_minutes"] == pytest.approx(10)
    assert result["breached"] is False
    assert result["source"]["doc_id"] == "05_Northstar_Logistics_Enterprise_Agreement"


@pytest.mark.parametrize(
    "ticket_id,expected",
    [
        ("TKT-501", "P1"),
        ("TKT-502", "P2"),
        ("TKT-503", "P3"),
        ("TKT-504", "P3"),
        ("TKT-505", "P1"),
    ],
)
def test_severity_classification_matches_the_eval_set(db, ticket_id, expected):
    assert calc.classify_severity(db, ticket_id)["severity"] == expected


def test_tkt_505_is_p1_because_of_credential_exposure(db):
    """The sleeper case: phrased as a question, but v3 §2 makes it P1."""
    classified = calc.classify_severity(db, "TKT-505")
    assert classified["severity"] == "P1"
    assert classified["source"]["doc_id"] == "01_Support_Policy_v3_CURRENT"
    assert classified["source"]["clause"] == "§2"


# --------------------------------------------------------------------------
# The deprecated-policy canary, stated directly
# --------------------------------------------------------------------------


def test_enterprise_p1_never_resolves_to_the_deprecated_one_hour(db):
    for account_id, plan in (("ACCT-004", "Enterprise"), (None, "Enterprise")):
        resolved = calc.resolve_sla_target(db, account_id=account_id, plan=plan, severity="P1")
        assert resolved["target_minutes"] == 30
        assert resolved["source"]["doc_id"] == "01_Support_Policy_v3_CURRENT"


def test_deprecated_targets_are_reachable_only_on_explicit_internal_opt_in(db):
    opted_in = calc.resolve_sla_target(
        db, account_id=None, plan="Enterprise", severity="P1", include_deprecated=True
    )
    # Even opted in, the current policy still wins on tier; the deprecated row
    # is visible as history, not as the answer.
    assert opted_in["target_minutes"] == 30
    assert any(
        candidate["doc_id"] == "02_Support_Policy_v2_DEPRECATED"
        for candidate in opted_in["all_candidates"]
    )

    default = calc.resolve_sla_target(db, account_id=None, plan="Enterprise", severity="P1")
    assert all(
        candidate["doc_id"] != "02_Support_Policy_v2_DEPRECATED"
        for candidate in default["all_candidates"]
    )


# --------------------------------------------------------------------------
# Business-hours arithmetic (the snapshot day is a Sunday)
# --------------------------------------------------------------------------


def test_business_minutes_skip_the_weekend(db):
    from datetime import datetime

    from app.config import IST

    friday_five = datetime(2026, 8, 14, 17, 0, tzinfo=IST)
    monday_ten = datetime(2026, 8, 17, 10, 0, tzinfo=IST)
    # One hour Friday afternoon plus one hour Monday morning.
    assert calc.business_minutes_between(friday_five, monday_ten) == pytest.approx(120)


def test_snapshot_day_is_a_sunday_so_business_clocks_have_not_started(db):
    from datetime import datetime

    from app.config import IST

    assert snapshot_now().weekday() == 6
    sunday_morning = datetime(2026, 8, 16, 9, 45, tzinfo=IST)
    assert calc.business_minutes_between(sunday_morning, snapshot_now()) == 0
