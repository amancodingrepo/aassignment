# Tools, scope gate, and the confirm protocol

## The gate

Every tool call passes through `tools/gate.py`. Nothing calls a tool module
directly.

```python
def call_tool(session, name, args):
    spec = REGISTRY[name]
    if spec.requires_internal and session.role != "internal":
        return deny(session, name, args, "internal-only tool")
    if spec.account_scoped:
        if session.role == "customer":
            args["account_id"] = session.account_id      # inject, overwrite
        elif "account_id" not in args and spec.requires_account:
            return deny(session, name, args, "internal caller must name an account")
    result = spec.fn(**args)
    result = strip_internal_fields(result, session.role)
    audit(session, name, args, allowed=True)
    return result
```

Two details carry the weight. The injection is an **overwrite**, not a default —
if the model supplies `account_id="ACCT-001"` while the session is ACCT-002, the
value is silently replaced, the call returns the caller's own data, and the
attempt is written to `audit_log`. And `account_id` is removed from the
customer-mode tool schemas entirely, so a well-behaved model never sends it and
a jailbroken one gains nothing by sending it.

Write `tests/test_gate.py` first:

- customer ACCT-002 calling `get_order("ORD-1001")` → not found, not a leak
- customer calling `search_documents` for Northstar terms → zero tier-1 chunks
- customer calling `list_signals` → denied
- customer with an injected `account_id` override → audit row with the attempt
- internal calling `get_order("ORD-1001")` → full record including `notes`

## Tool set

Three are required; six is the right number. Keep each one narrow — a model
picks correctly between narrow tools and badly between broad ones.

### 1. `search_documents`
`(query: str, topics?: string[], include_deprecated?: bool)` → chunks with
metadata. Covered in `03_SOURCE_AUTHORITY.md`. `include_deprecated` is rejected
by the gate for customers.

### 2. `lookup_orders`
`(order_id?: str, status?: str, carrier?: str, limit?: int)` → order rows with
the derived fields from `02_DATA_MODEL.md`. Account filter injected.

### 3. `lookup_tickets`
`(ticket_id?: str, status?: str, since?: str, limit?: int)` → ticket rows.
`historical_resolution` is returned **wrapped**:
`{"value": "...", "authority": "context_only", "warning": "may be incorrect"}`.
Wrapping at the tool layer rather than hoping the prompt holds is the difference
between a demo that survives adversarial questions and one that doesn't.

### 4. `lookup_account`
`(account_id?: str)` → plan, status, contract file, premium support flag.
Internal fields stripped for customers. This is how the agent learns which
contract to search for; it should not guess from the account name.

### 5. `compute` — deterministic, no LLM arithmetic
Three sub-functions, exposed as one tool with a `kind` discriminator or as three
tools if the model confuses them in testing:

- `cancellation_fee(order_id)` → `{fee_inr, rule_applied, source, blocked?}`
- `service_credit(order_id)` → `{eligible, amount_inr, rule_applied, source, needs_manager_approval, unknowns[]}`
- `sla_status(ticket_id, severity)` → `{target, elapsed, breached, breach_by, source}`

Each returns the *source clause id* it applied, so the citation chain is
machine-generated rather than model-generated. Exact rule logic and expected
outputs are in `06_EVAL_SET.md` — implement against those cases.

`service_credit` must return `eligible: "unknown"` with a populated `unknowns`
list when fault fields are null, and the agent must escalate rather than answer.
The SOP is explicit that a credit is not promised when carrier fault, pickup
timing, or customer fault is unknown.

### 6. `propose_action` / `execute_action`
The state-changing pair. Kinds: `escalation`, `ticket_update`, `follow_up_task`.

## The confirm protocol

```
propose_action(kind, ticket_id, payload)
  -> writes nothing
  -> returns {token, kind, preview: {...}, expires_at, warnings: []}
  -> server stores the preview keyed by token, bound to session id
UI renders a confirm card showing every field that will be written
User clicks Confirm
  -> POST /confirm {token}
  -> server validates: token exists, unexpired, same session, not already used
  -> executes, writes to actions + audit_log, returns action_id
```

Rules:

- The token is single-use and expires in 10 minutes.
- The token is bound to the session that created it. A different session
  presenting it is rejected.
- The model cannot call `execute_action`; it is not in the tool registry. Only
  the `/confirm` HTTP endpoint executes. This makes it structurally impossible
  for a model turn to write state — worth stating in the architecture note,
  because "the model asks nicely before writing" and "the model cannot write" are
  very different security properties.
- If the user edits a field in the confirm card, the token is invalidated and a
  fresh proposal is generated.
- Cancelling marks the proposal `cancelled` and is logged.

Preview payload for an escalation:

```json
{ "kind": "escalation", "ticket_id": "TKT-505", "account": "Axis Labs (ACCT-004)",
  "severity": "P1", "reason": "Suspected credential exposure per Support Policy v3 §2",
  "sla_target": "30 minutes, 24x7", "elapsed": "2h30m", "breached": true,
  "assign_to": "on-call security", "notify": "Priya Mehta (CSM)",
  "sources": ["01_Support_Policy_v3 §2", "01_Support_Policy_v3 §3"] }
```

## Multi-step behaviour

The brief requires requests spanning several tools. The canonical chain, which
should be visible in the UI trace during the demo:

```
"Can Northstar cancel ORD-1001 without a fee?"
  lookup_orders(ORD-1001)        -> BOOKED, booked 09:00, cancel req 11:00, ACCT-001
  lookup_account(ACCT-001)       -> Enterprise, contract 05_Northstar
  search_documents("cancellation fee waiver", topics=[cancellation])
                                 -> Northstar §2 (t1), SOP v4 §1 (t2)
  compute(cancellation_fee, ORD-1001)
                                 -> 0, rule "contract_waiver", source Northstar §2
  answer with conflict banner
```

Do not let the model shortcut this by answering from the contract alone. The
order status check is what prevents it from cheerfully waiving a fee on
ORD-1002, which is already PICKED_UP and cannot be cancelled at all.
