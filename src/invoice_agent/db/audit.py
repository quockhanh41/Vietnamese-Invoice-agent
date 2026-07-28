"""Append-only audit trail (design doc §2.8).

Append-only is enforced by TWO independent mechanisms, because measurement
2026-07-28 showed one is not enough:

1. PRIVILEGE — `invoice_app` holds only SELECT, INSERT. UPDATE and DELETE are
   revoked.
2. TRIGGER — a BEFORE UPDATE OR DELETE trigger that raises. This one also binds
   the table owner and any superuser, i.e. `invoice`, which the Docker image
   creates as a SUPERUSER. Privilege checks alone are bypassed by superusers.

There is deliberately no `update` or `delete` function in this module.
"""

from typing import Any

from sqlalchemy import Connection

from invoice_agent.db import models


def record(
    conn: Connection,
    *,
    actor: str,
    action: str,
    allowed: bool,
    target_table: str | None = None,
    target_id: str | None = None,
    reason: str | None = None,
    detail: dict[str, Any] | None = None,
) -> int:
    stmt = (
        models.audit_log.insert()
        .values(
            actor=actor,
            action=action,
            allowed=allowed,
            target_table=target_table,
            target_id=target_id,
            reason=reason,
            detail=detail or {},
        )
        .returning(models.audit_log.c.id)
    )
    return int(conn.execute(stmt).scalar_one())
