"""Invoice reads. No SQL here — `db/queries.py` and the repositories own it."""

import datetime
import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from invoice_agent.db import queries
from invoice_agent.db.engine import get_engine
from invoice_agent.db.repositories import flags
from invoice_agent.db.repositories import invoices as invoices_repo

router = APIRouter(prefix="/invoices", tags=["invoices"])


def _jsonable(value: Any) -> Any:
    """Decimal, date and UUID to strings at the boundary.

    Money is `Decimal` by a decision made at M1 — a real fixture carries
    21618409.09000000, and JSON numbers are IEEE 754 doubles, so serialising as
    a number loses the last đồng.
    """
    if isinstance(value, Decimal | uuid.UUID):
        return str(value)
    if isinstance(value, datetime.date | datetime.datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


@router.get("")
def list_invoices(
    status: str | None = None,
    department: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    with get_engine().begin() as conn:
        return _jsonable(
            queries.list_invoices(conn, status=status, department=department, limit=limit)
        )


@router.get("/{invoice_id}")
def get_invoice(invoice_id: uuid.UUID) -> dict[str, Any]:
    with get_engine().begin() as conn:
        row = invoices_repo.get_by_id(conn, invoice_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"invoice {invoice_id} not found")
        lines = invoices_repo.get_lines(conn, invoice_id)
        open_flags = flags.open_flags_for(conn, invoice_id)
    return _jsonable(
        {
            "invoice": {k: v for k, v in row.items() if k != "raw_xml"},
            "lines": [dict(x) for x in lines],
            # Vietnamese narratives pass through untranslated — an accountant
            # reads them (CLAUDE.md's product-output rule).
            "flags": [dict(f) for f in open_flags],
        }
    )
