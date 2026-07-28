"""Journal entries — the accounting ledger (design doc §2.7).

⚠️ ORDERING RISK, stated rather than assumed (spec §6.1). This module can
create a journal entry, and its CODE-layer guard — `tools/guards.py`, which
asserts `decision == "approved"` and re-reads Postgres to confirm the approvals
row persisted — is item 5.3 and does not exist yet. Until it lands, the
protections are the DB constraints plus the fact that nothing calls this;
`test_no_production_caller_of_journal_repository` asserts the second one.

Do not add a production caller before M5.3.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, select

from invoice_agent.db import models


@dataclass(frozen=True)
class JournalLine:
    account_code: str
    account_name: str
    debit: Decimal
    credit: Decimal
    description: str | None = None


def create_entry_with_lines(
    conn: Connection,
    *,
    invoice_id: uuid.UUID,
    entry_date: date,
    posting_authority: str,
    lines: list[JournalLine],
    approval_id: uuid.UUID | None = None,
    posted_by: str | None = None,
    status: str = "draft",
) -> uuid.UUID:
    """Totals are SUMMED HERE, never passed in.

    A caller that supplied its own totals could satisfy
    `CHECK (total_debit = total_credit)` with numbers unrelated to the lines,
    which would turn the balance guard into decoration.
    """
    entry_id = uuid.uuid4()
    conn.execute(
        models.journal_entries.insert().values(
            id=entry_id,
            invoice_id=invoice_id,
            entry_date=entry_date,
            posting_authority=posting_authority,
            approval_id=approval_id,
            posted_by=posted_by,
            status=status,
            total_debit=sum((line.debit for line in lines), Decimal("0")),
            total_credit=sum((line.credit for line in lines), Decimal("0")),
        )
    )
    conn.execute(
        models.journal_entry_lines.insert(),
        [
            {
                "journal_entry_id": entry_id,
                "line_no": position,
                "account_code": line.account_code,
                "account_name": line.account_name,
                "debit": line.debit,
                "credit": line.credit,
                "description": line.description,
            }
            for position, line in enumerate(lines, start=1)
        ],
    )
    return entry_id


def get_for_invoice(conn: Connection, invoice_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        conn.execute(
            select(models.journal_entries).where(models.journal_entries.c.invoice_id == invoice_id)
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def get_lines(conn: Connection, entry_id: uuid.UUID) -> list[dict[str, Any]]:
    stmt = (
        select(models.journal_entry_lines)
        .where(models.journal_entry_lines.c.journal_entry_id == entry_id)
        .order_by(models.journal_entry_lines.c.line_no)
    )
    return [dict(r) for r in conn.execute(stmt).mappings()]
