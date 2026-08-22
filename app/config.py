"""Process-wide configuration and the single source of "now".

The dataset is a frozen snapshot, so wall-clock time is meaningless here: every
elapsed-time and SLA calculation is relative to the snapshot instant declared on
the workbook's README sheet. `snapshot_now()` is the only sanctioned way to ask
what time it is. There is deliberately no fallback to `datetime.now()` -- an
unset snapshot raises, because silently drifting to real time would corrupt
every breach verdict in a way that looks plausible.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PARCELPILOT_DATA", REPO_ROOT / "data"))
DB_PATH = Path(os.environ.get("PARCELPILOT_DB", REPO_ROOT / "var" / "parcelpilot.db"))
WORKBOOK_PATH = DATA_DIR / "ParcelPilot_Assessment_Data.xlsx"
CHUNK_SIDECAR_PATH = DATA_DIR / "chunk_metadata.yaml"

MODEL = os.environ.get("PARCELPILOT_MODEL", "claude-sonnet-5")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-only-not-a-real-secret")

IST = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")

# Business calendar. The source pack states targets in "business hours" and
# "business days" but never defines them, and the snapshot day (2026-08-16)
# happens to be a Sunday. These values are an explicit, configurable assumption
# rather than a hidden one -- see the architecture note.
BUSINESS_DAYS = (0, 1, 2, 3, 4)  # Monday..Friday, per datetime.weekday()
BUSINESS_DAY_START_HOUR = 9
BUSINESS_DAY_END_HOUR = 18
BUSINESS_HOURS_PER_DAY = BUSINESS_DAY_END_HOUR - BUSINESS_DAY_START_HOUR

# Escalation thresholds that the SOP states in prose and the policy layer needs
# as numbers. Kept here so a grader can see them in one place.
MANAGER_APPROVAL_CREDIT_INR = 1000
CONFIRM_TOKEN_TTL_SECONDS = 600
MAX_AGENT_ITERATIONS = 8

_snapshot: datetime | None = None


class SnapshotNotLoaded(RuntimeError):
    """Raised when something asks for the time before ingest has run."""


def set_snapshot(value: datetime) -> None:
    """Called once by the workbook ingest with the parsed README value."""
    global _snapshot
    if value.tzinfo is None:
        value = value.replace(tzinfo=IST)
    _snapshot = value


def snapshot_now() -> datetime:
    """The dataset's reference instant. The only clock this system has."""
    if _snapshot is None:
        raise SnapshotNotLoaded(
            "snapshot_now() called before ingest loaded the README snapshot. "
            "Run app.ingest.workbook.build_database() first."
        )
    return _snapshot


def snapshot_is_loaded() -> bool:
    return _snapshot is not None
