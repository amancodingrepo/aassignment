# AI tool usage

## Tools used

- **Claude Code / Claude Opus** for the bulk of the original implementation,
  run against the written spec pack (`01_ARCHITECTURE.md` through
  `08_SUBMISSION_NOTES.md`) rather than open-ended prompting.
- **Grok 4.6 (xAI)** to execute the remaining verification pass: install a
  runtime, run ingest and `pytest`, fix first-run failures, add the leftover
  tests and ship files, and build the frontend.
- Editor completion and repo search as usual. No agent framework was used in
  the product itself.

## What was directed versus what the tool produced

**Directed.** The spec pack in the repository root was written first and treated
as binding. The build ran phase by phase against `07_BUILD_PLAN.md`, with a gate
at the end of each phase. Specific decisions that were directed rather than
generated:

- The authority-tier design, and the requirement that conflicts surface rather
  than collapse.
- Access control living in the tool layer, with `account_id` injected from the
  session and absent from the customer-mode schemas.
- `execute_action` being kept out of the tool registry, so writes are reachable
  only through the confirm endpoint.
- Test-first for the calculators and the gate, with every case in eval sections
  A, B, C and E becoming a test before the implementation existed.
- The ban on wall-clock time, enforced by a test rather than by discipline.

**Produced by the tool, then reviewed.** Implementation of each module, the React
interface, and the first drafts of these notes. The chunk metadata sidecar was
generated and then checked clause by clause against the PDFs, because a
mis-tagged tier corrupts every answer for that account with no visible symptom.

**Judgement calls surfaced rather than silently made.** Several gaps in the spec
were raised before coding and resolved explicitly: that the snapshot day is a
Sunday and half the SLA targets are stated in undefined "business hours"; that
`service_credit(order_id)` cannot express the hypothetical cases in eval section
B; that a credit with no shipment fee is eligible but unquantifiable; and that
the tier ordering in the source-authority spec contradicts the policy clause it
claims to encode. Each is recorded in the architecture note rather than papered
over.

## Verification

Run on Windows, Python 3.12, Node 22, 2026-08-23:

- `pytest` — full suite green after ingest (PDFs and workbook), including calc,
  gate, actions, signals, retrieval, policy, and API tests.
- `python -m scripts.dump_chunks` — 19 section chunks, 24 SLA targets, derived
  order fields matching the eval set. Enterprise P1 from v3 is `30 minutes, 24x7`;
  the deprecated v2 row is `1 hour` and tagged internal-only.
- Frontend: `cd web && npm install && npm run build`. The API surface
  (`/api/health`, personas, session, 403s, confirm, reset) is covered by
  `tests/test_api.py`. Conversational cases A1–A5 and D1–D4 through
  `scripts/ask.py` still need an `ANTHROPIC_API_KEY` and were not re-run in this
  verification pass.
- Two first-run failures were real and were fixed:
  1. The wall-clock guard matched a docstring that named the banned API.
  2. The SwiftShip concentration detector skipped delivered ORD-4001 (ACCT-004),
     which the signals spec still counts because KI-211 is about that carrier.

## Honest notes

The original implementation was written without a Python runtime on the build
machine, so every "gate" in `STATUS.md` was code-reviewed but unexecuted. The
gap that mattered was not a missing feature — it was that ingest regexes and
SLA table parsing can only be proven against pdfplumber's actual line output.
Once the suite ran, ingest succeeded first time; the two failures were in a
docstring and a detector filter, not in the calculators or the gate.

I would still run `scripts/ask.py` against A1–A5 and D1–D4 before recording the
demo. Calculator correctness is now proven; agent-loop correctness is not, and
those two classes of bug look identical through a chat window.
