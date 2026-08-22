"""Phase 0 gate: print every chunk with its tier, scope and topics, and every
parsed SLA target, so a human can eyeball the metadata before anything is built
on top of it.

    python -m scripts.dump_chunks

Errors in this layer poison every answer downstream and are invisible by the
time they surface as a wrong number in the chat window.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.authority import TIER_LABELS, load_clauses  # noqa: E402
from app.db import connect, get_meta  # noqa: E402
from app.ingest.build import build_all  # noqa: E402


def main() -> int:
    conn = build_all(connect(":memory:"))

    print("=" * 78)
    print(f"snapshot  : {get_meta(conn, 'snapshot_now')}")
    print(f"currency  : {get_meta(conn, 'currency')}")
    print("=" * 78)

    clauses = sorted(load_clauses(conn), key=lambda c: (c.tier, c.doc_id, c.section))
    print(f"\n{len(clauses)} CHUNKS\n")
    for clause in clauses:
        window = f"{clause.effective_from or '-'} .. {clause.effective_to or 'open'}"
        print(f"[tier {clause.tier}] {clause.chunk_id}")
        print(f"    title      : {clause.doc_title} §{clause.section} {clause.section_title}")
        print(f"    authority  : {TIER_LABELS[clause.tier]}")
        print(f"    scope      : {clause.scope}   status: {clause.status}   internal_only: {clause.internal_only}")
        print(f"    effective  : {window}")
        print(f"    topics     : {', '.join(clause.topics) or '-'}")
        if clause.flags:
            print(f"    flags      : {clause.flags}")
        if clause.params:
            print(f"    params     : {clause.params}")
        preview = " ".join(clause.text.split())[:150]
        print(f"    text       : {preview}...")
        print()

    rows = conn.execute(
        "SELECT * FROM sla_targets ORDER BY tier, doc_id, plan, severity"
    ).fetchall()
    print(f"{len(rows)} SLA TARGETS\n")
    header = f"{'tier':<5} {'doc':<42} {'scope':<10} {'plan':<12} {'sev':<4} target"
    print(header)
    print("-" * len(header))
    for row in rows:
        parsed = []
        if row["target_minutes"] is not None:
            parsed.append(f"{row['target_minutes']:.0f}m")
        if row["business_hours"] is not None:
            parsed.append(f"{row['business_hours']:.0f} bus-h")
        if row["business_days"] is not None:
            parsed.append(f"{row['business_days']:.0f} bus-d")
        if row["coverage"]:
            parsed.append(row["coverage"])
        print(
            f"{row['tier']:<5} {row['doc_id'][:42]:<42} {row['scope']:<10} "
            f"{row['plan']:<12} {row['severity']:<4} "
            f"{row['target_text']}  ->  {', '.join(parsed) or 'UNPARSED'}"
        )

    print("\nORDERS (derived fields)\n")
    order_header = f"{'order':<10} {'acct':<10} {'status':<10} {'min->cancel':>12} {'h past window':>14} {'cancellable':>12}"
    print(order_header)
    print("-" * len(order_header))
    for row in conn.execute("SELECT * FROM orders ORDER BY order_id"):
        mins = "-" if row["minutes_booked_to_cancel_request"] is None else f"{row['minutes_booked_to_cancel_request']:.0f}"
        hrs = "-" if row["hours_past_pickup_window_end"] is None else f"{row['hours_past_pickup_window_end']:.2f}"
        print(
            f"{row['order_id']:<10} {row['account_id']:<10} {row['status']:<10} "
            f"{mins:>12} {hrs:>14} {str(bool(row['is_cancellable_status'])):>12}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
