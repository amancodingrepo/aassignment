from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from app.db import connect
from app.ingest.build import build_all


@pytest.fixture(scope="session")
def db() -> Iterator[sqlite3.Connection]:
    """One in-memory database built from the real source pack.

    Built from the PDFs and workbook rather than from fixtures on purpose: a
    test suite that runs against hand-written fixtures cannot catch the ingest
    bug that makes every answer wrong.
    """
    conn = build_all(connect(":memory:"))
    yield conn
    conn.close()
