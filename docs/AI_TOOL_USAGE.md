# AI tool usage

*Draft — fill in the sections marked TODO before submitting.*

## Tools used

- **Claude Code** (Claude Opus) for the bulk of implementation, run against a
  written spec pack rather than open-ended prompting.
- TODO: any other tools — editor completion, search, etc.

## What was directed versus what the tool produced

**Directed.** The spec pack in the repository root (`01_ARCHITECTURE.md` through
`08_SUBMISSION_NOTES.md`) was written first and treated as binding. The build ran
phase by phase against `07_BUILD_PLAN.md`, with a gate at the end of each phase.
Specific decisions that were directed rather than generated:

- The authority-tier design, and the requirement that conflicts surface rather
  than collapse.
- Access control living in the tool layer, with `account_id` injected from the
  session and absent from the customer-mode schemas.
- `execute_action` being kept out of the tool registry, so writes are reachable
  only through the confirm endpoint.
- Test-first for the calculators and the gate, with every case in eval sections
  A, B, C and E becoming a test before the implementation existed.
- The ban on `datetime.now()`, enforced by a test rather than by discipline.

**Produced by the tool, then reviewed.** Implementation of each module, the React
interface, and the first drafts of these notes. The chunk metadata sidecar was
generated and then checked clause by clause against the PDFs, because a
mis-tagged tier corrupts every answer for that account with no visible symptom —
that review found more than it did not.

**Judgement calls surfaced rather than silently made.** Several gaps in the spec
were raised before coding and resolved explicitly: that the snapshot day is a
Sunday and half the SLA targets are stated in undefined "business hours"; that
`service_credit(order_id)` cannot express the hypothetical cases in eval section
B; that a credit with no shipment fee is eligible but unquantifiable; and that
the tier ordering in the source-authority spec contradicts the policy clause it
claims to encode. Each is recorded in the architecture note rather than papered
over.

## Verification

- TODO: state which parts you ran and confirmed yourself, and on what.
- The test suite is the primary check: `pytest`.
- `python -m scripts.dump_chunks` prints the full retrieval metadata for manual
  review; this was the phase-0 gate.
- `python -m scripts.ask <persona> "<question>"` runs the agent from a terminal,
  which is where the conversational cases were checked before any UI existed.

## Honest notes

TODO: anything that did not go to plan, or that you would do differently. This
section is worth writing properly — everyone uses these tools now, and a precise
description of the workflow reads as more competent than a vague one.
