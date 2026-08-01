"""The reviewer queue and the decision endpoint.

THIS ROUTE ADDS NO LOGIC. `POST /approvals/{invoice_id}/decision` calls
`approval.submit_decision` and returns what it returns. That is what makes
"interrupt is transport-agnostic" (M7's artefact) a demonstration rather than a
claim — a test, a script, this route and later MCP all drive the same function.

It must never import `tools.guards` or `db.repositories.journal`:
`tests/test_api_boundary.py` enforces it. A route that imported the guard could
choose its own `posting_authority`, and the HTTP layer would become a second
door to the ledger.
"""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from invoice_agent.api.schemas import DecisionRequest
from invoice_agent.db.engine import get_engine
from invoice_agent.db.repositories import approvals as approvals_repo
from invoice_agent.graphs import approval

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("")
def pending() -> list[dict[str, Any]]:
    with get_engine().begin() as conn:
        rows = approvals_repo.list_pending(conn)
    return [
        {
            "invoice_id": str(r["invoice_id"]),
            "approval_id": str(r["id"]),
            "thread_id": r["thread_id"],
            "requested_at": r["requested_at"].isoformat(),
        }
        for r in rows
    ]


@router.get("/{invoice_id}")
def detail(invoice_id: uuid.UUID) -> dict[str, Any]:
    with get_engine().begin() as conn:
        row = approvals_repo.get_by_invoice(conn, invoice_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no approval for invoice {invoice_id}")
    return {
        "invoice_id": str(row["invoice_id"]),
        "approval_id": str(row["id"]),
        "decision": row["decision"],
        # Vietnamese flag narratives pass through untranslated.
        "interrupt_payload": row["interrupt_payload"],
        "suggestions": row["suggestions"],
    }


@router.post("/{invoice_id}/decision")
def decide(invoice_id: uuid.UUID, body: DecisionRequest) -> dict[str, Any]:
    with get_engine().begin() as conn:
        row = approvals_repo.get_by_invoice(conn, invoice_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no approval for invoice {invoice_id}")
    if row["decision"] is not None:
        raise HTTPException(
            status_code=409,
            detail=f"invoice {invoice_id} was already {row['decision']}",
        )

    result = approval.submit_decision(
        invoice_id=str(invoice_id),
        decision=body.decision,
        decided_by=body.decided_by,
        reason=body.reason,
        corrections=body.corrections,
    )
    return {
        "invoice_id": str(invoice_id),
        "decision": body.decision,
        "status": result.get("status"),
        "entry_id": result.get("entry_id"),
    }
