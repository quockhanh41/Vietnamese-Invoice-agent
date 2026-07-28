"""Budget consumption ledger (design doc §2.6).

The `uq_budget_consumption_invoice` constraint prevents double-counting on
retry.
"""

import uuid
from decimal import Decimal

from sqlalchemy import Connection

from invoice_agent.db import models


def record_consumption(
    conn: Connection, *, budget_id: int, invoice_id: uuid.UUID,
    amount: Decimal, balance_before: Decimal, balance_after: Decimal,
    sequence_no: int,
) -> int:
    stmt = models.budget_consumption.insert().values(
        budget_id=budget_id, invoice_id=invoice_id, amount=amount,
        balance_before=balance_before, balance_after=balance_after,
        sequence_no=sequence_no,
    ).returning(models.budget_consumption.c.id)
    return int(conn.execute(stmt).scalar_one())
