"""Phase 0 gate, as assertions.

The build plan calls for eyeballing a printed chunk dump. That is the right
first check, but it does not survive into CI, and an ingest regression is
invisible by the time it surfaces as a wrong number in the chat window. These
tests pin the metadata the whole system reasons over.
"""

from __future__ import annotations

import pytest

from app.authority import load_clauses
from app.ingest.documents import parse_target
from app.ingest.workbook import parse_snapshot


def _by_id(db):
    return {clause.chunk_id: clause for clause in load_clauses(db)}


def test_every_document_produced_chunks(db):
    docs = {clause.doc_id for clause in load_clauses(db)}
    assert len(docs) == 6


def test_chunk_count_is_in_the_expected_range(db):
    # 03_SOURCE_AUTHORITY.md estimates about twenty chunks. A wild departure
    # means the section markers stopped matching the PDFs.
    assert 15 <= len(load_clauses(db)) <= 25


def test_tiers_are_assigned_as_the_ladder_specifies(db):
    expected = {
        "05_Northstar_Logistics_Enterprise_Agreement": 1,
        "06_LumenWorks_Service_Agreement": 1,
        "03_Cancellation_and_Service_Credit_SOP_v4": 2,
        "01_Support_Policy_v3_CURRENT": 3,
        "04_Product_Operations_Guide_and_Known_Issues": 4,
        "02_Support_Policy_v2_DEPRECATED": 5,
    }
    for clause in load_clauses(db):
        assert clause.tier == expected[clause.doc_id]


def test_contracts_are_scoped_to_their_account(db):
    scopes = {
        clause.doc_id: clause.scope for clause in load_clauses(db) if clause.tier == 1
    }
    assert scopes["05_Northstar_Logistics_Enterprise_Agreement"] == "ACCT-001"
    assert scopes["06_LumenWorks_Service_Agreement"] == "ACCT-002"


def test_the_deprecated_policy_is_marked_and_out_of_window(db):
    deprecated = [
        clause
        for clause in load_clauses(db)
        if clause.doc_id == "02_Support_Policy_v2_DEPRECATED"
    ]
    assert deprecated
    for clause in deprecated:
        assert clause.status == "deprecated"
        assert clause.internal_only is True
        assert clause.effective_to == "2026-04-30"


def test_the_resolved_known_issue_carries_its_flag(db):
    resolved = [
        clause
        for clause in load_clauses(db)
        if clause.flags.get("issue_id") == "KI-176"
    ]
    assert len(resolved) == 1
    assert resolved[0].status == "resolved"
    assert resolved[0].flags["do_not_apply_to_new_incidents"] is True


def test_chunk_text_came_from_the_pdfs(db):
    clauses = _by_id(db)
    assert "return-to-origin" in clauses["03_sop_v4#s1"].text
    assert "15 minutes" in clauses["05_northstar#s1"].text
    assert "INR 300" in clauses["06_lumenworks#s3"].text
    assert "5,000 rows" in clauses["04_product_guide#s1"].text


def test_the_two_sla_tables_are_stored_separately(db):
    rows = db.execute(
        "SELECT doc_id, target_text FROM sla_targets "
        "WHERE plan = 'Enterprise' AND severity = 'P1'"
    ).fetchall()
    by_doc = {row["doc_id"]: row["target_text"] for row in rows}

    assert by_doc["01_Support_Policy_v3_CURRENT"] == "30 minutes, 24x7"
    assert by_doc["02_Support_Policy_v2_DEPRECATED"] == "1 hour"


def test_contract_targets_are_parsed_from_bullets(db):
    rows = db.execute(
        "SELECT severity, target_text, target_minutes, business_hours FROM sla_targets "
        "WHERE doc_id = '05_Northstar_Logistics_Enterprise_Agreement' ORDER BY severity"
    ).fetchall()
    parsed = {row["severity"]: row for row in rows}

    assert parsed["P1"]["target_minutes"] == 15
    assert parsed["P2"]["target_minutes"] == 60
    assert parsed["P3"]["business_hours"] == 8


@pytest.mark.parametrize(
    "text,expected",
    [
        ("30 minutes, 24x7", {"target_minutes": 30, "coverage": "24x7"}),
        ("1 hour", {"target_minutes": 60, "coverage": None}),
        ("2 hours", {"target_minutes": 120, "coverage": None}),
        ("4 business hours", {"business_hours": 4}),
        ("2 business days", {"business_days": 2}),
        ("1 business day", {"business_days": 1}),
    ],
)
def test_target_parsing(text, expected):
    parsed = parse_target(text)
    for key, value in expected.items():
        assert parsed[key] == value


def test_snapshot_parsing_accepts_the_readme_form():
    parsed = parse_snapshot("2026-08-16 11:00 Asia/Kolkata")
    assert parsed.hour == 11
    assert parsed.utcoffset().total_seconds() == 5.5 * 3600


def test_derived_order_fields(db):
    rows = {row["order_id"]: row for row in db.execute("SELECT * FROM orders")}

    assert rows["ORD-1001"]["minutes_booked_to_cancel_request"] == 120
    assert rows["ORD-2001"]["minutes_booked_to_cancel_request"] == 75
    assert rows["ORD-3001"]["minutes_booked_to_cancel_request"] == 15
    assert rows["ORD-2002"]["hours_past_pickup_window_end"] == pytest.approx(4.5)
    assert rows["ORD-1002"]["is_cancellable_status"] == 0
    assert rows["ORD-4001"]["is_cancellable_status"] == 0
    assert rows["ORD-1001"]["is_cancellable_status"] == 1


def test_fault_flags_survive_as_booleans_and_nulls(db):
    row = db.execute("SELECT * FROM orders WHERE order_id = 'ORD-2002'").fetchone()
    assert row["carrier_fault"] == 1
    assert row["customer_fault"] == 0


def test_no_module_reads_the_wall_clock():
    """Guard for the rule that "now" is the snapshot.

    `time.monotonic` in the confirm-token expiry is the one deliberate
    exception, and it is not a wall clock.
    """
    import pathlib
    import re

    offenders = []
    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    for path in root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for number, line in enumerate(source.splitlines(), start=1):
            if re.search(r"datetime\.now\(|date\.today\(|time\.time\(", line):
                offenders.append(f"{path.name}:{number}")
    assert offenders == [], f"wall-clock time used in: {offenders}"
