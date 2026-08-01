"""Rebuild the reviewer queue after a restart (design doc §5.5).

WHAT IS LOST IS NOT THE STATE. The state is in Postgres. What is lost is the
LIST of which threads are waiting — LangGraph has no "enumerate pending threads"
API, so you have to already know which ones to ask about.

`approvals WHERE decision IS NULL` is that list. For each row, ask the graph
whether an interrupt is genuinely pending.

Two outcomes, and the second is why this script exists:

  pending   row undecided, interrupt live      -> back in the queue
  orphaned  row undecided, NO live interrupt   -> nobody will ever be asked

An orphan is an invoice a human waits for forever. A script that reported only
the healthy case would hide exactly the failure it was written to find.
"""

import json
from typing import Any

from invoice_agent.checkpointer import build_checkpointer
from invoice_agent.db.engine import get_engine
from invoice_agent.db.repositories import approvals as approvals_repo
from invoice_agent.graphs.approval import build_approval_graph


def scan() -> dict[str, list[dict[str, Any]]]:
    with get_engine().begin() as conn:
        rows = approvals_repo.list_pending(conn)

    pending: list[dict[str, Any]] = []
    orphaned: list[dict[str, Any]] = []
    if not rows:
        return {"pending": pending, "orphaned": orphaned}

    with build_checkpointer() as saver:
        graph = build_approval_graph(checkpointer=saver)
        for row in rows:
            config = {"configurable": {"thread_id": row["thread_id"]}}
            snapshot = graph.get_state(config)
            record = {
                "invoice_id": str(row["invoice_id"]),
                "approval_id": str(row["id"]),
                "thread_id": row["thread_id"],
            }
            # `.interrupts` is the live-interrupt list on the state snapshot.
            # No interrupts means the thread never ran or its checkpoints are
            # gone.
            #
            # DIRECT ATTRIBUTE ACCESS, not `getattr(..., ())`. Verified
            # 2026-08-01 on langgraph 1.2.9: `interrupts` is present in
            # `StateSnapshot._fields`. A default would be error handling for a
            # scenario that cannot happen today, and it would turn a future
            # rename into "every thread is orphaned" — silently emptying the
            # reviewer queue — instead of an AttributeError naming the cause.
            # The version is pinned (roadmap §0 rule 6), so a rename is an
            # intentional migration, and this is where it should fail loudly.
            if snapshot.interrupts:
                pending.append(record)
            else:
                orphaned.append(record)

    return {"pending": pending, "orphaned": orphaned}


def main() -> None:
    found = scan()
    print(json.dumps(found, indent=2, ensure_ascii=False))
    print(
        f"\n{len(found['pending'])} awaiting a reviewer, "
        f"{len(found['orphaned'])} orphaned (undecided with no live interrupt)"
    )


if __name__ == "__main__":
    main()
