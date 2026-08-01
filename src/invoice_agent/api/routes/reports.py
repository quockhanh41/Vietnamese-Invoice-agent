"""Aggregates, straight from `db/queries.py`. NO LLM anywhere.

M8's ReportingAgent writes Vietnamese commentary ON TOP of these numbers rather
than recomputing them, so the narrative and the dashboard cannot disagree.
"""

from typing import Any

from fastapi import APIRouter

from invoice_agent.db import queries
from invoice_agent.db.engine import get_engine

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/budgets")
def budgets(period: str | None = None) -> list[dict[str, Any]]:
    with get_engine().begin() as conn:
        return queries.budget_report(conn, period=period)


@router.get("/flags")
def flags() -> list[dict[str, Any]]:
    with get_engine().begin() as conn:
        return queries.flag_report(conn)


@router.get("/posting")
def posting() -> list[dict[str, Any]]:
    with get_engine().begin() as conn:
        return queries.posting_report(conn)


@router.get("/cost")
def cost() -> dict[str, Any]:
    with get_engine().begin() as conn:
        return queries.cost_report(conn)
