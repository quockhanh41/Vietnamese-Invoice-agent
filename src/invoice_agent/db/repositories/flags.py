"""Flag persistence.

Note what is NOT here: any way for a caller to declare itself an LLM. The
`raised_by` CHECK enumerates deterministic producers only (models.FLAG_SOURCES),
so the safety property is enforced by the database rather than by discipline.
"""

import uuid
from typing import Any

from sqlalchemy import Connection, func, select

from invoice_agent.db import models


def raise_flag(
    conn: Connection,
    invoice_id: uuid.UUID,
    *,
    code: str,
    severity: str,
    raised_by: str,
    detail: dict[str, Any] | None = None,
) -> int:
    stmt = (
        models.invoice_flags.insert()
        .values(
            invoice_id=invoice_id,
            code=code,
            severity=severity,
            raised_by=raised_by,
            detail=detail or {},
        )
        .returning(models.invoice_flags.c.id)
    )
    return int(conn.execute(stmt).scalar_one())


def open_flags_for(conn: Connection, invoice_id: uuid.UUID) -> list[dict[str, Any]]:
    stmt = select(models.invoice_flags).where(
        models.invoice_flags.c.invoice_id == invoice_id,
        models.invoice_flags.c.resolved_at.is_(None),
    )
    return [dict(r) for r in conn.execute(stmt).mappings()]


def resolve_flag(conn: Connection, flag_id: int) -> None:
    conn.execute(
        models.invoice_flags.update()
        .where(models.invoice_flags.c.id == flag_id)
        .values(resolved_at=func.now())
    )
