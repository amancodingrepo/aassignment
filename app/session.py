"""Session model and the mocked personas.

Auth is mocked: picking a persona from a dropdown sets a signed cookie, and
there is no password. What is *not* mocked is what the session is used for --
the account_id on this object is the only thing that decides which rows a
customer-scoped query can reach, and it is never supplied by the model.

In a real deployment this object would be built from a verified OIDC token; the
rest of the system would not change, because nothing below this line trusts
anything except `Session.account_id` and `Session.role`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

CUSTOMER = "customer"
INTERNAL = "internal"

READ_ALL = "read_all"
WRITE_ACTIONS = "write_actions"

# Internal staff are app configuration, not customer data, so they are listed
# here. Customer personas are derived from the accounts table -- adding a fifth
# account to the workbook must not require a code change.
INTERNAL_PERSONAS = [
    {
        "id": "internal-support",
        "user_name": "Rohit (Support Agent)",
        "internal_permissions": [READ_ALL, WRITE_ACTIONS],
    },
    {
        "id": "internal-ops",
        "user_name": "Maya (Ops Manager)",
        "internal_permissions": [READ_ALL, WRITE_ACTIONS],
    },
]


@dataclass
class Session:
    session_id: str
    role: str
    account_id: str | None
    user_name: str
    internal_permissions: list[str] = field(default_factory=list)
    persona_id: str | None = None

    def __post_init__(self) -> None:
        if self.role not in (CUSTOMER, INTERNAL):
            raise ValueError(f"unknown role {self.role!r}")
        if self.role == CUSTOMER and not self.account_id:
            raise ValueError("a customer session must be bound to an account")
        if self.role == INTERNAL:
            # An internal session is not "an account with more permissions"; it
            # has no account at all, so an accidentally unscoped customer query
            # cannot borrow one.
            self.account_id = None

    @property
    def is_internal(self) -> bool:
        return self.role == INTERNAL

    def can(self, permission: str) -> bool:
        return permission in self.internal_permissions

    def to_public(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "role": self.role,
            "account_id": self.account_id,
            "user_name": self.user_name,
            "internal_permissions": list(self.internal_permissions),
            "persona_id": self.persona_id,
        }


def customer_personas(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT account_id, account_name, plan FROM accounts ORDER BY account_id"
    ).fetchall()
    return [
        {
            "id": f"customer-{row['account_id']}",
            "role": CUSTOMER,
            "account_id": row["account_id"],
            "account_name": row["account_name"],
            "plan": row["plan"],
            "user_name": f"{row['account_name']} (customer)",
            "internal_permissions": [],
        }
        for row in rows
    ]


def personas(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    listed = customer_personas(conn)
    listed.extend(
        {
            "id": persona["id"],
            "role": INTERNAL,
            "account_id": None,
            "account_name": "ParcelPilot (internal)",
            "plan": None,
            "user_name": persona["user_name"],
            "internal_permissions": list(persona["internal_permissions"]),
        }
        for persona in INTERNAL_PERSONAS
    )
    return listed


def session_from_persona(
    conn: sqlite3.Connection, persona_id: str, session_id: str
) -> Session:
    for persona in personas(conn):
        if persona["id"] == persona_id:
            return Session(
                session_id=session_id,
                role=persona["role"],
                account_id=persona["account_id"],
                user_name=persona["user_name"],
                internal_permissions=list(persona["internal_permissions"]),
                persona_id=persona["id"],
            )
    raise KeyError(f"unknown persona {persona_id!r}")
