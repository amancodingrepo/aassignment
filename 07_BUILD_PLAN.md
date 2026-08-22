# Build plan

Ordered so that stopping at the end of any phase still yields a submittable
product. If time runs short, cut from the bottom, never the middle.

## Phase 0 — foundation (half a day)

- Repo skeleton, `docker compose up` runs an empty FastAPI + React shell.
- `ingest/workbook.py`: xlsx → SQLite, derived fields, `snapshot_now()`.
- `ingest/documents.py`: six PDFs → ~20 tiered chunks with the metadata from
  `03_SOURCE_AUTHORITY.md`. Hand-write the section tags in a YAML sidecar; do
  not auto-classify.
- SLA tables parsed into structured lookups, v2 and v3 stored separately.

Gate: a script prints all chunks with tier, scope, and topics. Eyeball it.
Errors here poison everything downstream and are invisible later.

## Phase 1 — deterministic core (half a day)

- `tools/calc.py`: cancellation fee, service credit, SLA status.
- `tests/test_calc.py` against every case in sections A, B and C of the eval set.

Gate: all calculation tests green. You now have the correct answers even before
an LLM is involved, which means any later wrong answer is an agent bug, not a
logic bug — a distinction that saves hours of debugging.

## Phase 2 — gate and tools (half a day)

- `tools/gate.py`, registry, audit log.
- `lookup_orders`, `lookup_tickets`, `lookup_account`, `search_documents`.
- `tests/test_gate.py` against section E.

Gate: cross-account denial tests green, audit rows appearing.

## Phase 3 — agent loop (one day)

- Tool-calling loop, SSE events, two role prompts.
- Conflict detection and the structured answer object.
- Escalation policy in `agent/policy.py`.

Gate: A1 through A5 and D1 through D4 answered correctly in a terminal client
before any UI exists. Test in the terminal first — UI bugs and agent bugs look
identical through a chat window.

## Phase 4 — actions and confirm (half a day)

- `propose_action`, `/confirm`, token binding and expiry.
- `tests` for section F including replay rejection.

Gate: F1 through F6.

## Phase 5 — interface (one day)

- Chat with streaming, role switcher, tool trace with tier badges, source cards,
  conflict banner, confirm card, actions list.
- Make the tool trace genuinely readable — tool name, arguments, a one-line
  result summary, expandable. The brief asks for it and it is what the reviewer
  watches during the video.

Gate: the section G multi-step chain renders end to end.

## Phase 6 — signals (half a day)

Per `05_SIGNALS.md`. Six detectors, ranked cards, seed-the-chat button.

## Phase 7 — ship (half a day)

- Deploy. Single container on Render or Fly. Confirm the free tier's cold start
  is tolerable, or pay for a month — a reviewer hitting a 50-second spin-up will
  assume the app is broken.
- `README.md`: one-command setup, mocked-auth explanation, persona list,
  a "try these questions" section lifted from the eval set.
- Architecture note and product note per `08_SUBMISSION_NOTES.md`.
- Record the video last, after a full rehearsal.

## Cut list, in cutting order

1. Signals detectors 3–5, keep 1, 2 and 6
2. Hybrid retrieval → BM25 only (barely changes results on this corpus)
3. `follow_up_task` and `ticket_update` kinds, keep `escalation`
4. Audit UI, keep the table
5. Streaming, fall back to whole-message responses

Never cut: the tier system, the gate, the confirm flow, the citations.
