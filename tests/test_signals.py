"""Detector tests, against the expected outputs named in 05_SIGNALS.md."""

from __future__ import annotations

from app.signals.detect import detect_signals


def _by_kind(signals, kind):
    return [signal for signal in signals if signal.kind == kind]


def _ids(signals):
    return {signal.id for signal in signals}


def test_both_breached_p1s_are_detected(db):
    breaches = _by_kind(detect_signals(db), "sla_breach")
    tickets = {signal.evidence[0]["ticket_id"] for signal in breaches}

    assert "TKT-501" in tickets
    assert "TKT-505" in tickets
    assert all(signal.severity == "P1" for signal in breaches)


def test_tkt_501_breach_uses_the_contract_target(db):
    breach = next(
        signal
        for signal in _by_kind(detect_signals(db), "sla_breach")
        if signal.evidence[0]["ticket_id"] == "TKT-501"
    )
    assert breach.evidence[0]["target"] == "15 minutes, 24x7"
    assert breach.evidence[0]["breach_by_minutes"] == 15.0


def test_tkt_505_breach_uses_the_current_enterprise_default(db):
    breach = next(
        signal
        for signal in _by_kind(detect_signals(db), "sla_breach")
        if signal.evidence[0]["ticket_id"] == "TKT-505"
    )
    assert breach.evidence[0]["target"] == "30 minutes, 24x7"
    assert breach.evidence[0]["breach_by_minutes"] == 120.0


def test_tickets_inside_their_target_do_not_raise_a_breach(db):
    breaches = _by_kind(detect_signals(db), "sla_breach")
    tickets = {signal.evidence[0]["ticket_id"] for signal in breaches}
    assert not tickets & {"TKT-502", "TKT-503", "TKT-504"}


def test_ki_208_is_raised_as_a_recurrence(db):
    root_causes = _by_kind(detect_signals(db), "repeated_root_cause")
    ki208 = next(signal for signal in root_causes if "KI-208" in signal.id)

    linked = {item["ticket_id"] for item in ki208.evidence}
    assert {"TKT-502", "TKT-451"} <= linked


def test_the_resolved_known_issue_never_raises_a_signal(db):
    """KI-176 is resolved and the guide forbids applying it to new incidents."""
    assert not [signal for signal in detect_signals(db) if "KI-176" in signal.id]


def test_swiftship_concentration_is_flagged_across_accounts(db):
    carriers = _by_kind(detect_signals(db), "carrier_concentration")
    swiftship = next(signal for signal in carriers if "SWIFTSHIP" in signal.id)

    assert len(swiftship.evidence) >= 2
    assert {"ACCT-001", "ACCT-004"} <= set(swiftship.affected_accounts)


def test_cancellation_spike_is_raised_and_labelled_as_assumed(db):
    spikes = _by_kind(detect_signals(db), "cancellation_spike")
    assert len(spikes) == 1
    assert len(spikes[0].evidence) == 4
    assert "assumed" in spikes[0].detail.lower()


def test_multi_account_impact_escalates_a_level(db):
    multi = _by_kind(detect_signals(db), "multi_account_impact")
    assert multi
    assert all(signal.severity == "P1" for signal in multi)
    assert all(len(signal.affected_accounts) >= 2 for signal in multi)


def test_both_stale_guidance_tickets_are_found(db):
    stale = _by_kind(detect_signals(db), "stale_guidance")
    tickets = {signal.evidence[0]["ticket_id"] for signal in stale}
    assert tickets == {"TKT-450", "TKT-451"}


def test_tkt_450_is_flagged_against_the_contract_waiver(db):
    stale = _by_kind(detect_signals(db), "stale_guidance")
    tkt450 = next(signal for signal in stale if "TKT-450" in signal.id)

    assert "waives the cancellation fee" in tkt450.detail
    assert tkt450.sources[0]["doc_id"] == "05_Northstar_Logistics_Enterprise_Agreement"


def test_tkt_451_is_flagged_against_the_product_guide(db):
    stale = _by_kind(detect_signals(db), "stale_guidance")
    tkt451 = next(signal for signal in stale if "TKT-451" in signal.id)

    assert "5,000 rows" in tkt451.detail
    assert tkt451.sources[0]["doc_id"] == "04_Product_Operations_Guide_and_Known_Issues"


def test_every_signal_explains_its_own_rank(db):
    for signal in detect_signals(db):
        assert signal.rank_score > 0
        assert set(signal.rank_terms) == {"severity", "account_impact", "breach_magnitude"}
        assert signal.evidence, f"{signal.id} has no evidence rows"
        assert signal.seed_query, f"{signal.id} cannot seed a chat"


def test_signals_are_ranked_most_urgent_first(db):
    scores = [signal.rank_score for signal in detect_signals(db)]
    assert scores == sorted(scores, reverse=True)
