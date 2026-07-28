"""Reference data access — departments, expense categories, budgets."""

from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, select
from sqlalchemy.dialects.postgresql import insert

from invoice_agent.db import models


def list_departments(conn: Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(select(models.departments)).mappings()]


def get_category(conn: Connection, code: str) -> dict[str, Any] | None:
    row = (
        conn.execute(
            select(models.expense_categories).where(models.expense_categories.c.code == code)
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def list_budgets(conn: Connection, department_code: str, period: str) -> list[dict[str, Any]]:
    stmt = select(models.budgets).where(
        models.budgets.c.department_code == department_code,
        models.budgets.c.period == period,
    )
    return [dict(r) for r in conn.execute(stmt).mappings()]


def upsert_department(conn: Connection, code: str, name_vi: str) -> None:
    stmt = insert(models.departments).values(code=code, name_vi=name_vi)
    conn.execute(stmt.on_conflict_do_update(index_elements=["code"], set_={"name_vi": name_vi}))


def upsert_category(
    conn: Connection,
    code: str,
    name_vi: str,
    debit_account: str,
    vat_account: str | None,
    credit_account: str,
    keywords_vi: list[str],
) -> None:
    values = {
        "code": code,
        "name_vi": name_vi,
        "debit_account": debit_account,
        "vat_account": vat_account,
        "credit_account": credit_account,
        "keywords_vi": keywords_vi,
    }
    stmt = insert(models.expense_categories).values(**values)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["code"], set_={k: v for k, v in values.items() if k != "code"}
        )
    )


def upsert_budget(
    conn: Connection,
    department_code: str,
    period: str,
    category: str | None,
    amount_limit: Decimal,
    warn_threshold_pct: Decimal = Decimal("0.8"),
) -> int:
    values = {
        "department_code": department_code,
        "period": period,
        "category": category,
        "amount_limit": amount_limit,
        "warn_threshold_pct": warn_threshold_pct,
    }
    stmt = (
        insert(models.budgets)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_budgets_department_period_category",
            set_={
                "amount_limit": amount_limit,
                "warn_threshold_pct": warn_threshold_pct,
            },
        )
        .returning(models.budgets.c.id)
    )
    return int(conn.execute(stmt).scalar_one())

