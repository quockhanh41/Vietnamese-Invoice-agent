"""The code layer of the three-layer safety property (design doc §2.7, §5.3).

THE ONLY PRODUCTION DOOR TO `journal_entries`. `repositories/journal.py` is
reachable from here and nowhere else — `test_no_production_caller_of_journal_repository`
enforces the allowlist.

Two rules carry this module, and both exist because state is not evidence:

  * `human_approved` re-reads `approvals` FROM POSTGRES. Design §5.3 step 2:
    graph state can be forged through a time-travel fork or a replay, so
    `decision == "approved"` arriving in an argument is an input, not proof.
  * `auto_clean` re-reads `invoice_flags` and refuses on any open `warn` or
    `critical` row — the same predicate migration 13 gave the trigger, so the
    code layer is never MORE PERMISSIVE than the layer beneath it.
"""

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import Connection

from invoice_agent.db import audit
from invoice_agent.db.repositories import approvals, flags, invoices, journal
from invoice_agent.domain import accounting

# Decisions that authorise a post. `edited` is here deliberately: a reviewer who
# corrected a field and let the invoice through has approved it (spec §4.1).
POSTING_DECISIONS = ("approved", "edited")

# The SAME predicate migration 13 gave the trigger. Two layers, one rule: an
# open `warn` or `critical` flag blocks auto_clean, `info` does not. Task 7's
# status write reads this tuple too, so the three places cannot drift apart
# without the mutation in the closeout note going red.
BLOCKING_SEVERITIES = ("warn", "critical")

ACTION = "tool.create_journal_entry"


class GuardDenied(Exception):
    """A write the guard refused. Always paired with an `audit_log` row."""


def _audit(
    *, actor: str, allowed: bool, invoice_id: uuid.UUID, reason: str, **detail: Any
) -> None:
    """Write the audit row on its OWN connection, committed independently.

    The guard runs inside the caller's transaction. A denial raises, the caller
    rolls back, and an audit row written on that same connection would vanish
    with it — design §5.3's "every refusal writes audit_log" would be silently
    false, and a test asserting the row exists would pass only because it never
    committed either. A log must not be rolled back by the thing it logs.

    Safe because `audit_log` is append-only and holds no FK to `invoices`.
    """
    from invoice_agent.db.engine import get_engine

    with get_engine().begin() as own:
        audit.record(
            own,
            actor=actor,
            action=f"{ACTION}.{'granted' if allowed else 'denied'}",
            allowed=allowed,
            target_table="journal_entries",
            target_id=str(invoice_id),
            reason=reason,
            detail=detail,
        )


def _deny(*, actor: str, invoice_id: uuid.UUID, reason: str, **detail: Any) -> GuardDenied:
    _audit(actor=actor, allowed=False, invoice_id=invoice_id, reason=reason, **detail)
    return GuardDenied(f"{invoice_id}: {reason}")


def create_journal_entry(
    conn: Connection,
    *,
    invoice_id: uuid.UUID,
    posting_authority: str,
    entry_date: date,
    lines: Sequence[accounting.JournalLine],
    actor: str,
    approval_id: uuid.UUID | None = None,
) -> uuid.UUID:
    if posting_authority not in ("auto_clean", "human_approved"):
        raise _deny(
            actor=actor,
            invoice_id=invoice_id,
            reason=f"unknown posting_authority {posting_authority!r}",
        )

    if posting_authority == "human_approved":
        if approval_id is None:
            raise _deny(
                actor=actor,
                invoice_id=invoice_id,
                reason="human_approved requires an approval_id",
            )
        approval = approvals.get_approval(conn, approval_id)
        if approval is None:
            raise _deny(
                actor=actor,
                invoice_id=invoice_id,
                reason=f"approval {approval_id} does not exist",
            )
        if approval["invoice_id"] != invoice_id:
            raise _deny(
                actor=actor,
                invoice_id=invoice_id,
                reason="approval belongs to a different invoice",
                approval_invoice_id=str(approval["invoice_id"]),
            )
        if approval["decision"] is None:
            raise _deny(
                actor=actor, invoice_id=invoice_id, reason="approval is not decided"
            )
        if approval["decision"] not in POSTING_DECISIONS:
            raise _deny(
                actor=actor,
                invoice_id=invoice_id,
                reason=f"approval was {approval['decision']}",
            )
    else:  # auto_clean
        if approval_id is not None:
            raise _deny(
                actor=actor,
                invoice_id=invoice_id,
                reason="auto_clean must not carry an approval_id",
            )
        blocking = [
            f
            for f in flags.open_flags_for(conn, invoice_id)
            if f["severity"] in BLOCKING_SEVERITIES
        ]
        if blocking:
            raise _deny(
                actor=actor,
                invoice_id=invoice_id,
                reason="invoice has an open flag",
                codes=sorted({f["code"] for f in blocking}),
            )

    entry_id = journal.create_entry_with_lines(
        conn,
        invoice_id=invoice_id,
        entry_date=entry_date,
        posting_authority=posting_authority,
        approval_id=approval_id,
        posted_by=actor,
        status="posted",
        lines=[
            journal.JournalLine(
                account_code=line.account_code,
                account_name=line.account_name,
                debit=line.debit,
                credit=line.credit,
                description=line.description,
            )
            for line in lines
        ],
    )
    invoices.set_status(conn, invoice_id, "posted")
    _audit(
        actor=actor,
        allowed=True,
        invoice_id=invoice_id,
        reason="posted",
        entry_id=str(entry_id),
        posting_authority=posting_authority,
    )
    return entry_id
