# Data model

Source: `ParcelPilot_Assessment_Data.xlsx`. Loaded into SQLite at startup by
`ingest/workbook.py`. Do not edit the workbook; derive everything.

## Snapshot time

README sheet: `2026-08-16 11:00 Asia/Kolkata`. Currency INR.

Parse this at ingest into `config.SNAPSHOT_NOW` and expose `snapshot_now()`.
Grep the codebase for `datetime.now` before submitting — there should be zero
hits outside logging.

The README also states that some historical ticket resolutions may be incorrect
and are context only, not policy authority. This is a data-level instruction and
is why `historical_resolution` is tier 6.

## accounts

| account_id | account_name | plan | csm | contract | premium_support |
|---|---|---|---|---|---|
| ACCT-001 | Northstar Logistics | Enterprise | Priya Mehta | 05_Northstar | TRUE |
| ACCT-002 | LumenWorks | Growth | Arjun Rao | 06_LumenWorks | FALSE |
| ACCT-003 | Beacon Retail | Standard | Neha Kapoor | — | FALSE |
| ACCT-004 | Axis Labs | Enterprise | Priya Mehta | — | FALSE |

ACCT-003 and ACCT-004 have no contract, so pure policy defaults apply. Keep them
in the demo — they prove the contract layer is conditional, not hard-coded.

`csm` and `notes` are internal-only fields. Strip them in customer mode.

## orders

Columns: `order_id, account_id, carrier, status, booked_at, pickup_window_start,
pickup_window_end, pickup_actual_at, shipment_fee_inr, carrier_fault,
customer_fault, cancellation_requested_at, notes`

| order | acct | carrier | status | booked | cancel req | mins | fee | fault |
|---|---|---|---|---|---|---|---|---|
| ORD-1001 | 001 | SwiftShip | BOOKED | 09:00 | 11:00 | 120 | 4200 | — |
| ORD-1002 | 001 | BlueDart Pro | PICKED_UP | 08:10 | 10:20 | 130 | 5100 | — |
| ORD-2001 | 002 | SwiftShip | BOOKED | 09:00 | 10:15 | 75 | 1800 | — |
| ORD-2002 | 002 | RoadRunner | BOOKED | 04:30 | — | — | 2400 | carrier |
| ORD-3001 | 003 | RoadRunner | BOOKED | 10:25 | 10:40 | 15 | 1200 | — |
| ORD-4001 | 004 | SwiftShip | DELIVERED | 14 Aug | — | — | 3600 | — |

All times 2026-08-16 unless noted.

ORD-2002 is the service-credit case: pickup window ended 06:30, never picked up,
carrier at fault, customer not at fault. At snapshot that is **4h30m** past the
window end.

`carrier_fault` and `customer_fault` are booleans in this dataset, but treat a
missing/null value as *unknown* in `calc.py` — the SOP forbids promising a
credit under uncertainty, and a grader may hand you a record with blanks.

## tickets

Columns: `ticket_id, account_id, created_at, status, subject, description,
channel, assigned_to, last_customer_message_at, historical_resolution`

| ticket | acct | created | status | gist |
|---|---|---|---|---|
| TKT-501 | 001 | 16 Aug 10:30 | open | all shipment creation returns HTTP 500 |
| TKT-502 | 002 | 16 Aug 09:45 | open | bulk upload fails at ~70% on 4,200-row CSV |
| TKT-503 | 003 | 16 Aug 10:05 | open | how to change billing contact |
| TKT-504 | 001 | 16 Aug 10:50 | open | SwiftShip shows BOOKED after driver pickup |
| TKT-505 | 004 | 16 Aug 08:30 | open | production API key posted in public channel |
| TKT-450 | 001 | 12 Jul | closed | historical: told Northstar INR 250 fee applied |
| TKT-451 | 002 | 11 Aug | closed | historical: told Growth caps at 3,000 rows |

Both closed tickets carry resolutions that are **wrong**. TKT-450 contradicts
Northstar's contract; TKT-451 contradicts the product guide's 5,000-row limit
and misattributes bug KI-208 to a plan limit. Your system must retrieve these,
recognise them as tier 6, and decline to follow them. Showing this happening on
camera is the strongest thirty seconds of the demo video.

`assigned_to` is internal-only.

## SQLite schema

Three tables mirroring the sheets, plus two the app owns:

```sql
CREATE TABLE actions (
  action_id TEXT PRIMARY KEY,       -- ESC-xxxx / TSK-xxxx
  kind TEXT,                        -- escalation | ticket_update | follow_up
  ticket_id TEXT, account_id TEXT,
  payload_json TEXT,
  created_by TEXT, created_at TEXT,
  status TEXT                       -- pending | executed | cancelled
);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, session_role TEXT, session_account TEXT,
  tool TEXT, args_json TEXT,
  allowed INTEGER, denial_reason TEXT
);
```

`audit_log` records every gated call including denials. Cheap to build, and it
turns "access control is enforced in the data layer" from a claim into a table
you can show. Expose it read-only at `/audit` in internal mode.

Actions are seeded from the workbook only in the sense that they reference real
ticket and account IDs; the table starts empty and is written by the confirm
flow. Persist to a file-backed SQLite so writes survive a page reload during the
demo, and provide a `POST /reset` to clear it between takes.

## Index-time derived fields

Compute once at ingest and store on the order row — the model should never have
to derive these:

- `minutes_booked_to_cancel_request`
- `hours_past_pickup_window_end` (vs `pickup_actual_at` if present, else snapshot)
- `is_cancellable_status` (DRAFT/BOOKED true, PICKED_UP/DELIVERED false)
