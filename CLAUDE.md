# ParcelPilot AI Support Agent — project context

You are building a submission for a hiring assessment. Read every file in `docs/`
before writing code. Do not start coding until you have read `01_ARCHITECTURE.md`,
`03_SOURCE_AUTHORITY.md`, and `04_TOOLS_SPEC.md`.

## What this is

An AI support system for ParcelPilot, a B2B logistics platform. Two user
contexts, one codebase:

- **Customer mode** — a customer at one account asks questions about their own
  orders, entitlements, SLAs, cancellations, and service credits.
- **Internal mode** — a ParcelPilot ops agent investigates across all accounts
  and works a proactive issue queue.

A role switcher in the UI flips between them. This is a demo, so auth is mocked
— but the *enforcement* is not mocked. See "Non-negotiables".

## The single most important idea

The source pack is deliberately booby-trapped. It contains a deprecated policy
that contradicts the current one, two customer contracts that override general
policy in opposite directions, and closed tickets whose resolutions are factually
wrong. A system that treats all seven sources as equal authority will produce
confident wrong answers on most of the evaluation questions.

Every retrieved fact carries an authority tier. Conflicts are resolved by tier
and **shown to the user**, never silently collapsed. See `03_SOURCE_AUTHORITY.md`.

## Non-negotiables

These are graded requirements. Do not compromise them for convenience.

1. **Access control lives in the tool layer, not the prompt.** The agent never
   supplies `account_id` for a customer-scoped call. The tool layer injects it
   from the session. A customer asking about another account's order gets an
   empty result from the data layer, not a model refusal.
2. **State-changing actions require a two-phase confirm.** Phase one returns a
   preview object and writes nothing. Phase two executes only after a distinct
   user confirmation carrying the preview's token. The model saying "shall I
   proceed?" in prose is not sufficient.
3. **Every substantive answer cites its sources**, with the document name and
   authority tier visible in the UI.
4. **Nothing is hard-coded.** ORD-1001 and Northstar appear in the brief's
   examples; graders will test with ORD-2001, LumenWorks, TKT-505 and others.
   All logic reads from the loaded data.
5. **"Now" is the dataset snapshot**, `2026-08-16 11:00:00 Asia/Kolkata`. Never
   `datetime.now()`. Read it from the README sheet at ingest and pass it through
   a single `snapshot_now()` accessor.
6. **Deterministic maths stays in Python.** Fee amounts, credit amounts, elapsed
   time, and SLA breach are computed by tested functions. The model chooses
   *which* rule applies and explains it; it never does the arithmetic.

## Stack

- Python 3.11, FastAPI, SQLite (built from the workbook at startup), pandas
- Anthropic SDK with a hand-rolled tool loop — no agent framework. The loop is
  ~80 lines and you need to inspect and stream every tool call to the UI, which
  frameworks make harder to demo.
- Retrieval: BM25 (`rank_bm25`) + embeddings, reciprocal-rank fusion, with a
  hard metadata prefilter. The corpus is six one-page documents — do not build
  a heavyweight vector pipeline. The intelligence is in the metadata, not the
  index.
- Frontend: React + Vite, plain CSS. Single Docker image, FastAPI serves the
  built static bundle. One deploy target.

## Repo layout

```
app/
  main.py              FastAPI entrypoint, serves API + static
  agent/
    loop.py            tool-calling loop, streams events
    prompts.py         system prompts per role
    policy.py          confidence + escalation rules
  tools/
    registry.py        tool schemas exposed to the model
    gate.py            scope enforcement wrapper  <-- security boundary
    docs.py            search_documents
    data.py            structured lookups
    calc.py            deterministic fee/credit/SLA maths
    actions.py         escalation, ticket update, follow-up task
  ingest/
    documents.py       PDF -> tiered chunks
    workbook.py        xlsx -> SQLite
  signals/
    detect.py          proactive issue detection (Problem 1)
data/                  the seven source files
web/                   React app
tests/
  test_calc.py         golden cases from docs/06_EVAL_SET.md
  test_gate.py         cross-account denial tests
docs/                  these specs
```

## Working style

- Write `tests/test_gate.py` and `tests/test_calc.py` early. They are the two
  things a grader can break in thirty seconds if they are wrong.
- Commit in logical increments with real messages. The repo is part of the
  submission.
- `README.md` must let someone clone and run with `docker compose up` plus one
  API key. Assume the grader gives it five minutes.
