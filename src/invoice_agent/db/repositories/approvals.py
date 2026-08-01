"""Approval records (design doc §2.8).

`list_pending` is what `scripts/recover_pending.py` (M5.5) scans after a
restart: the API must never assume an in-RAM registry survived.
"""

import uuid
from typing import Any

from sqlalchemy import Connection, func, select

from invoice_agent.db import models


def open_approval(
    conn: Connection,
    *,
    invoice_id: uuid.UUID,
    thread_id: str,
    interrupt_payload: dict[str, Any],
    flags_snapshot: dict[str, Any],
    suggestions: dict[str, Any],
) -> uuid.UUID:
    approval_id = uuid.uuid4()
    conn.execute(
        models.approvals.insert().values(
            id=approval_id,
            invoice_id=invoice_id,
            thread_id=thread_id,
            interrupt_payload=interrupt_payload,
            flags_snapshot=flags_snapshot,
            suggestions=suggestions,
        )
    )
    return approval_id


def record_decision(
    conn: Connection,
    approval_id: uuid.UUID,
    *,
    decision: str,
    decided_by: str,
    reason: str | None = None,
    field_corrections: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        models.approvals.update()
        .where(models.approvals.c.id == approval_id)
        .values(
            decision=decision,
            decided_by=decided_by,
            reason=reason,
            field_corrections=field_corrections,
            decided_at=func.clock_timestamp(),
        )
    )


def list_pending(conn: Connection) -> list[dict[str, Any]]:
    stmt = select(models.approvals).where(models.approvals.c.decision.is_(None))
    return [dict(r) for r in conn.execute(stmt).mappings()]


def get_approval(conn: Connection, approval_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        conn.execute(select(models.approvals).where(models.approvals.c.id == approval_id))
        .mappings()
        .first()
    )
    return dict(row) if row else None


def record_context(
    conn: Connection,
    approval_id: uuid.UUID,
    *,
    interrupt_payload: dict[str, Any],
    flags_snapshot: dict[str, Any],
    suggestions: dict[str, Any],
) -> None:
    """Fill the columns design §2.8 specifies, from the node BEFORE the gate.

    Not from the gate: `approval_gate` must contain `interrupt()` and nothing
    else, because M1.5 measured that node re-running from the top on resume.
    """
    conn.execute(
        models.approvals.update()
        .where(models.approvals.c.id == approval_id)
        .values(
            interrupt_payload=interrupt_payload,
            flags_snapshot=flags_snapshot,
            suggestions=suggestions,
        )
    )


def get_by_invoice(conn: Connection, invoice_id: uuid.UUID) -> dict[str, Any] | None:
    """The most recent approval for an invoice. `submit_decision` resolves a
    thread through this."""
    stmt = (
        select(models.approvals)
        .where(models.approvals.c.invoice_id == invoice_id)
        .order_by(models.approvals.c.requested_at.desc())
        .limit(1)
    )
    row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None

