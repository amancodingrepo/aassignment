# Build status

Last updated: 2026-08-22. Snapshot the system reasons about: 2026-08-16 11:00 IST.

**Nothing in this repository has been executed.** There is no Python runtime on
the build machine and Docker was not used, so every file below was written and
reviewed but never run. Treat every "gate" as *written, not passed*. This
document exists so that distinction does not get lost.

---

## Done

### Phase 0 — foundation

| Item | File |
|---|---|
| Snapshot clock, no wall-clock fallback | `app/config.py` |
| SQLite schema incl. `actions` and `audit_log` | `app/db.py` |
| Authority ladder, clause resolution, conflict records | `app/authority.py` |
| PDF → tiered section chunks | `app/ingest/documents.py` |
| xlsx → SQLite, derived order fields | `app/ingest/workbook.py` |
| Ingest orchestration, safe re-run | `app/ingest/build.py` |
| Hand-authored chunk metadata (tiers, scopes, windows, topics, rule params) | `data/chunk_metadata.yaml` |
| Chunk dump script (the gate) | `scripts/dump_chunks.py` |

The seven source files were extracted into `data/` from the candidate-pack zip;
they were not present in the repo when the build started.

### Phase 1 — deterministic calculators

`app/tools/calc.py`: cancellation fee, service credit, SLA status, severity
classification, business-hours arithmetic, manager-approval evaluation. Each
returns the source clause it applied.

Written test-first against eval sections A, B and C, including all four
correctness canaries.

### Phase 2 — scope gate and tools

`app/tools/gate.py` (the boundary), `registry.py`, `docs.py` (hybrid retrieval),
`data.py` (structured lookups), `app/session.py`. Written test-first against eval
section E.

### Phase 3 — agent

`app/agent/loop.py` (hand-rolled, 8-iteration budget, SSE events),
`prompts.py` (two role prompts), `policy.py` (escalation rules in code, not in
the prompt). `scripts/ask.py` runs a turn from a terminal.

### Phase 4 — confirm protocol

`app/tools/actions.py`: `propose_action` writes nothing; `execute_confirmed` is
absent from the tool registry and reachable only from `POST /api/confirm`.
Single-use, session-bound, ten-minute tokens with edit invalidation.

### Phase 5 — interface

`app/main.py` (FastAPI: API + built bundle) and `web/` (React + Vite): chat with
streaming, persona switcher, tool trace with tier badges, source cards, conflict
banner, confirm card, actions list, audit table.

### Phase 6 — signals

`app/signals/detect.py`: all six detectors, ranked, with evidence rows and the
rank arithmetic printed on each card.

### Phase 7 — documentation

`README.md`, `docs/ARCHITECTURE.md`, `docs/PRODUCT.md`, and the skeleton of
`docs/AI_TOOL_USAGE.md`.

### Tests written

| File | Covers |
|---|---|
| `tests/test_calc.py` | Eval A1–A5, B1–B6, C1–C5, plus the canaries |
| `tests/test_gate.py` | Eval E1–E5, schema stripping, audit rows |
| `tests/test_actions.py` | Eval F1–F6, token discipline |
| `tests/test_signals.py` | The six detectors against the expected outputs |
| `tests/test_ingest.py` | Tiers, scopes, windows, SLA parsing, derived fields, no-wall-clock guard |

---

## Remaining — code

Small, and none of it blocks a review.

| Item | Why it matters |
|---|---|
| `.dockerignore` | Without it the Docker build context includes `.git`, `node_modules` and the spec files. Build still works; it is just slower and fatter. |
| `.env` loading for the non-Docker path | `docker compose` passes the key through. Running `uvicorn` directly requires exporting `ANTHROPIC_API_KEY` by hand. A dozen lines in `config.py` would read `.env` without adding a dependency. |
| `tests/test_retrieval.py` | `app/tools/docs.py` has no direct tests. Prefilter behaviour is covered indirectly by `test_gate.py`, but ranking, the topic filter and the tier tie-break are not. |
| `tests/test_policy.py` | `app/agent/policy.py` decides every escalation and is fully testable without a model. Currently untested. |
| `tests/test_api.py` | The FastAPI routes have no tests. `httpx` is already a dependency, so `TestClient` coverage of the persona/confirm/403 paths is straightforward. |

---

## Remaining — verification

This is the real outstanding work.

1. **Install a Python runtime and run `pytest`.** Every gate is unverified.
2. **Expect first-run failures in ingest.** The two most likely: the section-marker
   regexes in `data/chunk_metadata.yaml` not matching pdfplumber's actual line
   output, and `_sla_from_table` not recognising the policy tables. Both fail
   loudly (`_split_sections` raises on an unmatched marker; `build_chunks` raises
   when a declared `sla_source` parses nothing), so they will not pass silently.
3. **Run the Phase 3 gate**, which additionally needs `ANTHROPIC_API_KEY`:
   eval cases A1–A5 and D1–D4 through `scripts/ask.py`, in the terminal, before
   trusting anything seen through the UI.
4. **Build the frontend** (`cd web && npm install && npm run build`) and confirm
   the section G multi-step chain renders end to end.

---

## Remaining — submission

| Item | Notes |
|---|---|
| Hosted deployment | Nothing has been pushed or deployed anywhere. The `Dockerfile` targets a single container suitable for Render or Fly. Check the free tier's cold start before relying on it. |
| Demo video | Beat sheet is in `08_SUBMISSION_NOTES.md`. Record after a full rehearsal. |
| `docs/AI_TOOL_USAGE.md` | Has TODOs that need your own account of what you directed and what you verified by hand. Not something I should write for you. |
| `CLAUDE.md` hygiene | The local SkillGod plugin appended an auto-generated block to it, and that block is committed in the Phase 0 commit. Probably wants removing before the repo is submitted. |

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
