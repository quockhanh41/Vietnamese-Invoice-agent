"""Shared fixtures and setup helpers for approval graph tests."""

import pathlib
import uuid

import pytest


@pytest.fixture
def committed_rows(requires_postgres):
    """Delete rows these tests commit, in FK order."""
    created: list[uuid.UUID] = []
    yield created
    if not created:
        return
    from sqlalchemy import text

    from invoice_agent.db.engine import get_owner_engine

    with get_owner_engine().begin() as conn:
        tax_codes = [
            r[0]
            for r in conn.execute(
                text("SELECT DISTINCT seller_tax_code FROM app.invoices WHERE id = ANY(:ids)"),
                {"ids": created},
            )
        ]
        conn.execute(
            text("DELETE FROM app.journal_entries WHERE invoice_id = ANY(:ids)"), {"ids": created}
        )
        for table in ("budget_consumption", "episodic_decisions", "approvals"):
            conn.execute(
                text(f"DELETE FROM app.{table} WHERE invoice_id = ANY(:ids)"), {"ids": created}
            )
        conn.execute(text("DELETE FROM app.invoices WHERE id = ANY(:ids)"), {"ids": created})
        if tax_codes:
            conn.execute(
                text(
                    "UPDATE app.vendors SET invoice_count=0, total_amount=0, sum_sq_amount=0,"
                    " avg_amount=NULL, stddev_amount=NULL, rejection_count=0,"
                    " reviewer_notes=NULL WHERE tax_code = ANY(:codes)"
                ),
                {"codes": tax_codes},
            )


def _persisted_invoice(created, *, category="OFFICE_SUPPLIES", flag=None, batch_id: uuid.UUID | None = None):
    """Commit one invoice, optionally flagged, and open an approval for it.

    Returns (invoice_db_id, approval_id).
    """
    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.db.repositories import approvals as approvals_repo
    from invoice_agent.db.repositories import flags, invoices
    from invoice_agent.extraction.xml_parser import parse_invoice_xml

    data = parse_invoice_xml(
        pathlib.Path("tests/fixtures/real_samples/viettelpost_gtgt_2.1.0.xml").read_bytes()
    ).model_copy(update={"invoice_number": uuid.uuid4().hex[:8]})

    with get_owner_engine().begin() as conn:
        db_id = invoices.insert_with_lines(
            conn, data, source_type="xml", category=category, department="ADMIN",
            batch_id=batch_id,
        )
        assert db_id is not None
        created.append(db_id)
        if flag is not None:
            flags.raise_flag(
                conn, db_id, code=flag[0], severity=flag[1], raised_by=flag[2]
            )
        approval_id = approvals_repo.open_approval(
            conn,
            invoice_id=db_id,
            thread_id=f"appr:{db_id}",
            interrupt_payload={},
            flags_snapshot={},
            suggestions={},
        )
    return db_id, approval_id


def _initial_state(db_id, approval_id) -> dict:
    return {
        "invoice_id": str(db_id),
        "approval_id": str(approval_id),
        "snapshot": {},
        "flags": [],
        "suggestions": [],
        "decision": "",
        "decided_by": "",
        "reason": None,
        "corrections": None,
        "entry_id": None,
        "status": "",
    }
