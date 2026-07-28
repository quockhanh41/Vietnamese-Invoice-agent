"""Episodic memory (design doc §7.1).

Recall is SQL FIRST: same vendor OR same category, ordered by recency. pgvector
is M7 and optional.
"""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, or_, select

from invoice_agent.db import models


def record_episode(
    conn: Connection,
    *,
    invoice_id: uuid.UUID,
    vendor_tax_code: str,
    amount: Decimal,
    flags: dict[str, Any],
    decision: str,
    approval_id: uuid.UUID | None = None,
    category: str | None = None,
    department: str | None = None,
    reason: str | None = None,
) -> uuid.UUID:
    episode_id = uuid.uuid4()
    conn.execute(
        models.episodic_decisions.insert().values(
            id=episode_id,
            approval_id=approval_id,
            invoice_id=invoice_id,
            vendor_tax_code=vendor_tax_code,
            category=category,
            department=department,
            amount=amount,
            flags=flags,
            decision=decision,
            reason=reason,
        )
    )
    return episode_id


def similar_episodes(
    conn: Connection,
    *,
    vendor_tax_code: str,
    category: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    conds = [models.episodic_decisions.c.vendor_tax_code == vendor_tax_code]
    if category:
        conds.append(models.episodic_decisions.c.category == category)
    stmt = (
        select(models.episodic_decisions)
        .where(or_(*conds))
        .order_by(models.episodic_decisions.c.decided_at.desc())
        .limit(limit)
    )
    return [dict(r) for r in conn.execute(stmt).mappings()]
