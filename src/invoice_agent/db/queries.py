"""Read-only list and aggregate queries — everything the HTTP API reads.

WHY THIS MODULE EXISTS. Half of M5-C's routes had nothing to wrap: the
repositories carry `get_by_id`, `get_by_natural_key`, `get_lines` and
`set_status`, but no list, no filter and no counts. The alternative was SQL
inside route modules, which makes "the API adds no logic" false on the first
commit and strands aggregate SQL where no non-HTTP caller can reuse it —
M5-D's Streamlit and M8's ReportingAgent both want these same numbers.

READ-ONLY IS A RULE, NOT A DESCRIPTION. Nothing here writes.
`tests/test_api_boundary.py::test_queries_module_is_read_only` enforces it, so
this cannot grow into a second write path beside the repositories.
"""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, func, select

from invoice_agent.db import models


def list_invoices(
    conn: Connection,
    *,
    status: str | None = None,
    department: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    inv = models.invoices
    stmt = select(
        inv.c.id, inv.c.invoice_series, inv.c.invoice_number, inv.c.seller_tax_code,
        inv.c.issue_date, inv.c.total_amount, inv.c.category, inv.c.department,
        inv.c.status, inv.c.batch_id,
    )
    if status is not None:
        stmt = stmt.where(inv.c.status == status)
    if department is not None:
        stmt = stmt.where(inv.c.department == department)
    stmt = stmt.order_by(inv.c.created_at.desc()).limit(limit)
    return [dict(r) for r in conn.execute(stmt).mappings()]


def batch_summary(conn: Connection, batch_id: uuid.UUID) -> dict[str, Any] | None:
    """Every number is a COUNT against a table, never the progress registry.

    Spec §2.4: the registry is in process memory and is expected to be lost.
    This answers correctly whether or not anyone was ever streaming.
    """
    row = (
        conn.execute(select(models.batches).where(models.batches.c.id == batch_id))
        .mappings()
        .first()
    )
    if row is None:
        return None

    inv = models.invoices

    def _count(*where: Any) -> int:
        return int(
            conn.execute(select(func.count()).select_from(inv).where(*where)).scalar_one()
        )

    pending = int(
        conn.execute(
            select(func.count())
            .select_from(models.approvals.join(inv, models.approvals.c.invoice_id == inv.c.id))
            .where(inv.c.batch_id == batch_id, models.approvals.c.decision.is_(None))
        ).scalar_one()
    )
    return {
        "batch_id": str(batch_id),
        "status": row["status"],
        "invoice_count": row["invoice_count"],
        "persisted": _count(inv.c.batch_id == batch_id),
        "posted": _count(inv.c.batch_id == batch_id, inv.c.status == "posted"),
        "flagged": _count(inv.c.batch_id == batch_id, inv.c.status == "flagged"),
        "pending_approval": pending,
    }


def budget_report(conn: Connection, *, period: str | None = None) -> list[dict[str, Any]]:
    b, bc = models.budgets, models.budget_consumption
    consumed = (
        select(bc.c.budget_id, func.coalesce(func.sum(bc.c.amount), 0).label("consumed"))
        .group_by(bc.c.budget_id)
        .subquery()
    )
    stmt = (
        select(
            b.c.department_code, b.c.period, b.c.category, b.c.amount_limit,
            b.c.warn_threshold_pct,
            func.coalesce(consumed.c.consumed, 0).label("consumed"),
        )
        .select_from(b.outerjoin(consumed, consumed.c.budget_id == b.c.id))
        .order_by(b.c.department_code, b.c.period)
    )
    if period is not None:
        stmt = stmt.where(b.c.period == period)
    rows = [dict(r) for r in conn.execute(stmt).mappings()]
    for r in rows:
        # Stringified because Decimal is not JSON-serialisable and this goes
        # straight out over HTTP. Converting at the boundary, as the approval
        # graph's `_jsonable` does, means one place to look.
        # Money rule: JSON numbers are IEEE 754 doubles; this system decided at M1
        # that money is Decimal — 21618409.09000000 appears in a real fixture,
        # and a float loses the last đồng.
        amount_limit = Decimal(str(r["amount_limit"]))
        warn_pct = Decimal(str(r["warn_threshold_pct"]))
        consumed_amt = Decimal(str(r["consumed"]))
        r["amount_limit"] = str(amount_limit)
        r["warn_threshold_pct"] = str(warn_pct)
        r["consumed"] = str(consumed_amt)
        r["remaining"] = str(amount_limit - consumed_amt)
    return rows


def flag_report(conn: Connection) -> list[dict[str, Any]]:
    f = models.invoice_flags
    stmt = (
        select(
            f.c.code, f.c.severity,
            func.count().label("total"),
            func.count().filter(f.c.resolved_at.is_(None)).label("open_count"),
        )
        .group_by(f.c.code, f.c.severity)
        .order_by(func.count().desc())
    )
    return [dict(r) for r in conn.execute(stmt).mappings()]


def posting_report(conn: Connection) -> list[dict[str, Any]]:
    j = models.journal_entries
    stmt = (
        select(j.c.posting_authority, func.count().label("entries"))
        .group_by(j.c.posting_authority)
        .order_by(j.c.posting_authority)
    )
    return [dict(r) for r in conn.execute(stmt).mappings()]


def cost_report(conn: Connection) -> dict[str, Any]:
    """Real AND shadow. On Groq free tier `cost_usd` is $0, which reads as
    "you did not measure" rather than "it is cheap" — design §2.9."""
    c = models.llm_calls
    row = conn.execute(
        select(
            func.count().label("calls"),
            func.count().filter(c.c.succeeded).label("succeeded"),
            func.coalesce(func.sum(c.c.cost_usd), 0).label("cost_usd"),
            func.coalesce(func.sum(c.c.shadow_cost_usd), 0).label("shadow_cost_usd"),
        ).select_from(c)
    ).mappings().one()
    return {
        "calls": int(row["calls"]),
        "succeeded": int(row["succeeded"]),
        "cost_usd": str(row["cost_usd"]),
        "shadow_cost_usd": str(row["shadow_cost_usd"]),
    }
