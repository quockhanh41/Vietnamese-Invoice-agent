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
from invoice_agent.observability import traced


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


@traced(node="load_snapshot")
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


@traced(node="suggest")
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


@traced(node="approval_gate")
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


@traced(node="apply_decision")
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


@traced(node="post_entry")
def post_entry_node(state: ApprovalState) -> dict[str, Any]:
    """Post through the guard, with human authority.

    A `GuardDenied` here PROPAGATES. `auto_post` catches it and routes the
    invoice to a reviewer, but a reviewer has already decided this one — a
    denial means the guard and the human disagree, and that has to surface. The
    thread stays resumable, so the disagreement can be shown to them.
    """
    import uuid as _uuid

    from invoice_agent.db.repositories import invoices as invoices_repo
    from invoice_agent.db.repositories import reference
    from invoice_agent.domain import accounting
    from invoice_agent.tools import guards

    invoice_id = _uuid.UUID(state["invoice_id"])
    approval_id = _uuid.UUID(state["approval_id"])

    with get_engine().begin() as conn:
        row = invoices_repo.get_by_id(conn, invoice_id)
        if row is None:
            raise ValueError(f"invoice not found: {invoice_id}")
        cat_row = reference.get_category(conn, row["category"]) if row["category"] else None
        if cat_row is None:
            raise ValueError(
                f"{invoice_id}: approved but its category {row['category']!r} has no seeded "
                "accounts — nothing to book against, and inventing one is what M4-D removed"
            )
        accounts = accounting.CategoryAccounts(
            debit_account=cat_row["debit_account"],
            debit_name=cat_row["name_vi"],
            vat_account=cat_row["vat_account"],
            vat_name="Thuế GTGT được khấu trừ" if cat_row["vat_account"] else None,
            credit_account=cat_row["credit_account"],
            credit_name="Phải trả cho người bán",
        )
        invoice_data = _rebuild_invoice_data(conn, invoice_id)
        lines = accounting.build_journal_entry(invoice_data, accounts)
        entry_id = guards.create_journal_entry(
            conn,
            invoice_id=invoice_id,
            posting_authority="human_approved",
            approval_id=approval_id,
            entry_date=invoice_data.issue_date,
            lines=lines,
            # The ONLY place `human:` is added. See ApprovalState.decided_by.
            actor=f"human:{state['decided_by']}",
        )
    return {"entry_id": str(entry_id), "status": "posted"}


def _rebuild_invoice_data(conn: Any, invoice_id: Any) -> Any:
    """Rebuild `InvoiceData` from the DATABASE, not from the snapshot.

    The snapshot is stringified JSON built for a human to read, and it may be
    days old. The journal entry must be computed from the source of truth, by
    the same `accounting.build_journal_entry` the auto path uses — otherwise
    the two paths could book different numbers for the same invoice.
    """
    from invoice_agent.db.repositories import invoices as invoices_repo
    from invoice_agent.extraction.models import InvoiceData, InvoiceLine

    row = invoices_repo.get_by_id(conn, invoice_id)
    if row is None:
        raise ValueError(f"invoice not found: {invoice_id}")
    lines = [
        InvoiceLine(
            line_no=line["line_no"],
            description=line["description"],
            unit=line["unit"],
            quantity=line["quantity"],
            unit_price=line["unit_price"],
            amount=line["amount"],
            vat_rate=line["vat_rate"],
            vat_amount=line["vat_amount"],
            item_kind=line["item_kind"],
            ext=line["ext"],
        )
        for line in invoices_repo.get_lines(conn, invoice_id)
    ]
    return InvoiceData(
        template_no=row["template_no"],
        invoice_series=row["invoice_series"],
        invoice_number=row["invoice_number"],
        seller_tax_code=row["seller_tax_code"],
        seller_name=None,
        buyer_tax_code=row["buyer_tax_code"],
        issue_date=row["issue_date"],
        currency=row["currency"],
        exchange_rate=row["exchange_rate"],
        subtotal=row["subtotal"],
        vat_amount=row["vat_amount"],
        total_amount=row["total_amount"],
        total_in_words=row["total_in_words"],
        ext=row["ext"],
        lines=lines,
    )



@traced(node="record_rejection")
def record_rejection_node(state: ApprovalState) -> dict[str, Any]:
    import uuid as _uuid

    from invoice_agent.db.repositories import invoices as invoices_repo

    with get_engine().begin() as conn:
        invoices_repo.set_status(conn, _uuid.UUID(state["invoice_id"]), "rejected")
    return {"status": "rejected"}


def route_after_decision(state: ApprovalState) -> str:
    """`edited` posts. A reviewer who corrected a field and let the invoice
    through has approved it — decided in M5-A spec §4.1 so this inherits an
    answer rather than a guess.
    """
    return "post_entry" if state["decision"] in ("approved", "edited") else "record_rejection"


@traced(node="learn")
def learn_node(state: ApprovalState) -> dict[str, Any]:
    """HITL feeds memory (design §5.4) — one mechanism, not two features.

    Records an episode on EVERY decision, not only rejections. Design §5.4 names
    the reject arrow, but `suggest` asks "what happened last time for this
    vendor" and approvals are the majority answer; a memory holding only
    refusals biases every suggestion it makes. Deliberate extension past the
    design doc's literal wording (spec §4.6).
    """
    import uuid as _uuid
    from decimal import Decimal as _Decimal

    from invoice_agent.db.repositories import episodic, vendors

    snapshot = state["snapshot"]
    tax_code = snapshot.get("seller_tax_code")
    if not tax_code:
        return {"status": "learned"}

    with get_engine().begin() as conn:
        episodic.record_episode(
            conn,
            invoice_id=_uuid.UUID(state["invoice_id"]),
            approval_id=_uuid.UUID(state["approval_id"]),
            vendor_tax_code=tax_code,
            amount=_Decimal(snapshot.get("total_amount") or "0"),
            flags={"codes": [f["code"] for f in state.get("flags") or []]},
            decision=state["decision"],
            category=snapshot.get("category"),
            department=snapshot.get("department"),
            reason=state.get("reason"),
        )

        if state["decision"] == "rejected":
            current = vendors.get_vendor(conn, tax_code) or {}
            vendors.update_profile(
                conn, tax_code, rejection_count=int(current.get("rejection_count") or 0) + 1
            )

        corrections = state.get("corrections")
        if corrections:
            # Appended, not overwritten: this field is a running log a reviewer
            # writes to over time, and `classify` reads the whole thing.
            current = vendors.get_vendor(conn, tax_code) or {}
            previous = current.get("reviewer_notes") or ""
            entry = f"[{state['decided_by']}] " + ", ".join(
                f"{k}={v}" for k, v in sorted(corrections.items())
            )
            vendors.update_profile(
                conn, tax_code, reviewer_notes=(previous + "\n" + entry).strip()
            )

    return {"status": "learned"}


def build_approval_graph(*, checkpointer: Any = None) -> Any:
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(ApprovalState)
    builder.add_node("load_snapshot", load_snapshot_node)
    builder.add_node("suggest", suggest_node)
    builder.add_node("approval_gate", approval_gate_node)
    builder.add_node("apply_decision", apply_decision_node)
    builder.add_node("post_entry", post_entry_node)
    builder.add_node("record_rejection", record_rejection_node)
    builder.add_node("learn", learn_node)

    builder.add_edge(START, "load_snapshot")
    builder.add_edge("load_snapshot", "suggest")
    builder.add_edge("suggest", "approval_gate")
    builder.add_edge("approval_gate", "apply_decision")
    builder.add_conditional_edges(
        "apply_decision",
        route_after_decision,
        {"post_entry": "post_entry", "record_rejection": "record_rejection"},
    )
    builder.add_edge("post_entry", "learn")
    builder.add_edge("record_rejection", "learn")
    builder.add_edge("learn", END)

    return builder.compile(checkpointer=checkpointer)


VALID_DECISIONS = ("approved", "rejected", "edited")


def submit_decision(
    *,
    invoice_id: str,
    decision: str,
    decided_by: str,
    reason: str | None = None,
    corrections: dict[str, Any] | None = None,
    graph_builder: Any = None,
) -> dict[str, Any]:
    """Resume a pending approval thread. THE SEAM M5-C WRAPS.

    The FastAPI route parses a request body and calls this; it adds no logic.
    That is what makes "interrupt is transport-agnostic" (the M7 artefact)
    demonstrable rather than asserted — a test, a script, HTTP and MCP all drive
    the identical function.

    `decision` is validated BEFORE the graph is touched. `route_after_decision`
    sends anything that is not approved/edited to `record_rejection`, so an
    unvalidated typo would silently reject a good invoice.

    `graph_builder` EXISTS FOR ONE CALLER, and it is worth naming rather than
    leaving as an unexplained parameter. `tests/test_hitl_survives_restart.py`
    resumes through this function but needs the node-counting graph from
    `tests/helpers/hitl_child.py`, because the "`approval_gate` ran twice"
    measurement cannot come from checkpoint history — the gate's first pass
    raises `GraphInterrupt` and commits nothing (measured 2026-07-27 at M1.5).
    Without the seam the durability test would have to re-implement the resume
    instead of exercising the production path, which is the more valuable thing
    to test.

    The cost is a test-shaped hole in production code, and it is bounded by
    `test_the_counted_graph_matches_the_real_one`: the injected builder must
    render byte-identical mermaid to `build_approval_graph`, so it cannot be a
    different graph. Same arrangement as `build_enrichment_graph(gateway=…)`.
    """
    from langgraph.types import Command

    from invoice_agent.checkpointer import build_checkpointer

    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of {VALID_DECISIONS}, got {decision!r}")

    config = {"configurable": {"thread_id": f"appr:{invoice_id}"}}
    with build_checkpointer() as saver:
        builder = graph_builder or build_approval_graph
        app = builder(checkpointer=saver)
        return dict(
            app.invoke(
                Command(
                    resume={
                        "decision": decision,
                        "decided_by": decided_by,
                        "reason": reason,
                        "corrections": corrections,
                    }
                ),
                config,
            )
        )





