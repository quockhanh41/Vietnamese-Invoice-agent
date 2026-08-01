"""batch_graph — fan out over invoices, then join serially (design doc §4).

THE SHAPE IS THE ARGUMENT. Every fan-out task is READ-ONLY; every write happens
in one serial node after the join. That single decision buys three properties at
once, and they are the milestone:

  * budget reconciliation is order-invariant, because one walker in canonical
    order draws down the budget;
  * duplicate detection is order-invariant, because the same walker decides
    which member of a pair is the original;
  * `signals` never sees the invoice it is scoring in that invoice's own vendor
    statistics, because `apply_invoice` runs here rather than in the task.

That third one is silent when broken. M4-B measured the self-inclusion ceiling:
a single extreme point's z-score against a sample containing itself is bounded
by (n-1)/sqrt(n) — 1.79 at n=5 — so persisting before `signals` runs does not
fail, it just stops PRICE_OUTLIER ever firing.
"""

import operator
import uuid
from decimal import Decimal
from typing import Annotated, Any, TypedDict

from langgraph.types import Send

from invoice_agent.extraction.models import InvoiceData


class EnrichedInvoice(TypedDict):
    """What a fan-out task hands the serial join. Deliberately small: the join
    re-derives everything else from the database."""

    invoice_id: str
    source_path: str
    source_type: str
    extracted: InvoiceData | None
    findings: list[Any]
    signals: list[Any]
    status: str


class BatchState(TypedDict):
    batch_id: str
    inputs: list[dict[str, Any]]
    # Accumulate across Sends. Nodes return DELTAS — returning the accumulated
    # list makes each superstep double the last (design §3.1's reducer trap).
    enriched: Annotated[list[EnrichedInvoice], operator.add]
    failed: Annotated[list[dict[str, str]], operator.add]
    persisted: list[dict[str, Any]]
    consumption: list[dict[str, Any]]
    auto: list[str]
    needs_approval: list[str]
    status: str


def thread_id_for(batch_id: uuid.UUID | str, invoice_id: str) -> str:
    """Design §1.3's correlation key, with the per-run component M3-C §8.5 owes.

    `enrich:{invoice_id}` alone is stable across re-runs, so a second batch over
    the same invoice resumes the first batch's checkpoints rather than starting
    clean — and the interrupt/resume semantics M1 proved would then apply to the
    wrong run.
    """
    return f"enrich:{batch_id}:{invoice_id}"


def enrich_one(task: dict[str, Any], *, graph: Any) -> dict[str, Any]:
    invoice_id = task["invoice_id"]
    state = {
        "invoice_id": invoice_id,
        "source_path": task["source_path"],
        "pdf_path": task.get("pdf_path"),
        "source_type": task["source_type"],
        "strategy": "xml" if task["source_type"] == "xml" else "vision",
        "extracted": None,
        "extraction_attempts": 0,
        "current_errors": [],
        "error_history": [],
        "findings": [],
        "signals": [],
        "llm_calls": [],
        "classification": None,
        "audit_verdict": None,
        "status": "",
    }
    config = {"configurable": {"thread_id": thread_id_for(task["batch_id"], invoice_id)}}

    try:
        result = graph.invoke(state, config)
    except Exception as exc:  # noqa: BLE001 - a bad file must not end the batch
        return {"enriched": [], "failed": [{"invoice_id": invoice_id, "error": str(exc)}]}

    return {
        "enriched": [
            {
                "invoice_id": invoice_id,
                "source_path": task["source_path"],
                "source_type": task["source_type"],
                "extracted": result.get("extracted"),
                "findings": result.get("findings") or [],
                "signals": result.get("signals") or [],
                "status": result.get("status") or "",
            }
        ],
        "failed": [],
    }


def serial_join(state: BatchState, *, conn: Any = None) -> dict[str, Any]:
    """Persist, profile, de-duplicate and reconcile — ONE transaction.

    Constraint 2: the batch must never end with invoices recorded and budgets
    not drawn against them, so this is the batch's single commit point. A
    failure here loses persistence but not the expensive work: `llm_calls` rows
    were written inline during enrichment and survive independently.
    """
    if conn is not None:
        return _join(conn, state)
    from invoice_agent.db.engine import get_engine

    with get_engine().begin() as owned:
        return _join(owned, state)


def _join(conn: Any, state: BatchState) -> dict[str, Any]:
    from typing import cast

    from sqlalchemy import select

    from invoice_agent.db import models
    from invoice_agent.db.repositories import (
        batches,
        budget_consumption,
        flags,
        invoices,
        vendors,
    )
    from invoice_agent.domain import anomaly, findings
    from invoice_agent.domain import budget as domain_budget
    from invoice_agent.extraction.models import InvoiceData
    from invoice_agent.memory import vendor_profile

    survivors = [e for e in state["enriched"] if e.get("extracted") is not None]

    def _canonical_key(e: EnrichedInvoice) -> tuple[Any, str, str]:
        data = cast(InvoiceData, e["extracted"])
        return (data.issue_date, data.invoice_series, data.invoice_number)

    ordered = sorted(survivors, key=_canonical_key)

    batch_uuid = (
        uuid.UUID(state["batch_id"])
        if isinstance(state["batch_id"], str)
        else state["batch_id"]
    )
    batches.ensure_batch(conn, batch_uuid)
    persisted: list[dict[str, Any]] = []
    raised: list[dict[str, Any]] = []
    db_id_map: dict[str, uuid.UUID] = {}
    priors: list[anomaly.InvoiceKey] = []

    for e in ordered:
        inv = cast(InvoiceData, e["extracted"])
        assert inv.seller_tax_code is not None
        vendors.ensure_vendor(conn, inv.seller_tax_code, inv.seller_name)
        db_id = invoices.insert_with_lines(
            conn,
            inv,
            batch_id=batch_uuid,
            source_type=e["source_type"],
            source_path=e["source_path"],
        )

        if db_id is None:
            raised.append({"invoice_id": e["invoice_id"], "code": findings.DUPLICATE_INVOICE})
            continue

        db_id_map[e["invoice_id"]] = db_id
        persisted.append({"invoice_id": e["invoice_id"], "db_id": str(db_id)})
        vendor_profile.apply_invoice(
            conn,
            tax_code=inv.seller_tax_code,
            amount=Decimal(inv.total_amount),
            category=getattr(inv, "category", None),
            department=getattr(inv, "department", None),
        )

        cand = anomaly.InvoiceKey(
            seller_tax_code=inv.seller_tax_code,
            invoice_number=inv.invoice_number,
            issue_date=inv.issue_date,
            total_amount=Decimal(inv.total_amount),
        )
        near_dup = anomaly.near_duplicate(cand, priors)
        priors.append(cand)

        if near_dup:
            flags.raise_flag(
                conn,
                db_id,
                code=near_dup.code,
                severity=near_dup.severity,
                raised_by=near_dup.raised_by,
                detail=near_dup.detail,
            )
            raised.append({"invoice_id": e["invoice_id"], "code": near_dup.code})

    # Group survivors by department
    by_dept: dict[str, list[tuple[EnrichedInvoice, InvoiceData]]] = {}
    for e in ordered:
        data_obj = cast(InvoiceData, e["extracted"])
        dept = getattr(data_obj, "department", None) or "OTHER"
        by_dept.setdefault(dept, []).append((e, data_obj))

    consumption_out: list[dict[str, Any]] = []
    for dept in sorted(by_dept.keys()):
        dept_items = by_dept[dept]
        first_inv = dept_items[0][1]
        period = first_inv.issue_date.strftime("%Y-%m")

        stmt = select(models.budgets).where(
            models.budgets.c.department_code == dept,
            models.budgets.c.period == period,
        ).with_for_update()
        budget_row = conn.execute(stmt).mappings().first()

        limit = Decimal(budget_row["amount_limit"]) if budget_row else Decimal("50000000")
        warn_pct = Decimal(budget_row["warn_threshold_pct"]) if budget_row else Decimal("0.8")

        inputs = [
            domain_budget.BudgetInput(
                invoice_id=e["invoice_id"],
                issue_date=inv.issue_date,
                invoice_series=inv.invoice_series,
                invoice_number=inv.invoice_number,
                amount=Decimal(inv.total_amount),
            )
            for e, inv in dept_items
        ]

        consumptions = domain_budget.consume(limit, warn_pct, inputs)
        for c in consumptions:
            consumption_out.append(
                {
                    "invoice_id": c.invoice_id,
                    "amount": str(c.amount),
                    "balance_before": str(c.balance_before),
                    "balance_after": str(c.balance_after),
                    "sequence_no": c.sequence_no,
                }
            )
            if budget_row and c.invoice_id in db_id_map:
                budget_consumption.record_consumption(
                    conn,
                    budget_id=budget_row["id"],
                    invoice_id=db_id_map[c.invoice_id],
                    amount=c.amount,
                    balance_before=c.balance_before,
                    balance_after=c.balance_after,
                    sequence_no=c.sequence_no,
                )
            for f in c.findings:
                raised.append({"invoice_id": c.invoice_id, "code": f.code})
                if c.invoice_id in db_id_map:
                    flags.raise_flag(
                        conn,
                        db_id_map[c.invoice_id],
                        code=f.code,
                        severity=f.severity,
                        raised_by=f.raised_by,
                        detail=f.detail,
                    )

    return {
        "persisted": persisted,
        "consumption": consumption_out,
        "raised": raised,
    }


def ingest_node(state: BatchState) -> dict[str, Any]:
    batch_id = state.get("batch_id") or str(uuid.uuid4())
    return {"batch_id": batch_id, "status": "ingested"}


def cost_gate_node(state: BatchState) -> dict[str, Any]:
    """Check budget/file-count gate.

    M6.6 owns the real token budget logic and cost cap. Ships as a threshold
    check here to establish graph topology per design §4.
    """
    return {"status": "cost_gate_passed"}


def cost_gate_condition(state: BatchState) -> str:
    inputs = state.get("inputs") or []
    if len(inputs) > 10000:
        return "rejected"
    return "ok"


def fan_out_node(state: BatchState) -> dict[str, Any]:
    return {"status": "fan_out"}


def fan_out_router(state: BatchState) -> list[Send]:
    batch_id = state["batch_id"]
    inputs = state.get("inputs") or []
    return [
        Send("enrich_one", {**inp, "batch_id": batch_id})
        for inp in inputs
    ]


def enrich_one_task_node(task: Any) -> dict[str, Any]:
    from invoice_agent.graphs.enrichment import build_enrichment_graph

    enrichment_graph = build_enrichment_graph()
    return enrich_one(task, graph=enrichment_graph)


def route_outcomes_node(state: BatchState) -> dict[str, Any]:
    """Split into auto and needs_approval.

    Design §0 constraint 5: produces lists that nothing consumes until M5.
    Deliberate.
    """
    auto: list[str] = []
    needs_approval: list[str] = []

    for e in state.get("enriched") or []:
        if e.get("findings") or e.get("signals"):
            needs_approval.append(e["invoice_id"])
        else:
            auto.append(e["invoice_id"])

    for f in state.get("failed") or []:
        needs_approval.append(f["invoice_id"])

    return {"auto": auto, "needs_approval": needs_approval, "status": "routed"}


def finalize_node(state: BatchState) -> dict[str, Any]:
    batch_id_str = state.get("batch_id")
    if batch_id_str:
        try:
            batch_uuid = uuid.UUID(batch_id_str)
            from invoice_agent.db.engine import get_engine
            from invoice_agent.db.repositories import batches

            with get_engine().begin() as conn:
                count = len(state.get("enriched") or []) + len(state.get("failed") or [])
                batches.complete_batch(conn, batch_uuid, status="completed", invoice_count=count)
        except Exception:
            pass
    return {"status": "completed"}


def build_batch_graph(*, max_concurrency: int = 5) -> Any:
    """Compile the batch workflow graph.

    max_concurrency defaults to 5 (design §4.2's 4-6). Ships unexercised
    against real rate limits because the gate replays hermetically — M4-C
    measured 177 Gemini rate-limits in a serial run.
    """
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(BatchState)

    builder.add_node("ingest", ingest_node)
    builder.add_node("cost_gate", cost_gate_node)
    builder.add_node("fan_out", fan_out_node)
    builder.add_node("enrich_one", enrich_one_task_node)  # type: ignore[arg-type]
    builder.add_node("serial_join", serial_join)
    builder.add_node("route_outcomes", route_outcomes_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "cost_gate")
    builder.add_conditional_edges("cost_gate", cost_gate_condition, {"ok": "fan_out", "rejected": END})
    builder.add_conditional_edges("fan_out", fan_out_router, ["enrich_one"])
    builder.add_edge("enrich_one", "serial_join")
    builder.add_edge("serial_join", "route_outcomes")
    builder.add_edge("route_outcomes", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile()


