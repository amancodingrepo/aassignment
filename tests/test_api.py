"""HTTP surface: personas, session cookie, 403s, confirm, reset."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.session import Session
from app.tools import actions, gate


@pytest.fixture
def client(db):
    import app.main as main_mod

    previous = main_mod._conn
    main_mod._conn = db
    with TestClient(main_mod.app) as test_client:
        yield test_client
    main_mod._conn = previous
    actions.STORE.clear()


def _adopt(client: TestClient, persona_id: str) -> dict:
    response = client.post("/api/session", json={"persona_id": persona_id})
    assert response.status_code == 200, response.text
    return response.json()["session"]


def test_health_reports_the_snapshot(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["snapshot_now"].startswith("2026-08-16T11:00")
    assert "chat_ready" in body
    assert body["chunks"] >= 15
    assert body["orders"] == 6
    assert body["tickets"] == 7
    alias = client.get("/health")
    assert alias.status_code == 200
    assert alias.json()["ok"] is True


def test_personas_include_four_customers_and_two_internal(client):
    body = client.get("/api/personas").json()
    ids = {persona["id"] for persona in body["personas"]}
    assert {
        "customer-ACCT-001",
        "customer-ACCT-002",
        "customer-ACCT-003",
        "customer-ACCT-004",
        "internal-support",
        "internal-ops",
    } <= ids
    assert body["snapshot_now"].startswith("2026-08-16T11:00")
    assert "chat_ready" in body
    assert "mocked" in body["auth_note"].lower()


def test_adopted_session_carries_persona_id(client):
    session = _adopt(client, "internal-ops")
    assert session["persona_id"] == "internal-ops"
    assert session["role"] == "internal"
    assert session["account_id"] is None

    customer = _adopt(client, "customer-ACCT-002")
    assert customer["persona_id"] == "customer-ACCT-002"
    assert customer["account_id"] == "ACCT-002"


def test_unknown_persona_is_404(client):
    response = client.post("/api/session", json={"persona_id": "customer-ACCT-999"})
    assert response.status_code == 404


def test_session_required_for_protected_routes(client):
    assert client.get("/api/session").status_code == 401
    assert client.get("/api/signals").status_code == 401
    assert client.get("/api/audit").status_code == 401
    assert client.post("/api/confirm", json={"token": "x"}).status_code == 401


def test_customer_cannot_read_signals_or_audit(client):
    _adopt(client, "customer-ACCT-002")
    assert client.get("/api/signals").status_code == 403
    assert client.get("/api/audit").status_code == 403
    assert client.post("/api/reset").status_code == 403


def test_internal_can_read_signals_and_audit(client):
    _adopt(client, "internal-ops")
    signals = client.get("/api/signals")
    assert signals.status_code == 200
    kinds = {item["kind"] for item in signals.json()["signals"]}
    assert "sla_breach" in kinds
    assert "stale_guidance" in kinds

    audit = client.get("/api/audit")
    assert audit.status_code == 200


def test_confirm_without_a_token_is_400(client):
    _adopt(client, "internal-ops")
    response = client.post("/api/confirm", json={})
    assert response.status_code == 400


def test_confirm_endpoint_is_the_only_write_path(client, db):
    adopted = _adopt(client, "internal-ops")
    session = Session(
        session_id=adopted["session_id"],
        role=adopted["role"],
        account_id=adopted["account_id"],
        user_name=adopted["user_name"],
        internal_permissions=adopted["internal_permissions"],
    )
    proposal = gate.call_tool(
        db, session, "propose_action", {"kind": "escalation", "ticket_id": "TKT-505"}
    )
    assert proposal["ok"] is True
    before = db.execute("SELECT COUNT(*) AS n FROM actions").fetchone()["n"]

    confirmed = client.post("/api/confirm", json={"token": proposal["token"]})
    assert confirmed.status_code == 200
    body = confirmed.json()
    assert body["ok"] is True
    assert body["action_id"].startswith("ESC-")
    after = db.execute("SELECT COUNT(*) AS n FROM actions").fetchone()["n"]
    assert after == before + 1

    replay = client.post("/api/confirm", json={"token": proposal["token"]})
    assert replay.status_code == 409


def test_reset_clears_actions(client, db):
    adopted = _adopt(client, "internal-ops")
    session = Session(
        session_id=adopted["session_id"],
        role="internal",
        account_id=None,
        user_name=adopted["user_name"],
        internal_permissions=adopted["internal_permissions"],
    )
    proposal = gate.call_tool(
        db, session, "propose_action", {"kind": "escalation", "ticket_id": "TKT-501"}
    )
    assert client.post("/api/confirm", json={"token": proposal["token"]}).status_code == 200
    assert db.execute("SELECT COUNT(*) AS n FROM actions").fetchone()["n"] >= 1

    reset = client.post("/api/reset")
    assert reset.status_code == 200
    assert db.execute("SELECT COUNT(*) AS n FROM actions").fetchone()["n"] == 0
    assert db.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"] == 0
