"""FastAPI entrypoint: API plus the built React bundle from one container.

Routes:
    GET  /api/personas    the mocked login options
    POST /api/session     adopt a persona, set a signed cookie
    GET  /api/session     who am I
    POST /api/chat        SSE stream of the agent turn
    POST /api/confirm     the ONLY path that writes an action
    POST /api/cancel      mark a proposal cancelled
    GET  /api/signals     internal-only proactive queue
    GET  /api/actions     what has been written
    GET  /api/audit       internal-only gated-call log
    POST /api/reset       clear actions and audit between demo takes
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import Body, Cookie, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeSerializer

from app.agent.loop import MissingApiKey, run_turn
from app.config import DB_PATH, REPO_ROOT, SESSION_SECRET, snapshot_now
from app.db import connect
from app.ingest.build import ensure_loaded
from app.session import Session, personas, session_from_persona
from app.signals.detect import detect_signals
from app.tools import actions as actions_module
from app.tools import data as data_module
from app.tools import gate

COOKIE_NAME = "parcelpilot_session"
STATIC_DIR = REPO_ROOT / "web" / "dist"

app = FastAPI(title="ParcelPilot AI Support Agent")
serializer = URLSafeSerializer(SESSION_SECRET, salt="parcelpilot-session")

_conn = None


def db():
    global _conn
    if _conn is None:
        _conn = ensure_loaded(db_path=DB_PATH)
    return _conn


@app.on_event("startup")
def _startup() -> None:
    db()


def _read_session(cookie: str | None) -> Session:
    if not cookie:
        raise HTTPException(status_code=401, detail="no session; pick a persona first")
    try:
        payload = serializer.loads(cookie)
    except BadSignature as error:
        raise HTTPException(status_code=401, detail="invalid session") from error
    return Session(
        session_id=payload["session_id"],
        role=payload["role"],
        account_id=payload.get("account_id"),
        user_name=payload["user_name"],
        internal_permissions=payload.get("internal_permissions", []),
    )


def _require_internal(session: Session) -> None:
    if not session.is_internal:
        raise HTTPException(status_code=403, detail="internal only")


@app.get("/api/personas")
def get_personas() -> dict[str, Any]:
    return {
        "personas": personas(db()),
        "snapshot_now": snapshot_now().isoformat(),
        "auth_note": (
            "Authentication is mocked for this demo: choosing a persona sets a "
            "signed cookie, with no password. Access control is not mocked -- "
            "the account bound to the session is injected into every scoped "
            "query by the tool gate."
        ),
    }


@app.post("/api/session")
def create_session(response: Response, persona_id: str = Body(..., embed=True)) -> dict[str, Any]:
    try:
        session = session_from_persona(db(), persona_id, uuid.uuid4().hex)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="unknown persona") from error

    response.set_cookie(
        COOKIE_NAME,
        serializer.dumps(session.to_public()),
        httponly=True,
        samesite="lax",
    )
    return {"session": session.to_public()}


@app.get("/api/session")
def read_session(parcelpilot_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    return {"session": _read_session(parcelpilot_session).to_public()}


@app.post("/api/chat")
def chat(
    payload: dict[str, Any] = Body(...),
    parcelpilot_session: str | None = Cookie(default=None),
) -> StreamingResponse:
    session = _read_session(parcelpilot_session)
    messages = payload.get("messages") or []
    if not messages:
        raise HTTPException(status_code=400, detail="messages is required")

    def stream() -> Iterator[str]:
        try:
            for event in run_turn(db(), session, messages):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except MissingApiKey as error:
            yield f"data: {json.dumps({'type': 'error', 'error': str(error)})}\n\n"
        except Exception as error:  # surfaced, never swallowed
            yield f"data: {json.dumps({'type': 'error', 'error': repr(error)})}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/confirm")
def confirm(
    payload: dict[str, Any] = Body(...),
    parcelpilot_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """The only endpoint in the system that writes an action."""
    session = _read_session(parcelpilot_session)
    token = payload.get("token")
    if not token:
        raise HTTPException(status_code=400, detail="token is required")

    result = actions_module.execute_confirmed(
        db(), session, token, edited_payload=payload.get("payload")
    )
    gate.audit(
        db(),
        session,
        "confirm",
        {"token": "***", "ok": result.get("ok")},
        allowed=bool(result.get("ok")),
        denial_reason=None if result.get("ok") else result.get("error"),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error"))
    return result


@app.post("/api/cancel")
def cancel(
    payload: dict[str, Any] = Body(...),
    parcelpilot_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    session = _read_session(parcelpilot_session)
    result = actions_module.cancel_proposal(session, payload.get("token", ""))
    gate.audit(
        db(),
        session,
        "cancel_proposal",
        {"token": "***"},
        allowed=bool(result.get("ok")),
        denial_reason=None if result.get("ok") else result.get("error"),
    )
    return result


@app.get("/api/signals")
def signals(parcelpilot_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    session = _read_session(parcelpilot_session)
    _require_internal(session)
    return {
        "signals": [signal.to_dict() for signal in detect_signals(db())],
        "computed_at": snapshot_now().isoformat(),
        "note": (
            "Recomputed on request. The dataset is a frozen snapshot, so a "
            "scheduled job here would be pretend infrastructure."
        ),
    }


@app.get("/api/actions")
def list_actions(parcelpilot_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    session = _read_session(parcelpilot_session)
    return data_module.list_actions(db(), account_id=session.account_id, limit=50)


@app.get("/api/audit")
def audit_log(parcelpilot_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    session = _read_session(parcelpilot_session)
    _require_internal(session)
    return data_module.read_audit_log(db(), limit=100)


@app.post("/api/reset")
def reset(parcelpilot_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    """Clear written actions and the audit log between demo takes.

    Deliberately does not touch the ingested source data -- rebuilding that is
    `ensure_loaded(rebuild=True)` and is not something a stray POST should do.
    """
    session = _read_session(parcelpilot_session)
    _require_internal(session)
    connection = db()
    connection.execute("DELETE FROM actions")
    connection.execute("DELETE FROM audit_log")
    connection.commit()
    actions_module.STORE.clear()
    return {"ok": True, "cleared": ["actions", "audit_log", "pending_proposals"]}


@app.get("/api/health")
def health() -> dict[str, Any]:
    connection = db()
    return {
        "ok": True,
        "snapshot_now": snapshot_now().isoformat(),
        "chunks": connection.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"],
        "orders": connection.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"],
        "tickets": connection.execute("SELECT COUNT(*) AS n FROM tickets").fetchone()["n"],
    }


# The built React bundle, served from the same container. Mounted last so it
# never shadows an API route.
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
