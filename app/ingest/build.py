"""Ingest orchestration.

Documents load before the workbook because the workbook's derived fields read
their status vocabulary out of the cancellation clause. That ordering is the
small price of not hard-coding 'BOOKED' in Python.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from app.config import set_snapshot
from app.db import connect, get_meta, init_schema
from app.ingest.documents import build_chunks, write_chunks
from app.ingest.workbook import build_database


def build_all(
    conn: sqlite3.Connection | None = None,
    *,
    db_path: Path | str | None = None,
    data_dir: Path | None = None,
    workbook_path: Path | None = None,
) -> sqlite3.Connection:
    """Build a complete database from the source pack. Safe to re-run."""
    connection = conn or connect(db_path)
    init_schema(connection)

    write_chunks(connection, build_chunks(data_dir=data_dir))
    build_database(connection, workbook_path)
    return connection


def restore_snapshot(conn: sqlite3.Connection) -> datetime | None:
    """Re-install the snapshot clock from an already-built database."""
    stored = get_meta(conn, "snapshot_now")
    if not stored:
        return None
    parsed = datetime.fromisoformat(stored)
    set_snapshot(parsed)
    return parsed


def ensure_loaded(
    conn: sqlite3.Connection | None = None,
    *,
    db_path: Path | str | None = None,
    rebuild: bool = False,
) -> sqlite3.Connection:
    """Used at API startup: rebuild if empty, otherwise just restore the clock.

    The actions and audit tables must survive a restart during a demo, so a
    populated database is never silently rebuilt.
    """
    connection = conn or connect(db_path)
    init_schema(connection)

    if rebuild or restore_snapshot(connection) is None:
        return build_all(connection)

    chunk_count = connection.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    order_count = connection.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
    if chunk_count == 0 or order_count == 0:
        return build_all(connection)
    return connection
