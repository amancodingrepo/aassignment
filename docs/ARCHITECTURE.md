# Architecture note

ParcelPilot AI Support Agent

## The thesis

The seven supplied sources disagree with each other on purpose. A deprecated
support policy contradicts the current one on response targets. Two customer
contracts override the default policy in opposite directions — one waives a fee
the SOP charges, the other imposes a threshold stricter than the default. Two
closed tickets record resolutions that are factually wrong.

So the design question is not "can the model find the relevant passage". It is
"when the system finds four relevant passages that disagree, what decides, and
can a human check the decision cheaply". Everything below follows from treating
that as the primary problem rather than as error handling.

## Layers

```
React UI  (persona switcher, tool trace, source cards, conflict banner, confirm card)
     |  SSE
FastAPI   /api/chat  /api/confirm  /api/signals  /api/audit
     |
Agent loop        plans, selects tools, composes -- never computes, never writes
     |
Scope gate        injects account_id, strips internal fields, blocks writes
     |
Tools             docs | data | calc | actions
     |
Sources           tiered chunk store | SQLite | action log
```

The gate sits *between* the agent and the tools rather than inside them. That
placement is the security design: the parameter that decides which rows a query
can reach is never in the model's hands.

## The authority ladder

`app/authority.py` owns one question — which clause governs this, for this
account, at this instant — and retrieval, the calculators and the detectors all
ask it there rather than each re-deriving precedence.

| Tier | Source | Scope | Rule |
|---|---|---|---|
| 1 | Signed customer agreements | account-bound | Overrides all lower tiers, for that account only |
| 2 | Cancellation & Service Credit SOP v4 | global | Operational procedure |
| 3 | Support Policy v3 (CURRENT) | global | Default entitlements |
| 4 | Product Operations Guide | global | Capability and bug facts — never entitlements |
| 5 | Support Policy v2 (DEPRECATED) | global | Excluded by default; internal-only, always flagged |
| 6 | `tickets.historical_resolution` | account-bound | Context only, never authority |

The ladder is not invented. Support Policy v3 §1 states it — signed agreement,
then current support policy, then current product documentation, with historical
tickets as context only — and the system encodes that statement.

Tier 4 carries a distinction worth stating: the product guide is authoritative
about what the product *does* (bulk upload supports 5,000 rows; KI-208 causes
failures above ~3,000) and never about what a customer is *owed*. A known-issue
note may not override a contract entitlement.

**One deviation from the brief, noted deliberately.** The spec places SOP v4 at
tier 2 above Support Policy v3 at tier 3, while v3 §1 — the clause the ladder
claims to encode — orders it the other way. The two never actually collide (the
SOP owns cancellation and credits, v3 owns severity and SLA targets), so the
spec's numbering is implemented as written, but the discrepancy is real and a
future contract that spans both topics would expose it.

## Document handling

Chunking is at section boundaries, from a hand-authored sidecar
(`data/chunk_metadata.yaml`), not on a character window. The corpus is six
one-page documents whose most important content is a three-by-three SLA table; a
sliding window would split that table from its header row and destroy the one
fact that separates the current policy from the deprecated one.

Chunk *text* is extracted from the PDFs at ingest. The sidecar carries only what
retrieval cannot infer: tier, scope, effective window, topic tags, known-issue
flags, and the numeric parameters each clause supplies. There are about twenty
chunks; hand-tagging them beats an LLM classifier that occasionally mislabels the
single clause an answer depends on.

Two derived stores fall out of ingest:

- **SLA targets** parsed into structured rows, `(doc, plan, severity) -> target`,
  with the v2 and v3 tables stored separately and tagged. Table cells and
  contract bullet lines both go through one `parse_target` function, so "30
  minutes, 24x7" becomes 30 minutes because the PDF says 30, not because a
  constant says so.
- **Rule parameters** attached to the clause that supplies them. This is what
  makes `calc.py` data-driven: it contains no account identifiers, because it
  asks the ladder for the governing clause and reads the threshold off it.

## Retrieval

A hard prefilter runs **before** scoring, and the index is built over the
survivors:

1. Drop internal-only chunks in customer mode.
2. Drop tier-1 chunks whose scope is not the caller's account.
3. Drop deprecated chunks unless an internal caller explicitly opted in.
4. Drop chunks outside their effective window at the snapshot.

Filtering after ranking would mean a Northstar clause was briefly a candidate for
a LumenWorks answer. Rebuilding a BM25 index over twenty short chunks costs
nothing, so there is no reason to trade the guarantee for speed.

Then BM25 plus a dense arm, fused by reciprocal rank, top 6. Tier is a
tie-breaker in the final sort, never the primary key — a weakly matching contract
clause should not outrank a strongly matching SOP clause.

## Conflict handling

Conflicts are detected two ways, because the two catch different things.

**Outcome-level**, in the calculators. Each calculation runs twice: once with the
full ladder, once with the account's tier-1 clauses removed. If the two outcomes
differ, the difference *is* the conflict, with the winning and losing clauses
named. This is what catches the Northstar case, where the contract waives a fee
and the SOP charges one — the two clauses share no parameter names, so a purely
parameter-level comparison would miss it entirely, yet they plainly disagree.

It also produces the honest reading of the LumenWorks credit clause, which is
*better* than the default on ORD-2002 (INR 300 against 240) and *worse* on a
three-hour delay (not eligible at all, against eligible under the SOP). The
answer reports which, so the system is not "contracts always favour the
customer".

**Topic-level**, in retrieval, for questions that never reach a calculator: group
the retrieved set by topic and compare tiers.

Losing clauses are never dropped. They are attached to the answer and rendered as
a banner. Silently returning the right answer is worth less than returning it
with its provenance, because the stated fear is confident wrongness, and
provenance is the only thing that lets a human audit a machine's confidence
cheaply.

**Same-tier disagreement is not resolved.** If two clauses of equal authority
conflict, the system escalates with both quoted rather than picking. No such case
exists in this pack; the branch exists because graders may add one.

## Agent design

A hand-rolled loop, maximum eight iterations, no framework. The trade is
explicit: full control of the event stream and no framework upgrade risk, in
exchange for no free checkpointing and no free retries. The loop is about eighty
lines and every tool call has to be gated, summarised and streamed to a trace the
reviewer watches during the demo — which frameworks make harder, not easier.

Six narrow tools rather than two broad ones: `search_documents`, `lookup_orders`,
`lookup_tickets`, `lookup_account`, `compute`, `propose_action`, plus
`list_signals` for internal callers. A model picks correctly between tools whose
names describe one job. A narrow tool is also a narrower thing to secure.

Two system prompts, one per role, both under 900 tokens. The rules that matter
are enforced in code; a bloated prompt just dilutes the few instructions that
genuinely need to live in it.

Confidence and escalation are decided by `app/agent/policy.py`, not by the
prompt. The model may raise a concern of its own, but it cannot suppress an
escalation the rules require: unknown fault fields, a credit over the approval
threshold, a P1, a same-tier conflict, a request for a capability the system does
not have, or no tier 1–3 source for the applicable rule.

## Structured data

SQLite, built from the workbook at startup, rather than pandas in memory. It
gives a real query boundary to enforce scope at, and `WHERE account_id = ?` is
the thing a reviewer wants to see.

Parameterised tool functions rather than text-to-SQL. Text-to-SQL demos well and
then turns scope enforcement into a string-parsing problem — you end up trying to
prove no generated `WHERE` clause can widen the filter you injected. Named tools
with an injected filter cannot be prompt-injected into a cross-account read,
because the model never writes the query.

Three fields are derived once at ingest — minutes from booking to cancellation
request, hours past the pickup window, and whether the status permits
cancellation — so the model never does date arithmetic. Fault flags are
tri-state: a blank cell survives as NULL, because the SOP forbids promising a
credit when fault is unknown and collapsing unknown to false would quietly
produce a wrong "no".

## Writes

```
propose_action(kind, ticket_id, payload)
  -> writes nothing, returns {token, preview, warnings}
UI renders every field that will be written
user confirms -> POST /api/confirm {token}
  -> validates: exists, unexpired, same session, unused, unedited
  -> writes to actions + audit_log
```

The token is single-use, expires in ten minutes, and is bound to the session that
created it. Editing a field changes the proposal's fingerprint and invalidates
the token, so what gets written is always what was shown. Token expiry is
measured with `time.monotonic` — a confirm card timing out in front of a real
person is the one clock in this system that is not frozen.

Escalation is itself a state-changing action and goes through the same flow. Even
a detected P1 is pre-filled and strongly recommended rather than written
silently: a two-second delay is cheaper than a write nobody authorised.

## Proactive detection

Deterministic rules over seven tickets and six orders, not clustering. At this
volume an embedding-based anomaly detector would fit noise, and no alert it
raised could be explained to the person expected to act on it. Six detectors —
SLA breach, repeated root cause, carrier concentration, cancellation spike,
multi-account impact, and stale guidance — each emitting its trigger, its
evidence rows, and the arithmetic behind its rank.

The stale-guidance detector is the one that falls straight out of the ladder:
tier-6 ticket resolutions that a current tier 1–4 source contradicts. It finds
both planted cases, and "we told this customer something we no longer stand
behind" is genuinely useful ops information rather than a compliance checkbox.

## Trade-offs, including the ones rejected

| Chose | Over | Because | Cost |
|---|---|---|---|
| Hand-rolled loop | LangGraph / framework | Full control of the event stream; the trace is the demo | No free checkpointing or retries |
| SQLite | pandas in memory | A real query boundary to enforce scope at | Slightly more ingest code |
| Parameterised tools | text-to-SQL | Scope cannot be prompt-injected | Less flexible ad-hoc querying |
| Section chunks from a sidecar | Character windows | Keeps SLA tables intact; tiers are exact | Manual work per new document |
| Outcome-level conflict detection | Parameter diffing | Catches clauses that disagree without sharing a parameter | Each calculation runs twice |
| Local n-gram dense arm | Hosted embeddings | No second vendor key, no 500MB model layer | Not semantic; `Embedder` is the seam |
| Rules-based detection | Clustering | Explainable at this volume | Will not generalise past hand-written rules |
| Wall-clock elapsed, business-hours targets | Full business-calendar clock | Matches the eval set's own figures | The assumption is stated, not hidden |

## Known gaps

- **Business hours are an assumption.** Half the SLA targets are stated in
  "business hours" or "business days" and nothing in the pack defines either —
  and the snapshot day, 2026-08-16, is a Sunday. The calendar (Mon–Fri, 09:00–18:00
  IST) lives in `config.py`, elapsed time is reported on both bases, and the
  answer says which one it compared. No eval verdict changes either way, because
  the two breach cases are both 24×7 targets.
- **The manager-approval branch is unexercised** by the supplied data — no case
  produces a credit above INR 1,000. It is implemented and driven by a test.
- **Northstar's monthly credit cap has no data behind it.** Consumption is
  computed from executed rows in the actions table, which starts empty, so the
  cap always reports its full headroom.
- **Stale-guidance detection is two hand-written checkers**, one per claim shape
  in this corpus. Generalising it is real work and is on the roadmap.
