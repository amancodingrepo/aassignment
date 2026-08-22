# Architecture

## Layer stack

```
React chat UI  (role switcher, tool trace, source cards, confirm cards)
        |  SSE
FastAPI  /chat  /confirm  /signals  /session
        |
Agent loop      plans, selects tools, composes answer, never computes
        |
Scope gate      injects account_id, strips fields, blocks writes   <-- boundary
        |
Tools           docs | data | calc | actions
        |
Sources         tiered chunk store | SQLite | mock action log
```

The gate sits between the agent and the tools. That placement is the whole
security design: the model cannot address data it was not scoped to, because
the scoping parameter is never in its hands.

## Session model

```python
Session = {
  "role": "customer" | "internal",
  "account_id": "ACCT-002" | None,   # required for customer, None for internal
  "user_name": str,
  "internal_permissions": ["read_all", "write_actions"] | [],
}
```

Mocked login: a dropdown offering four customer personas (one per account) and
two internal personas (support agent, ops manager). Selecting one sets a signed
session cookie. No password. The README says clearly that auth is mocked and
what would replace it.

Role differences:

| | customer | internal |
|---|---|---|
| order/ticket reads | own account only, forced filter | any account |
| documents | current global docs + own contract only | all docs incl. deprecated, flagged |
| other accounts' contracts | never | readable |
| internal fields (`csm`, `assigned_to`, `notes`) | stripped | visible |
| cross-account aggregates | denied | allowed |
| actions | can request escalation on own tickets | full |
| signals feed | not exposed | exposed |

Customers must not see the deprecated policy at all. Internal users may see it,
tagged `DEPRECATED`, because "what did we used to promise?" is a real ops
question and answering it demonstrates the tier system works.

## Agent loop

A single `while` loop, max 8 iterations:

1. Send conversation + tool schemas to the model.
2. If the response contains `tool_use` blocks, emit a `tool_start` SSE event per
   block, run each through the gate, emit `tool_result` with a UI-safe summary,
   append results, loop.
3. If the response is text, emit `answer` and stop.
4. If iteration 8 is hit, stop and escalate with the reason "could not resolve
   within step budget".

Emit these SSE event types so the UI can render the trace: `tool_start`,
`tool_result`, `token`, `confirm_required`, `answer`, `escalation`.

Two system prompts, one per role, both containing: the snapshot time, the
authority ladder, the escalation triggers, and a hard instruction never to
compute money or elapsed time itself. Keep them under 900 tokens each; the rules
that matter are enforced in code, and a bloated prompt just dilutes them.

## Confidence and escalation

`agent/policy.py` decides. Escalate when any of these hold:

- The applicable rule is not found in any tier-1 to tier-3 source.
- Two sources at the same tier disagree.
- A required data field is null and the outcome depends on it — e.g.
  `carrier_fault` unknown for a credit request. The SOP explicitly forbids
  promising a credit in this case.
- The computed credit exceeds INR 1,000 (SOP requires manager approval).
- The customer asks for an exception to a written rule.
- The request needs an action the system has no tool for (refunds, address
  changes, plan upgrades, billing contact changes).
- A P1 severity is detected, which the policy says escalates immediately.

Escalation is itself a state-changing action and goes through the same confirm
flow. Do not auto-create escalations silently, with one exception worth calling
out in the notes: for a detected P1 the agent should *strongly* recommend and
pre-fill, but still ask. Silent writes are worse than a two-second delay.

## Answer contract

Every substantive answer returns a structured object the UI renders:

```json
{
  "verdict": "short direct answer",
  "reasoning": "the chain, in plain language",
  "sources": [{"doc": "...", "tier": 1, "clause": "§2"}],
  "conflicts": [{"winner": "...", "loser": "...", "why": "..."}],
  "confidence": "high" | "medium" | "escalate",
  "suggested_action": null | {...preview...}
}
```

The `conflicts` array is the trust feature. When it is non-empty the UI shows a
banner: "Northstar's agreement overrides the standard 30-minute rule."

## Trade-offs to record in the architecture note

- Hand-rolled loop over LangGraph: full control of the event stream, no
  framework upgrade risk, but no free checkpointing or retries.
- SQLite over pandas-in-memory: gives a real query boundary to enforce scope at,
  and the `WHERE account_id = ?` clause is the thing a grader wants to see.
- Parameterised tool functions over text-to-SQL: text-to-SQL demos well but
  makes scope enforcement a string-parsing problem. Named tools with an injected
  filter cannot be prompt-injected into cross-account reads.
- Hybrid retrieval on six documents is arguably overkill; BM25 alone would score
  nearly as well. Kept because exact-ID and clause-number matching is where BM25
  wins and pure embeddings lose.
