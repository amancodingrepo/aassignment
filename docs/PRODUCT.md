# Product note

*Draft — ParcelPilot AI Support Agent*

## Which additional problem, and how

Both, unevenly, and the asymmetry is the point.

**Problem 2 — trust and conflicting sources — is addressed structurally, not as a
feature.** It is why the retrieval layer has the shape it does. Every chunk
carries an authority tier, scope and effective window; conflicts are resolved by
tier and rendered next to the answer rather than collapsed into it; the
calculators run twice so that a contract silently overriding a default becomes a
visible, named conflict.

There is no "Trust" screen, and there should not be. Trust is a property of every
answer or it is not a property at all — a screen that reports confidence
separately from the answer is a screen nobody opens at the moment they need it.
The reason this does not look like a feature is that it is load-bearing for the
core requirements: the same tier metadata that produces the conflict banner is
what makes cancellation fees come out right for two accounts with opposite
contracts.

**Problem 1 — proactive detection — is a separate internal view,** because it is
a genuinely different job. Answering a question is reactive and per-customer;
finding the thing nobody has asked about yet is a sweep across the whole dataset
with no user in the loop. It has its own screen, its own ranking, and its own
detectors.

The integration point between them is the "Ask the agent about this" button on
every signal card. The detector finds it, the agent explains it, the confirm flow
acts on it. That is what makes it feel like one product rather than two, and it
costs almost nothing: the seed query is just a pre-written question.

## What I would build next, in order

1. **Feedback capture on every answer.** One control: *this was wrong*. Wrong
   answers become regression tests in `tests/`, which is where this system's
   correctness already lives. This is first because without it the system has no
   way to improve after launch and every other item on this list is guesswork.
   It is also cheap — the answer object already carries its sources and the tool
   trace that produced it, so a flagged answer is a complete reproduction.

2. **Contract ingestion as a first-class workflow.** Two contracts were
   hand-tagged into `data/chunk_metadata.yaml`. At fifty customers that is the
   bottleneck, and it is worse than slow: mis-tagging one clause silently
   corrupts every answer for that account, with no symptom until someone
   complains. What this needs is an assisted extraction step with a human
   approving the tier, scope and parameters before a contract goes live — plus
   a diff view when a contract is renewed.

3. **Draft-reply mode inside the existing helpdesk.** Ops staff will not adopt a
   second window; they will paste from it for a week and then stop. The agent
   should propose a reply where the ticket already lives, with the source cards
   attached so the human can check the citation before sending. Meeting the work
   where it happens matters more than the model does.

4. **Write-back to the ticketing system.** Actions currently land in a local
   table. The confirm protocol, token binding and audit trail are all built; what
   is missing is the adapter and the reconciliation logic for when the remote
   write fails after the local one succeeded. Real value needs the real system of
   record.

5. **Answer-level versioning.** Every answer records the chunk IDs it relied on.
   When a policy changes, that makes it mechanical to ask which past answers are
   now wrong and who received them — which is the thing an ops lead will ask the
   first time a contract is renewed, and currently nobody can answer.

## What I left out, deliberately

- **Real authentication and SSO.** Personas are a dropdown and a signed cookie.
  The session object is the only thing the enforcement layer trusts, so replacing
  its construction with a verified OIDC token changes nothing below that line —
  which is exactly why mocking it here is honest rather than convenient.
- **Multi-tenant isolation beyond the account filter.** One database, one
  process. Real isolation means separate credentials and row-level security in
  the database itself, not a `WHERE` clause a bug could omit.
- **Rate limiting**, on both the API and the model calls.
- **Conversation persistence across sessions.** History lives in the browser tab.
- **PII redaction in logs.** The audit log stores tool arguments verbatim, which
  is right for demonstrating the gate and wrong for production.
- **Streaming partial tool results.** Tool calls stream as start/finish events;
  the text answer arrives whole rather than token by token.
- **Embedding-based complaint clustering.** Six orders and seven tickets cannot
  support it — it would fit noise and produce alerts nobody could explain. At
  ParcelPilot's stated volume of hundreds of requests weekly I would add it to
  detector 2, gated behind a minimum cluster size of five tickets in a rolling
  seven-day window, and I would ship it in shadow mode first: log what it would
  have raised, and turn it on only once a human agrees with the majority of a
  week's alerts.

Naming these accurately is worth more than pretending the scope was complete.

## One metric

**Resolution without human correction** — the share of answered requests where no
ops agent subsequently edits, contradicts or reopens the answer, tracked
separately for the customer and internal contexts.

It measures the thing the client is actually afraid of. A wrong answer that gets
corrected shows up as a failure even though it was fluent and confident, which is
precisely the failure mode they said would kill adoption. Volume metrics like
deflection rate would reward exactly that confident wrongness: an agent that
answers everything decisively and wrongly scores well on deflection and destroys
trust in a fortnight.

Split by context because the two have different tolerances. A wrong answer shown
to a customer is a commercial problem; a wrong answer shown to an ops agent who
catches it is a productivity problem. Averaging them hides which one is
happening.

Pair it with **escalation precision** — the share of escalations a human agrees
warranted escalating — so the system cannot game the primary metric by escalating
everything. Together they bound the two directions of failure: answering when it
should not, and refusing to answer when it should.
