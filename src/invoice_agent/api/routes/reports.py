"""Aggregates, straight from `db/queries.py`. NO LLM anywhere.

M8's ReportingAgent writes Vietnamese commentary ON TOP of these numbers rather
than recomputing them, so the narrative and the dashboard cannot disagree.
"""

from typing import Any

from fastapi import APIRouter

from invoice_agent.agents import reporting
from invoice_agent.db import queries
from invoice_agent.db.engine import get_engine
from invoice_agent.llm.gateway import LLMGateway

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


@router.get("/commentary")
def commentary(period: str) -> dict[str, Any]:
    """Vietnamese narrative over this period's aggregates.

    `source` tells the reader whether a model or the template wrote it — the
    model's commentary is only used when every number in it is one this
    endpoint's own `facts` payload contains (`agents.reporting.narrate`).
    """
    with get_engine().begin() as conn:
        facts = reporting.build_facts(
            period=period,
            budgets=queries.budget_report(conn, period=period),
            flags=queries.flag_report(conn),
            posting=queries.posting_report(conn),
            cost=queries.cost_report(conn),
            spend=queries.spend_summary(conn, period=period, group_by="department"),
        )
    narration = reporting.narrate(facts, gateway=LLMGateway())
    return {
        "period": period,
        "source": narration.source,
        "summary": narration.commentary.summary,
        "observations": narration.commentary.observations,
        "facts": reporting.as_payload(facts),
    }

