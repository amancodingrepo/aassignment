"""The tool-calling loop.

Hand-rolled rather than built on an agent framework. The loop is about eighty
lines and every tool call has to be inspected, gated and streamed to the
interface as a trace the reviewer can read -- which frameworks make harder, not
easier, and in exchange for checkpointing and retries this system does not need.
The cost is real and worth naming: no free persistence, no built-in retry.

Events emitted, in the order the interface renders them:
    tool_start, tool_result, token, confirm_required, answer, escalation, done
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterator
from typing import Any

from app.agent import policy, prompts
from app.config import MAX_AGENT_ITERATIONS, MODEL
from app.session import Session
from app.tools import gate
from app.tools.registry import REGISTRY, schemas_for

ANSWER_BLOCK_RE = re.compile(r"```answer\s*(?P<body>\{.*?\})\s*```", re.DOTALL)

# Result keys too bulky to put in the trace card. The full result still goes to
# the model; this only controls what the interface shows by default.
_TRACE_SUMMARY_KEYS = (
    "found",
    "count",
    "fee_inr",
    "amount_inr",
    "eligible",
    "cancellable",
    "rule_applied",
    "severity",
    "target_text",
    "elapsed_minutes",
    "breached",
    "denied",
    "reason",
    "ok",
)


class MissingApiKey(RuntimeError):
    pass


def _client():
    """Imported and constructed lazily so the rest of the app runs without a key."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise MissingApiKey(
            "ANTHROPIC_API_KEY is not set. The ingest, calculators, gate, "
            "signals and confirm flow all work without it; only the chat loop "
            "needs a model."
        )
    from anthropic import Anthropic

    return Anthropic(api_key=api_key)


def _account_context(conn: sqlite3.Connection, session: Session) -> dict[str, Any]:
    if not session.account_id:
        return {}
    row = conn.execute(
        "SELECT account_name, plan FROM accounts WHERE account_id = ?",
        (session.account_id,),
    ).fetchone()
    if row is None:
        return {}
    return {
        "account_name": row["account_name"],
        "account_id": session.account_id,
        "plan": row["plan"],
    }


def _summarise(name: str, result: Any) -> str:
    """One readable line per tool call, for the trace card."""
    if not isinstance(result, dict):
        return str(result)[:200]
    if result.get("denied"):
        return f"denied: {result.get('reason')}"
    if name == "search_documents":
        titles = [
            f"{chunk['doc_title']} §{chunk['section']} (tier {chunk['tier']})"
            for chunk in result.get("chunks", [])[:4]
        ]
        return f"{len(result.get('chunks', []))} clauses: " + "; ".join(titles)
    if name == "lookup_orders":
        rows = result.get("orders", [])
        return (
            f"{len(rows)} order(s)"
            + (f": {rows[0]['order_id']} {rows[0]['status']}" if rows else " - none found")
        )
    if name == "lookup_tickets":
        rows = result.get("tickets", [])
        return (
            f"{len(rows)} ticket(s)"
            + (f": {rows[0]['ticket_id']} {rows[0]['status']}" if rows else " - none found")
        )
    if name == "lookup_account":
        account = result.get("account")
        if account:
            contract = "contract on file" if account.get("has_contract") else "no contract"
            return f"{account.get('account_name')} - {account.get('plan')}, {contract}"
        return f"{result.get('count', 0)} account(s)"
    if name == "propose_action":
        return "preview created, awaiting confirmation" if result.get("ok") else str(result.get("error"))

    parts = [f"{key}={result[key]}" for key in _TRACE_SUMMARY_KEYS if key in result]
    return ", ".join(parts) or "ok"


def _extract_answer_block(text: str) -> tuple[str, dict[str, Any] | None]:
    match = ANSWER_BLOCK_RE.search(text)
    if not match:
        return text.strip(), None
    try:
        parsed = json.loads(match.group("body"))
    except json.JSONDecodeError:
        return text.strip(), None
    prose = (text[: match.start()] + text[match.end() :]).strip()
    return prose, parsed


def _collect_sources_and_conflicts(
    tool_results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[int]]:
    sources: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    tiers: set[int] = set()
    seen_sources: set[str] = set()
    seen_conflicts: set[str] = set()

    for result in tool_results:
        for node in policy._walk(result):
            for conflict in node.get("conflicts") or []:
                if not isinstance(conflict, dict):
                    continue
                key = json.dumps(conflict, sort_keys=True)
                if key not in seen_conflicts:
                    seen_conflicts.add(key)
                    conflicts.append(conflict)

        for chunk in result.get("chunks") or []:
            key = chunk.get("chunk_id")
            if key and key not in seen_sources:
                seen_sources.add(key)
                sources.append(
                    {
                        "chunk_id": key,
                        "doc": chunk.get("doc_title") or chunk.get("doc"),
                        "tier": chunk.get("tier"),
                        "tier_label": chunk.get("tier_label"),
                        "clause": chunk.get("clause"),
                        "section_title": chunk.get("section_title"),
                        "status": chunk.get("status"),
                        "scope": chunk.get("scope"),
                    }
                )
                if isinstance(chunk.get("tier"), int):
                    tiers.add(chunk["tier"])

        for key_name in ("source", "waiver_source", "monthly_cap_source", "approval_source"):
            source = result.get(key_name)
            if isinstance(source, dict) and source.get("chunk_id"):
                if source["chunk_id"] not in seen_sources:
                    seen_sources.add(source["chunk_id"])
                    sources.append(source)
                if isinstance(source.get("tier"), int):
                    tiers.add(source["tier"])

    sources.sort(key=lambda item: (item.get("tier") or 99))
    return sources, conflicts, tiers


def run_turn(
    conn: sqlite3.Connection,
    session: Session,
    messages: list[dict[str, Any]],
) -> Iterator[dict[str, Any]]:
    """Run one user turn to completion, yielding SSE-shaped events."""
    context = _account_context(conn, session)
    system = prompts.system_prompt(session.role, **context) + "\n\n" + prompts.ANSWER_CONTRACT
    tools = schemas_for(session.role)

    client = _client()
    conversation = list(messages)
    user_message = next(
        (
            message["content"]
            for message in reversed(conversation)
            if message.get("role") == "user" and isinstance(message.get("content"), str)
        ),
        "",
    )

    tool_results: list[dict[str, Any]] = []
    pending_confirmations: list[dict[str, Any]] = []
    final_text = ""

    for iteration in range(MAX_AGENT_ITERATIONS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system,
            tools=tools,
            messages=conversation,
        )

        text_parts = [block.text for block in response.content if block.type == "text"]
        tool_uses = [block for block in response.content if block.type == "tool_use"]

        if text_parts:
            joined = "\n".join(text_parts)
            final_text = joined
            yield {"type": "token", "text": joined}

        if not tool_uses:
            break

        conversation.append({"role": "assistant", "content": response.content})
        tool_result_blocks = []

        for block in tool_uses:
            args = dict(block.input or {})
            yield {
                "type": "tool_start",
                "id": block.id,
                "tool": block.name,
                "args": args,
                "iteration": iteration + 1,
            }

            result = gate.call_tool(conn, session, block.name, args)
            tool_results.append(result if isinstance(result, dict) else {"result": result})

            if isinstance(result, dict) and result.get("requires_confirmation"):
                pending_confirmations.append(result)
                yield {
                    "type": "confirm_required",
                    "token": result["token"],
                    "kind": result["kind"],
                    "preview": result["preview"],
                    "warnings": result.get("warnings", []),
                    "expires_in_seconds": result.get("expires_in_seconds"),
                }

            yield {
                "type": "tool_result",
                "id": block.id,
                "tool": block.name,
                "summary": _summarise(block.name, result),
                "result": result,
            }

            tool_result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                }
            )

        conversation.append({"role": "user", "content": tool_result_blocks})
    else:
        # Step budget exhausted without a final answer.
        yield {
            "type": "escalation",
            "reason": "could not resolve within step budget",
            "iterations": MAX_AGENT_ITERATIONS,
        }

    sources, conflicts, tiers = _collect_sources_and_conflicts(tool_results)
    assessment = policy.assess(user_message, tool_results, retrieved_tiers=tiers)
    assessment.stale_guidance = policy.find_stale_guidance(tool_results, conflicts)

    prose, declared = _extract_answer_block(final_text)
    verdict = (declared or {}).get("verdict")
    reasoning = (declared or {}).get("reasoning")

    # The model may raise its own confidence concern, but it cannot lower the
    # one the rules decided on.
    confidence = assessment.confidence
    if declared and declared.get("confidence") == policy.ESCALATE:
        confidence = policy.ESCALATE

    if assessment.escalate:
        yield {
            "type": "escalation",
            "reason": "; ".join(assessment.reasons),
            "reasons": assessment.reasons,
            "unsupported_capabilities": assessment.unsupported,
        }

    yield {
        "type": "answer",
        "answer": {
            "verdict": verdict or prose[:400],
            "reasoning": reasoning or prose,
            "prose": prose,
            "sources": sources,
            "conflicts": conflicts,
            "confidence": confidence,
            "stale_guidance": assessment.stale_guidance,
            "escalation": assessment.to_dict() if assessment.escalate else None,
            "suggested_action": pending_confirmations[0] if pending_confirmations else None,
        },
    }
    yield {"type": "done", "tool_calls": len(tool_results)}


def available_tools(role: str) -> list[str]:
    return [name for name, spec in REGISTRY.items() if not (spec.requires_internal and role != "internal")]
