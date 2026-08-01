"""approval_graph — the human decision, made durable (design doc §5).

    START → load_snapshot → suggest → approval_gate ⏸ → apply_decision
    apply_decision ─cond→ { approved|edited: post_entry, rejected: record_rejection }
    post_entry → learn → END          record_rejection → learn → END

THREE THINGS ABOUT THIS FILE ARE LOAD-BEARING, and each was measured rather
than reasoned:

1. `approval_gate` contains `interrupt()` AND NOTHING ELSE. Measured 2026-07-27
   at M1.5 (langgraph 1.2.9): the node containing the interrupt runs TWICE
   across a `kill -9` + resume, the node before it runs once. A write placed in
   that node happens twice; graph shape is what stops everything except the
   journal entry, which `UNIQUE(invoice_id)` and M5-A's guard stop.

2. `apply_decision` COMMITS on its own connection, and `post_entry` reads on a
   different one. The guard's whole value is re-reading `approvals` from
   Postgres (design §5.3 step 2). Share a transaction and it reads its own
   caller's uncommitted write — which passes every test that does not
   specifically mutate for it.

3. `ApprovalState` is plain JSON. An `appr:` thread lives HOURS TO DAYS
   (design §1.3) while every other thread here lives minutes, so its checkpoint
   must survive a deploy landing between the interrupt and the decision.
"""

from datetime import date
from decimal import Decimal
from typing import Any, TypedDict

from invoice_agent.db.engine import get_engine


class ApprovalState(TypedDict):
    invoice_id: str
    approval_id: str
    snapshot: dict[str, Any]
    flags: list[dict[str, Any]]
    suggestions: list[dict[str, Any]]
    decision: str
    # BARE name. `approvals.decided_by` stores "ketoan"; `audit_log.actor`
    # stores "human:ketoan" (the convention in db/models.py). Prefixing an
    # already-prefixed value yields "human:human:ketoan" and no constraint
    # catches it.
    decided_by: str
    reason: str | None
    corrections: dict[str, Any] | None
    entry_id: str | None
    status: str


def _jsonable(value: Any) -> Any:
    """Decimal, date, and UUID become strings AT THE BOUNDARY.

    Both land in JSONB and in a checkpoint. Converting here rather than deep in
    a node means one place to look when a number arrives as a string.
    """
    import uuid as _uuid

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value



def _with_conn(conn: Any, fn: Any, state: Any) -> dict[str, Any]:
    if conn is not None:
        return fn(conn, state)
    with get_engine().begin() as owned:
        return fn(owned, state)


def load_snapshot_node(state: ApprovalState, *, conn: Any = None) -> dict[str, Any]:
    return _with_conn(conn, _load_snapshot, state)


def _load_snapshot(conn: Any, state: ApprovalState) -> dict[str, Any]:
    import uuid as _uuid

    from invoice_agent.db.repositories import flags, invoices

    invoice_id = _uuid.UUID(state["invoice_id"])
    row = invoices.get_by_id(conn, invoice_id)
    if row is None:
        raise ValueError(f"approval opened for an invoice that does not exist: {invoice_id}")

    snapshot = _jsonable({k: v for k, v in row.items() if k not in ("raw_xml",)})
    snapshot["id"] = str(row["id"])
    snapshot["lines"] = _jsonable([dict(line) for line in invoices.get_lines(conn, invoice_id)])
    open_flags = _jsonable([dict(f) for f in flags.open_flags_for(conn, invoice_id)])
    return {"snapshot": snapshot, "flags": open_flags, "status": "loaded"}


def suggest_node(state: ApprovalState, *, conn: Any = None) -> dict[str, Any]:
    return _with_conn(conn, _suggest, state)


def _suggest(conn: Any, state: ApprovalState) -> dict[str, Any]:
    """Episodic recall. NO LLM — design §5.1: this node sits on the path the
    batch is waiting for, and a model call here would make every reviewer queue
    entry cost a token and a round trip.

    Returns [] on a fresh database, because `episodic_decisions` is empty until
    reviewers decide things. Reported as measured rather than seeded.
    """
    import uuid as _uuid

    from invoice_agent.db.repositories import approvals as approvals_repo
    from invoice_agent.db.repositories import episodic

    snapshot = state["snapshot"]
    episodes = episodic.similar_episodes(
        conn,
        vendor_tax_code=snapshot.get("seller_tax_code", ""),
        category=snapshot.get("category"),
        limit=5,
    )
    suggestions = _jsonable([dict(e) for e in episodes])

    payload = {
        "invoice": snapshot,
        "flags": state["flags"],
        "suggestions": suggestions,
    }
    # Written HERE, before the gate. See the module docstring, point 1.
    approvals_repo.record_context(
        conn,
        _uuid.UUID(state["approval_id"]),
        interrupt_payload=payload,
        flags_snapshot={"flags": state["flags"]},
        suggestions={"episodes": suggestions},
    )
    return {"suggestions": suggestions, "status": "suggested"}


def approval_gate_node(state: ApprovalState) -> dict[str, Any]:
    """`interrupt()` and nothing else.

    NOT a stylistic preference. Measured 2026-07-27 at M1.5 across a real
    `kill -9`: this node's body runs TWICE — once in the process that died,
    once in the process that resumed. Anything added here happens twice.
    `tests/test_approval_graph.py` guards it statically and Task 9's durability
    test guards it behaviourally.
    """
    from langgraph.types import interrupt

    decision = interrupt(
        {
            "invoice": state["snapshot"],
            # Vietnamese, and it stays Vietnamese: a Vietnamese accountant reads
            # these. CLAUDE.md puts product output on the other side of the
            # authoring-language rule.
            "flags": state["flags"],
            "suggestions": state["suggestions"],
        }
    )
    return {
        "decision": decision["decision"],
        "decided_by": decision["decided_by"],
        "reason": decision.get("reason"),
        "corrections": decision.get("corrections"),
    }


def apply_decision_node(state: ApprovalState) -> dict[str, Any]:
    """Record the decision and close the flags. OWNS ITS TRANSACTION.

    NO `conn` PARAMETER, deliberately. `post_entry` re-reads `approvals` from
    Postgres to verify the decision persisted (design §5.3 step 2). If a caller
    could hand this node the same transaction `post_entry` later reads on, the
    guard would be verifying its own caller's uncommitted write — indistinguish-
    able from a working guard in every test that does not mutate for it
    (spec §2.3). Accepting no connection makes that arrangement unexpressible.

    Flags are resolved on BOTH outcomes, which answers M5-A spec §8 question 1:
    a human has adjudicated them either way. `resolved_at` is set; nothing is
    deleted, so the false-positive metric can still count them. Note the
    consequence: the DB trigger stops blocking `auto_clean` for this invoice.
    It cannot be re-posted regardless — `UNIQUE(invoice_id)` and M5-A's
    idempotency fork both hold.
    """
    import uuid as _uuid

    from invoice_agent.db.repositories import approvals as approvals_repo
    from invoice_agent.db.repositories import flags

    invoice_id = _uuid.UUID(state["invoice_id"])
    approval_id = _uuid.UUID(state["approval_id"])

    with get_engine().begin() as conn:
        approvals_repo.record_decision(
            conn,
            approval_id,
            decision=state["decision"],
            decided_by=state["decided_by"],
            reason=state.get("reason"),
            field_corrections=state.get("corrections"),
        )
        for row in flags.open_flags_for(conn, invoice_id):
            flags.resolve_flag(conn, row["id"])

    return {"status": "decided"}


def build_approval_graph(*, checkpointer: Any = None) -> Any:

    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(ApprovalState)
    builder.add_node("load_snapshot", load_snapshot_node)
    builder.add_node("suggest", suggest_node)
    builder.add_node("approval_gate", approval_gate_node)

    builder.add_edge(START, "load_snapshot")
    builder.add_edge("load_snapshot", "suggest")
    builder.add_edge("suggest", "approval_gate")
    builder.add_edge("approval_gate", END)

    return builder.compile(checkpointer=checkpointer)

