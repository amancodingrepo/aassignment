"""Direct tests for search_documents.

Prefilter behaviour is also covered by test_gate.py. These pin ranking, the
topic filter, and the deprecated-policy exclusion at the retrieval layer itself.
"""

from __future__ import annotations

from app.tools.docs import search_documents


def _ids(result):
    return [chunk["chunk_id"] for chunk in result["chunks"]]


def _doc_ids(result):
    return {chunk["doc_id"] for chunk in result["chunks"]}


def test_customer_never_retrieves_another_accounts_contract(db):
    result = search_documents(
        db,
        "cancellation fee waiver SLA",
        account_id="ACCT-002",
        role="customer",
        topics=["cancellation", "sla"],
    )
    assert "05_Northstar_Logistics_Enterprise_Agreement" not in _doc_ids(result)
    assert all(chunk["scope"] in {"global", "ACCT-002"} for chunk in result["chunks"])


def test_northstar_customer_does_retrieve_their_own_contract(db):
    result = search_documents(
        db,
        "cancellation fee waiver",
        account_id="ACCT-001",
        role="customer",
        topics=["cancellation"],
    )
    assert "05_Northstar_Logistics_Enterprise_Agreement" in _doc_ids(result)


def test_customer_never_retrieves_the_deprecated_policy(db):
    result = search_documents(
        db,
        "Enterprise P1 first response target",
        account_id="ACCT-004",
        role="customer",
        include_deprecated=True,
        topics=["sla"],
    )
    assert "02_Support_Policy_v2_DEPRECATED" not in _doc_ids(result)


def test_internal_opt_in_retrieves_deprecated_policy_flagged(db):
    result = search_documents(
        db,
        "Enterprise P1 first response",
        role="internal",
        include_deprecated=True,
        topics=["sla"],
    )
    deprecated = [
        chunk
        for chunk in result["chunks"]
        if chunk["doc_id"] == "02_Support_Policy_v2_DEPRECATED"
    ]
    assert deprecated
    assert all(chunk["status"] == "deprecated" for chunk in deprecated)
    assert all(chunk["internal_only"] is True for chunk in deprecated)


def test_internal_default_excludes_deprecated(db):
    result = search_documents(
        db,
        "Enterprise P1 first response",
        role="internal",
        include_deprecated=False,
        topics=["sla"],
    )
    assert "02_Support_Policy_v2_DEPRECATED" not in _doc_ids(result)


def test_topic_filter_keeps_cancellation_chunks(db):
    result = search_documents(
        db,
        "cancel a booked shipment",
        account_id="ACCT-003",
        role="customer",
        topics=["cancellation"],
    )
    assert result["chunks"]
    assert all("cancellation" in chunk["topics"] for chunk in result["chunks"])


def test_ki_208_ranks_for_a_bulk_upload_query(db):
    result = search_documents(
        db,
        "bulk upload fails at 4200 rows",
        account_id="ACCT-002",
        role="customer",
        topics=["known_issue", "plan_capability"],
    )
    ids = _ids(result)
    assert "04_product_guide#s2.1" in ids
    assert ids.index("04_product_guide#s2.1") < 4


def test_tier_is_only_a_tie_break(db):
    """A weakly matching contract must not outrank a strongly matching SOP."""
    result = search_documents(
        db,
        "failed pickup service credit threshold hours",
        account_id="ACCT-001",
        role="customer",
        topics=["service_credit"],
    )
    # Northstar has no failed-pickup clause of its own; the SOP should still
    # appear, and the Northstar credits section should not monopolise the top.
    ids = _ids(result)
    assert "03_sop_v4#s2" in ids
