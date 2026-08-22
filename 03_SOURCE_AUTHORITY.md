# Source authority, retrieval, and conflict handling

This file is the spine of the submission. Implement it before the chat UI.

## The ladder

Support Policy v3 §1 states the precedence itself: signed customer agreement
first, then current support policy, then current product documentation, with
historical tickets and internal notes as context only. The system encodes that
statement rather than inventing a hierarchy.

| Tier | Source | Scope | Rule |
|---|---|---|---|
| 1 | `05_Northstar…`, `06_LumenWorks…` | account-bound | Overrides all lower tiers **for that account only** |
| 2 | `03_Cancellation_and_Service_Credit_SOP_v4` | global | Operational procedure; contract may replace specific clauses |
| 3 | `01_Support_Policy_v3_CURRENT` | global | Default entitlements |
| 4 | `04_Product_Operations_Guide_and_Known_Issues` | global | Capability + bug facts, not entitlements |
| 5 | `02_Support_Policy_v2_DEPRECATED` | global | Excluded by default; internal-only, always flagged |
| 6 | `tickets.historical_resolution` | account-bound | Context only, never authority, never cited as a rule |

Tier 4 has a subtlety worth getting right: the product guide is authoritative
about **what the product does** (bulk upload supports 5,000 rows; KI-208 causes
failures above ~3,000; KI-211 delays SwiftShip webhooks up to 20 minutes) but
not about **what a customer is owed**. Never let a known-issue note override a
contract or SOP entitlement.

The guide also says KI-176 (address validation) is resolved and must not be used
to explain new incidents without matching evidence. Encode that as a chunk flag
`do_not_apply_to_new_incidents: true` and have the agent skip resolved issues
when diagnosing.

## Chunk metadata

Chunk at section boundaries — these are one-page documents with numbered
sections, so section-level chunks are the natural unit. Do not use a fixed
character window; it will split the SLA table from its header and destroy the
one thing you need to read accurately.

```python
{
  "chunk_id": "05_northstar#s2",
  "doc_id": "05_Northstar_Logistics_Enterprise_Agreement",
  "doc_title": "Northstar Logistics Enterprise Agreement",
  "tier": 1,
  "status": "current" | "deprecated" | "resolved",
  "scope": "global" | "ACCT-001",
  "effective_from": "2026-01-01",
  "effective_to": "2026-12-31",
  "section": "2",
  "section_title": "Shipment cancellation",
  "topics": ["cancellation", "fee_waiver"],
  "text": "...",
  "internal_only": false
}
```

Parse the SLA tables into structured rows as well as text — store
`sla_targets[(plan, severity)] -> target` per document so `calc.py` can look
them up without re-parsing prose. The v2 and v3 tables must be stored
separately and tagged, since their numbers differ and that difference is a
deliberate trap.

`topics` is a small controlled vocabulary: `cancellation`, `service_credit`,
`sla`, `severity`, `escalation`, `plan_capability`, `known_issue`,
`account_contact`. Tag by hand at ingest — there are about twenty chunks total.
Hand-tagging twenty chunks beats an LLM classifier that occasionally mislabels
the one clause the answer depends on.

## Retrieval

`search_documents(query, topics=None, account_id=<injected>, include_deprecated=False)`

Hard prefilter before scoring:

1. Drop `internal_only` chunks in customer mode.
2. Drop tier-1 chunks whose `scope` is not the caller's account. A LumenWorks
   customer must never retrieve a Northstar clause, and neither must the model
   when answering for LumenWorks — this is a correctness issue as much as a
   privacy one.
3. Drop `status == "deprecated"` unless `include_deprecated` is true, which only
   internal mode can set.
4. Drop chunks outside their effective window relative to the snapshot.

Then BM25 + embeddings, fused by reciprocal rank, top 6. Return chunks with full
metadata so the UI can render tier badges.

## Conflict resolution

After retrieval, group results by `topics`. Within a group, if two chunks give
different answers, resolve by tier and record the conflict rather than dropping
the loser:

```python
Conflict(topic="cancellation_fee",
         winner="Northstar Agreement §2 (tier 1)",
         loser="SOP v4 §1 (tier 2)",
         why="Signed agreement overrides the default 30-minute fee rule for ACCT-001")
```

Conflicts surface in the answer object and render as a UI banner. This is
Problem 2's answer: the system does not hide that sources disagreed, it shows
which one won and why. Silently returning the right answer is worth less than
returning the right answer with its provenance, because the ops team's stated
fear is confident wrongness, and provenance is the only thing that lets a human
audit a machine's confidence cheaply.

Three conflicts exist in this pack and all three should be demonstrable:

1. Northstar contract vs SOP on cancellation fees → contract wins for ACCT-001.
2. Support Policy v3 vs v2 on response targets → v3 wins, v2 never retrieved in
   customer mode.
3. Historical ticket resolutions vs current sources → current sources win, and
   the ticket is reported as likely-incorrect past guidance.

The third deserves special treatment. When a tier-6 resolution contradicts a
tier 1–4 source, don't just ignore it — emit a `stale_guidance` flag on the
answer. In internal mode, surface it as "TKT-450 told this customer something we
no longer stand behind", which is genuinely useful ops information and shows
product thinking beyond the literal requirement.

## Same-tier disagreement

If two chunks at the same tier conflict, the system does not pick. It escalates
with both quoted. There is no such case in this pack, but graders may add one,
and the brief explicitly lists "different systems may disagree" as a concern.
