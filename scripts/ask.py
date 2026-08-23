"""Phase 3 gate: run the agent from a terminal, before any UI exists.

    python -m scripts.ask customer-ACCT-001 "Can we cancel ORD-1001 without a fee?"
    python -m scripts.ask internal-ops "Escalate TKT-505"

Testing here first is deliberate: through a chat window a UI bug and an agent
bug look identical, and you can lose an afternoon to that.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.llm import MissingApiKey, QuotaExhausted  # noqa: E402
from app.agent.loop import run_turn  # noqa: E402
from app.db import connect  # noqa: E402
from app.ingest.build import ensure_loaded  # noqa: E402
from app.session import personas, session_from_persona  # noqa: E402

DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def main(argv: list[str]) -> int:
    conn = ensure_loaded(connect(":memory:"))

    if len(argv) < 3:
        print(__doc__)
        print("Available personas:")
        for persona in personas(conn):
            print(f"  {persona['id']:<22} {persona['user_name']}")
        return 2

    persona_id, question = argv[1], " ".join(argv[2:])
    try:
        session = session_from_persona(conn, persona_id, uuid.uuid4().hex)
    except KeyError:
        print(f"unknown persona {persona_id!r}")
        return 2

    print(f"{BOLD}{session.user_name}{RESET} asks: {question}\n")

    try:
        for event in run_turn(conn, session, [{"role": "user", "content": question}]):
            kind = event["type"]
            if kind == "tool_start":
                print(f"{DIM}  -> {event['tool']}({event['args']}){RESET}")
            elif kind == "tool_result":
                print(f"{DIM}     {event['summary']}{RESET}")
            elif kind == "confirm_required":
                print(f"\n{BOLD}CONFIRM REQUIRED{RESET} ({event['kind']})")
                for key, value in event["preview"].items():
                    print(f"     {key:<18} {value}")
                for warning in event.get("warnings", []):
                    print(f"     ! {warning}")
            elif kind == "escalation":
                print(f"\n{BOLD}ESCALATION{RESET}: {event['reason']}")
            elif kind == "answer":
                answer = event["answer"]
                print(f"\n{BOLD}{answer['verdict']}{RESET}\n")
                print(answer["prose"])
                if answer["conflicts"]:
                    print(f"\n{BOLD}Conflicts{RESET}")
                    for conflict in answer["conflicts"]:
                        print(f"  {conflict['winner']} beats {conflict['loser']}")
                        print(f"    {DIM}{conflict['why']}{RESET}")
                if answer["stale_guidance"]:
                    print(f"\n{BOLD}Stale guidance{RESET}")
                    for item in answer["stale_guidance"]:
                        print(f"  {item['ticket_id']}: {item['said']}")
                print(f"\n{BOLD}Sources{RESET}")
                for source in answer["sources"]:
                    print(f"  [T{source['tier']}] {source['doc']} {source.get('clause') or ''}")
                print(f"\nconfidence: {answer['confidence']}")
            elif kind == "error":
                print(f"error: {event['error']}")
    except (MissingApiKey, QuotaExhausted) as error:
        print(f"\n{error}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
