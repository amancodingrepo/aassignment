# Build status

Last updated: 2026-08-23. Snapshot the system reasons about: 2026-08-16 11:00 IST.

The suite has now been executed. `pytest` is green, ingest produces 19 chunks
and 24 SLA targets, the Vite bundle builds, and the API serves the SPA.

Code gaps against the official brief are closed. Remaining work is recording
the 5-minute video locally and (optionally) hosting.

---

## Done

### Phase 0 — foundation

Ingest from the real PDFs and workbook: snapshot clock, SQLite schema, authority
ladder, hand-authored `data/chunk_metadata.yaml`. Gate:
`python -m scripts.dump_chunks` prints 19 chunks with correct tiers/scopes.

### Phase 1 — deterministic calculators

`app/tools/calc.py` against eval sections A, B and C. Enterprise P1 is 30
minutes (v3), never 1 hour (deprecated v2).

### Phase 2 — scope gate and tools

`tools/gate.py`, lookups, hybrid retrieval. Cross-account denial tests green.

### Phase 3 — agent

Hand-rolled loop, two role prompts, escalation policy. Terminal client:
`python -m scripts.ask`. Conversational cases still need `ANTHROPIC_API_KEY`.

### Phase 4 — confirm protocol

`propose_action` writes nothing; only `POST /api/confirm` writes. Single-use,
session-bound, ten-minute tokens.

### Phase 5 — interface

FastAPI + React. Persona switcher, tool trace, source cards, conflict banner,
confirm card, actions, audit. Bundle at `web/dist`, served from `/`.

### Phase 6 — signals

Six detectors. SwiftShip concentration includes delivered ORD-4001 (ACCT-004)
because KI-211 is about that carrier.

### Phase 7 — documentation

`README.md`, `docs/ARCHITECTURE.md`, `docs/PRODUCT.md`, `docs/AI_TOOL_USAGE.md`.

### Tests

```
pytest   # green, 2026-08-23, Python 3.12
```

| File | Covers |
|---|---|
| `tests/test_calc.py` | Eval A1–A5, B1–B6, C1–C5, plus the canaries |
| `tests/test_gate.py` | Eval E1–E5, schema stripping, audit rows |
| `tests/test_actions.py` | Eval F1–F6, token discipline |
| `tests/test_signals.py` | The six detectors against the expected outputs |
| `tests/test_ingest.py` | Tiers, scopes, windows, SLA parsing, derived fields, no-wall-clock guard |
| `tests/test_retrieval.py` | Prefilter, topic filter, deprecated opt-in, ranking |
| `tests/test_policy.py` | Escalation rules without a model |
| `tests/test_api.py` | Personas, session cookie, 403s, confirm, reset |

First-run failures that were fixed:

1. `test_no_module_reads_the_wall_clock` matched a docstring that named the
   banned API. The comment was rephrased; there is still no wall-clock call.
2. SwiftShip concentration omitted ACCT-004 because ORD-4001 is `DELIVERED`.
   Orders on a carrier with an open known issue are now attached as evidence.

---

## Remaining — submission, not code

| Item | Notes |
|---|---|
| Demo video | Beat sheet in `08_SUBMISSION_NOTES.md` and `README.md` "Local demo". Record at localhost:8000 with `ANTHROPIC_API_KEY` set. |
| Hosted application | Highly preferred, not required. Dockerfile is one container. |
| Conversational agent check | A1–A5 and D1–D4 through `scripts/ask.py` still need the key; run once before recording. |

---

## Known gaps in the design, already documented

These are deliberate and recorded in `docs/ARCHITECTURE.md`; they are not
oversights.

- **Business hours are an assumption.** The pack states half its SLA targets in
  "business hours"/"business days" and defines neither, and the snapshot day is a
  Sunday. Elapsed time is reported on both bases against a configurable Mon–Fri
  09:00–18:00 IST calendar. No eval verdict changes either way, because both
  breach cases are 24×7 targets.
- **The manager-approval branch is unexercised** by the supplied data — nothing
  produces a credit above INR 1,000. It is implemented and driven by a test.
- **Northstar's INR 5,000 monthly cap has no consumption data.** It is computed
  from executed rows in `actions`, which starts empty, so the cap always reports
  full headroom.
- **Stale-guidance detection is two hand-written checkers**, one per claim shape
  in this corpus. Generalising it is item 2 on the product-note roadmap.
- **The dense retrieval arm is local character n-grams**, not a hosted embedding
  model — no second vendor key, no 500MB model layer, and not semantic.
  `Embedder` in `app/tools/docs.py` is the seam.
- **The spec's tier ordering contradicts the clause it encodes.** `03_SOURCE_AUTHORITY.md`
  places SOP v4 above Support Policy v3, while v3 §1 orders it the other way.
  They never collide on this corpus, so the spec is implemented as written.
