"""Batch bookkeeping. `batch_id` is referenced by design doc §2.3 but the table
is never defined there, so it is defined minimally here: enough to answer "which
run produced this invoice" and nothing speculative."""

import uuid
from typing import Any

from sqlalchemy import Connection, func, select

from invoice_agent.db import models


def create_batch(conn: Connection, label: str | None = None) -> uuid.UUID:
    batch_id = uuid.uuid4()
    conn.execute(models.batches.insert().values(id=batch_id, label=label, status="running"))
    return batch_id


def ensure_batch(conn: Connection, batch_id: uuid.UUID, label: str | None = None) -> None:
    from sqlalchemy.dialects.postgresql import insert

    stmt = (
        insert(models.batches)
        .values(id=batch_id, label=label, status="running")
        .on_conflict_do_nothing(index_elements=["id"])
    )
    conn.execute(stmt)


def complete_batch(
    conn: Connection, batch_id: uuid.UUID, *, status: str, invoice_count: int
) -> None:
    conn.execute(
        models.batches.update()
        .where(models.batches.c.id == batch_id)
        .values(status=status, invoice_count=invoice_count, completed_at=func.now())
    )


def get_batch(conn: Connection, batch_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        conn.execute(select(models.batches).where(models.batches.c.id == batch_id))
        .mappings()
        .first()
    )
    return dict(row) if row else None
