"""Aggregates, straight from `db/queries.py`. NO LLM anywhere.

M8's ReportingAgent writes Vietnamese commentary ON TOP of these numbers rather
than recomputing them, so the narrative and the dashboard cannot disagree.
"""

from typing import Any

from fastapi import APIRouter

from invoice_agent.agents import reporting
from invoice_agent.db import queries
from invoice_agent.db.engine import get_engine
from invoice_agent.llm.gateway import LLMGateway  # noqa: F401

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

    Per ADR 0007's verdict (eval N=24: LLM fidelity 0.8333 < 0.90 and coverage
    0.5000 <= template 1.0000), `render_template` is the default baseline.
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
    comm = reporting.render_template(facts)
    return {
        "period": period,
        "source": "template",
        "summary": comm.summary,
        "observations": comm.observations,
        "facts": reporting.as_payload(facts),
    }


