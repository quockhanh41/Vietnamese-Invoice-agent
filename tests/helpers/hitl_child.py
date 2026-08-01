"""Runs the REAL approval graph to its interrupt, in a process built to be killed.

Must be a real process (`sys.executable`), not a thread or a fork sharing
memory: the whole point is that state survives the process DYING — no
`__del__`, no atexit flush, no surviving Python cache.

THE COUNTERS LIVE HERE, NOT IN `approval.py`. The assertion "suggest ran once,
approval_gate ran twice" cannot come from checkpoint history — measured
2026-07-27 at M1.5: the gate's first pass raises GraphInterrupt and commits
nothing, so it appears once in `trace` whether its body ran once or twice. So
the nodes are wrapped HERE, before compiling, and `approval.py` never learns it
is being measured.

Usage: python -m tests.helpers.hitl_child <invoice_id> <approval_id>
Prints "INTERRUPTED" (flushed) when the graph parks, then sleeps forever.
"""

import sys
import time

from invoice_agent.checkpointer import build_checkpointer
from invoice_agent.graphs import approval


def _counting(fn, thread_id: str, node: str):
    def wrapped(state, *args, **kwargs):
        import psycopg

        from invoice_agent.config import get_settings

        with psycopg.connect(get_settings().app_dsn) as conn:
            conn.execute(
                "INSERT INTO app.spike_counter (thread_id, node) VALUES (%s, %s)",
                (thread_id, node),
            )
        return fn(state, *args, **kwargs)

    return wrapped


def build_counted_graph(thread_id: str, saver):
    """The real graph, with two nodes wrapped in counters."""
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(approval.ApprovalState)
    builder.add_node(
        "load_snapshot", _counting(approval.load_snapshot_node, thread_id, "load_snapshot")
    )
    builder.add_node("suggest", _counting(approval.suggest_node, thread_id, "suggest"))
    builder.add_node(
        "approval_gate", _counting(approval.approval_gate_node, thread_id, "approval_gate")
    )
    builder.add_node("apply_decision", approval.apply_decision_node)
    builder.add_node("post_entry", approval.post_entry_node)
    builder.add_node("record_rejection", approval.record_rejection_node)
    builder.add_node("learn", approval.learn_node)

    builder.add_edge(START, "load_snapshot")
    builder.add_edge("load_snapshot", "suggest")
    builder.add_edge("suggest", "approval_gate")
    builder.add_edge("approval_gate", "apply_decision")
    builder.add_conditional_edges(
        "apply_decision",
        approval.route_after_decision,
        {"post_entry": "post_entry", "record_rejection": "record_rejection"},
    )
    builder.add_edge("post_entry", "learn")
    builder.add_edge("record_rejection", "learn")
    builder.add_edge("learn", END)
    return builder.compile(checkpointer=saver)


def main(invoice_id: str, approval_id: str) -> None:
    thread_id = f"appr:{invoice_id}"
    config = {"configurable": {"thread_id": thread_id}}
    with build_checkpointer() as saver:
        saver.setup()
        app = build_counted_graph(thread_id, saver)
        result = app.invoke(
            {
                "invoice_id": invoice_id,
                "approval_id": approval_id,
                "snapshot": {},
                "flags": [],
                "suggestions": [],
                "decision": "",
                "decided_by": "",
                "reason": None,
                "corrections": None,
                "entry_id": None,
                "status": "",
            },
            config,
        )
        if "__interrupt__" not in result:
            print("NO_INTERRUPT", flush=True)
            sys.exit(2)
        print("INTERRUPTED", flush=True)

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
