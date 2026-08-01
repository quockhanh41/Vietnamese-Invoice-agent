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
from typing import Annotated, Any, TypedDict

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
