"""Seed reference data — item 2.5.

`keywords_vi` is not decoration: it is the non-LLM baseline every
classification metric must be reported against (principle 3, design doc
§10.4). It stays Vietnamese because it is data the system ingests.
"""

from decimal import Decimal

import pytest
from scripts.seed_reference import seed
from sqlalchemy import select

from invoice_agent.db import models
from invoice_agent.db.engine import get_owner_engine

# TT133/2016/TT-BTC, not TT200: the scenario is a 40-person trading company,
# i.e. an SME (design doc §2.1).
TT133_ACCOUNTS = {"6421", "6422", "1331", "331", "156", "153"}


@pytest.fixture
def seeded(requires_postgres):
    engine = get_owner_engine()
    with engine.begin() as conn:
        seed(conn)
    return engine


def test_seed_uses_real_tt133_account_numbers(seeded):
    with seeded.connect() as conn:
        rows = conn.execute(
            select(
                models.expense_categories.c.debit_account,
                models.expense_categories.c.vat_account,
                models.expense_categories.c.credit_account,
            )
        ).all()
    used = {a for row in rows for a in row if a is not None}
    assert used <= TT133_ACCOUNTS, f"non-TT133 account numbers seeded: {used - TT133_ACCOUNTS}"
    assert {"6421", "6422", "1331", "331"} <= used


def test_every_category_has_a_non_empty_keywords_baseline(seeded):
    with seeded.connect() as conn:
        rows = conn.execute(
            select(models.expense_categories.c.code, models.expense_categories.c.keywords_vi)
        ).all()
    assert rows, "no categories seeded"
    empty = [code for code, kw in rows if not kw]
    assert not empty, f"categories with no keywords_vi baseline: {empty}"


def test_seed_is_idempotent(seeded):
    """`make seed` must be safe to re-run — it is wired into `make dev`."""
    stmt = select(models.expense_categories).order_by(models.expense_categories.c.code)
    with seeded.connect() as conn:
        before = conn.execute(stmt).all()
    with seeded.begin() as conn:
        seed(conn)
    with seeded.connect() as conn:
        after = conn.execute(stmt).all()
    assert before == after



def test_two_department_wide_budgets_rejected(seeded):
    """MUTATION GUARD (spec §8.2).

    `category IS NULL` means whole-department budget. Postgres treats NULLs as
    distinct, so a plain UNIQUE admits two such rows and the department silently
    gets two budgets. Verified red 2026-07-28 by dropping
    `postgresql_nulls_not_distinct=True`.
    """
    from sqlalchemy.exc import IntegrityError

    from invoice_agent.db.repositories import reference

    with seeded.begin() as conn:
        reference.upsert_department(conn, "ZZTEST", "Phòng thử nghiệm")
    with pytest.raises(IntegrityError), seeded.begin() as conn:
        conn.execute(
            models.budgets.insert(),
            [
                {
                    "department_code": "ZZTEST",
                    "period": "2026-07",
                    "category": None,
                    "amount_limit": 1000,
                    "warn_threshold_pct": Decimal("0.8"),
                },
                {
                    "department_code": "ZZTEST",
                    "period": "2026-07",
                    "category": None,
                    "amount_limit": 2000,
                    "warn_threshold_pct": Decimal("0.8"),
                },
            ],
        )
