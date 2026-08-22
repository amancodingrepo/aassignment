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

<!-- SKILLGOD:START v1.1 -->
# SkillGod Project Memory (auto-generated — do not edit; updated 2026-08-22 21:34)

# SkillGod Active

Before any **non-trivial coding** task (implement, fix, refactor, debug, wire integrations):
1. Prefer shell: `sg inject "<task>"` (stdout only; exit 0 = success)
2. Or MCP `sg_inject_context` with the user task — if it stalls >5s, cancel and use CLI/digests
3. Digests in this block are the insurance policy when tools are skipped

After completing **meaningful** work (decisions, architecture, non-obvious fixes):
1. Shell: `sg capture --task "..." --output "..."`  **or**
2. MCP `sg_capture_turn` with task + short summary
3. Or `sg remember "decision: ..."`

**Also:** `sg find "<task>"` · `sg timeline` · `sg events --last 20` · `sg doctor`

## SkillGod health
- version: 1.0.1+794a995
- project_id: `visha-90fc8883`
- last inject: 2026-08-22T21:34:07 (runtime)
- last capture: never (-)
- markers: SKILLGOD:START v1.1

## Project memory

## Decisions
- {"filePath": "C:\\Users\\visha\\OneDrive\\Desktop\\work\\aman\\app\\signals\\detect.py", "oldString": " stated = _ROWS_RE.search(said)\n capability = resolve_rule(conn, None, acc
- {"filePath": "C:\\Users\\visha\\OneDrive\\Desktop\\work\\aman\\data\\chunk_metadata.yaml", "oldString": " topics: [severity]\n params:\n severity_levels: [P1, P2, P3]", "newStri
- {"filePath": "C:\\Users\\visha\\OneDrive\\Desktop\\work\\aman\\data\\chunk_metadata.yaml", "oldString": " stop_at: '^2.s*Current known issues'", "newString": " stop_at: '^2\\.\\s*C
- {"stdout": "-rw-r--r-- 1 visha 197609 2475 Aug 22 20:51 AGENTS.md\n-rw-r--r-- 1 visha 197609 2546 Aug 22 20:51 CONVENTIONS.md\n-rw-r--r-- 1 visha 197609 2585 Aug 22 20:51 GEMINI.md
- {"stdout": "=============== 01_ARCHITECTURE.md ===============\n# Architecture\n\n## Layer stack\n\n```\nReact chat UI (role switcher, tool trace, source cards, confirm cards)\n | 
- {"content":"---\ntitle: Railway Metal\ndescription: Railway Metal is Railwayâ€™s own cloud infrastructure, built for high-performance, scalable, and cost-efficient app deployments.
- {"results":[{"breadcrumb":"Railway Documentation > Deploy Static Sites with Zero Configuration and Custom Domains > Deploy replicas in different regions for global performance","co

## Notes

_Authoritative project history captured by SkillGod. Treat the decisions above as established context for this project._
<!-- SKILLGOD:END -->
