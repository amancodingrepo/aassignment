# Submission deliverables

Six items are required. Missing one costs more than any feature gains.

## 1. Repository

Public. `README.md` covers: what it is, one-command run, required env vars, the
mocked personas, and a "try these" list drawn from the eval set. Include the
tests — a grader who sees `test_gate.py` may not read the source at all.

## 2. Hosted application

Highly preferred, so treat it as required. Include the persona switcher on the
landing screen so a reviewer needs no instructions.

## 3. Demo video (~5 minutes)

Rehearse. Five minutes disappears fast.

- **0:00–0:50 Architecture.** One diagram. Say the thesis out loud: the sources
  disagree on purpose, so the system ranks them and shows its work.
- **0:50–2:10 The contract-override chain.** ORD-1001 for Northstar. Point at
  the tool trace. Then immediately run ORD-2001 for LumenWorks and get 250. Same
  question, different accounts, different correct answers, no hard-coding.
- **2:10–2:50 Trust.** The bulk-upload question (D1). The system contradicts a
  prior support answer and cites why. Then show the deprecated v2 policy is
  never retrieved.
- **2:50–3:30 Access control.** Switch to LumenWorks, ask for ORD-1001, get
  nothing. Try the prompt injection from E3. Show the audit row.
- **3:30–4:10 Action + confirm.** Escalate TKT-505, show the preview, cancel,
  show nothing was written, redo and confirm.
- **4:10–4:45 Signals.** The two breached P1s and the stale-guidance signal.
- **4:45–5:00 Decisions.** Three sentences: gate below the model, deterministic
  maths outside the model, structural rather than cosmetic conflict handling.

## 4. Architecture note

One to two pages. Cover: agent design, tool design, document and structured-data
handling, source reliability and conflict handling, and major trade-offs. Pull
directly from `01_ARCHITECTURE.md`, `03_SOURCE_AUTHORITY.md`, and the trade-offs
section. Include the trade-offs you rejected and why — a note that only lists
what you built reads as a feature list, while one that shows the alternatives
you weighed reads as engineering judgment.

## 5. Product note

Required elements, each answered concretely:

**Which additional problem, and how.** Both, unevenly. Problem 2 is addressed
structurally through the authority ladder rather than as a screen — it is why
the retrieval layer exists in the shape it does. Problem 1 is a separate signals
view. Explain the reasoning: trust is a property of every answer, so it cannot
be a feature; detection is a distinct job, so it can.

**What else you would build.** Prioritised, with reasons:

1. *Feedback capture on every answer.* Ops marks answers wrong; the wrong ones
   become regression tests. Without this the system has no way to improve after
   launch, and every other item on this list is guesswork.
2. *Contract ingestion as a first-class workflow.* Two contracts were
   hand-tagged. At fifty customers that is the bottleneck, and mis-tagging one
   clause silently corrupts every answer for that account.
3. *Draft-reply mode inside the existing helpdesk.* Ops staff will not adopt a
   second window. Meeting the work where it happens matters more than the model.
4. *Write-back to the ticketing system.* Actions currently land in a local
   table; real value needs the real system of record.
5. *Answer-level versioning.* When a policy changes, know which past answers are
   now wrong and who received them.

**What you left out, deliberately.** Real auth and SSO. Multi-tenant isolation
beyond the account filter. Rate limiting. Conversation persistence across
sessions. PII redaction in logs. Streaming partial tool results. Embedding-based
complaint clustering, which needs more volume than the pack provides to be
anything but noise. Naming these accurately is worth more than pretending the
scope was complete.

**One metric.** *Resolution without human correction* — the share of answered
requests where no ops agent subsequently edits or contradicts the answer,
tracked separately for the customer and internal contexts. It captures the thing
the client is actually afraid of, since a wrong answer that gets corrected shows
up as a failure even though it was fluent, and volume metrics like deflection
rate would reward exactly the confident wrongness they said would kill adoption.
Pair it with escalation precision so the system cannot game the metric by
escalating everything.

## 6. AI tool usage

State plainly which tools and how. Be specific about what you directed versus
what the tool produced: spec-first prompting from the docs in this pack, tests
written before implementation, manual review of the retrieval metadata. Honesty
here is low-risk — everyone uses these now, and a candidate who describes their
workflow precisely reads as more competent than one who is vague about it.
